from __future__ import annotations

import asyncio

import pytest

from src.infra.sandbox.node_scheduler import OpenSandboxAllocationLease, OpenSandboxNodeScheduler
from src.infra.sandbox.node_storage import StoredOpenSandboxNodes
from src.kernel.schemas.opensandbox import OpenSandboxMode, OpenSandboxNodesUpdate


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _script: str, _keys: int, key: str, token: str, *_args: object) -> int:
        if self.values.get(key) != token:
            return 0
        if "del" in _script:
            del self.values[key]
            return 1
        return 1


class FakeNodes:
    async def get_current(self) -> StoredOpenSandboxNodes:
        return StoredOpenSandboxNodes(
            OpenSandboxMode.MULTI_NODE,
            [
                {"id": "busy", "domain": "http://busy", "max_sandboxes": 2, "priority": 1},
                {"id": "free", "domain": "http://free", "max_sandboxes": 2, "priority": 10},
            ],
            "r1",
        )


class FakeCapacity:
    def __init__(self) -> None:
        self.used = {"busy": 1, "free": 0}
        self.reserved: list[str] = []
        self.binding: dict | None = None

    async def ensure_node(self, node_id: str, *, max_sandboxes: int, enabled: bool = True) -> None:
        del max_sandboxes, enabled
        self.used.setdefault(node_id, 0)

    async def get_status(self, node_id: str) -> dict:
        return {"used_sandboxes": self.used[node_id]}

    async def reconcile_expired_creating(self, _node_id: str) -> int:
        return 0

    async def reconcile_provider_ttl(self, _node_id: str) -> int:
        return 0

    async def reconcile_expired_managed(self, _node: dict) -> int:
        return 0

    async def reserve(self, node_id: str, user_id: str, **_kwargs: object) -> dict:
        if self.used[node_id] >= 1:
            return None
        self.used[node_id] += 1
        self.reserved.append(node_id)
        return {"reservation_id": "r", "allocation_token": "t", "user_id": user_id}

    async def get_binding(self, _user_id: str) -> dict | None:
        return self.binding

    async def save_binding(self, user_id: str, *, allocation_token: str, **fields: object) -> bool:
        self.binding = {"user_id": user_id, "allocation_token": allocation_token, **fields}
        return True

    async def release(self, *_args: object, **_kwargs: object) -> bool:
        return True

    async def transition(self, *_args: object, **_kwargs: object) -> bool:
        return True


class SingleNode(FakeNodes):
    async def get_current(self) -> StoredOpenSandboxNodes:
        return StoredOpenSandboxNodes(
            OpenSandboxMode.MULTI_NODE,
            [{"id": "free", "domain": "http://free", "max_sandboxes": 2, "timeout": 10}],
            "r1",
        )


@pytest.mark.asyncio
async def test_scheduler_reserves_only_the_first_eligible_node() -> None:
    capacity = FakeCapacity()
    scheduler = OpenSandboxNodeScheduler(FakeNodes(), capacity, redis_client=FakeRedis())

    node, reservation = await scheduler.select_node("user-1")

    assert node["id"] == "free"
    assert reservation["allocation_token"] == "t"
    assert capacity.reserved == ["free"]


@pytest.mark.asyncio
async def test_full_node_retries_only_after_authoritative_reconciliation() -> None:
    class ReconciledCapacity(FakeCapacity):
        def __init__(self) -> None:
            super().__init__()
            self.used = {"busy": 2, "free": 2}
            self.reconciled: list[str] = []

        async def reserve(self, node_id: str, user_id: str, **_kwargs: object) -> dict | None:
            if self.used[node_id] >= 2:
                return None
            self.used[node_id] += 1
            self.reserved.append(node_id)
            return {"reservation_id": "r", "allocation_token": "t", "user_id": user_id}

        async def reconcile_expired_managed(self, node: dict) -> int:
            self.reconciled.append(node["id"])
            if node["id"] == "busy":
                self.used["busy"] = 1
                return 1
            return 0

    capacity = ReconciledCapacity()
    scheduler = OpenSandboxNodeScheduler(FakeNodes(), capacity, redis_client=FakeRedis())

    node, _reservation = await scheduler.select_node("user-1")

    assert node["id"] == "busy"
    assert capacity.reconciled == ["busy"]


@pytest.mark.asyncio
async def test_allocation_lease_release_is_token_safe() -> None:
    redis = FakeRedis()
    first = OpenSandboxAllocationLease("user-1", redis_client=redis)
    second = OpenSandboxAllocationLease("user-1", redis_client=redis)

    assert await first.acquire() is True
    assert await second.acquire() is False
    assert await second.release() is False
    assert await first.release() is True


@pytest.mark.asyncio
async def test_admission_keeps_renewable_lease_through_provisioning() -> None:
    redis = FakeRedis()
    capacity = FakeCapacity()
    scheduler = OpenSandboxNodeScheduler(
        SingleNode(), capacity, redis_client=redis, lease_ttl=1
    )
    observed: list[bool] = []

    async def provision(_binding: dict) -> None:
        observed.append(bool(redis.values))
        await asyncio.sleep(0.4)
        observed.append(bool(redis.values))
        assert capacity.binding is not None
        capacity.binding["sandbox_id"] = "sandbox-1"
        capacity.binding["allocation_state"] = "allocated"

    result = await scheduler.admit("user-1", provision=provision)

    assert result["sandbox_id"] == "sandbox-1"
    assert observed == [True, True]
    assert redis.values == {}


@pytest.mark.asyncio
async def test_creating_binding_without_sandbox_fails_closed() -> None:
    redis = FakeRedis()
    capacity = FakeCapacity()
    capacity.binding = {
        "user_id": "user-1",
        "allocation_token": "owner-token",
        "allocation_state": "creating",
        "sandbox_state": "creating",
        "node_id": "free",
        "reservation_id": "reservation-1",
    }
    scheduler = OpenSandboxNodeScheduler(
        SingleNode(), capacity, redis_client=redis
    )

    with pytest.raises(Exception) as exc_info:
        await scheduler.admit("user-1")

    assert exc_info.value.__class__.__name__ == "SandboxCapacityUnavailable"
    assert capacity.reserved == []
    assert redis.values == {}


@pytest.mark.asyncio
async def test_concurrent_replicas_provision_same_user_only_once() -> None:
    redis = FakeRedis()
    capacity = FakeCapacity()
    first = OpenSandboxNodeScheduler(
        SingleNode(), capacity, redis_client=redis, lease_ttl=2
    )
    second = OpenSandboxNodeScheduler(
        SingleNode(), capacity, redis_client=redis, lease_ttl=2
    )
    provisions: list[str] = []

    async def provision(binding: dict) -> None:
        provisions.append(str(binding["allocation_token"]))
        await asyncio.sleep(0.05)
        assert capacity.binding is not None
        capacity.binding.update(
            sandbox_id="sandbox-1",
            allocation_state="allocated",
            sandbox_state="running",
        )

    first_task = asyncio.create_task(first.admit("user-1", provision=provision))
    await asyncio.sleep(0)
    results = await asyncio.gather(
        first_task,
        second.admit("user-1", provision=provision),
        return_exceptions=True,
    )

    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(item.__class__.__name__ == "SandboxCapacityUnavailable" for item in results) == 1
    assert len(provisions) == 1
    assert capacity.reserved == ["free"]


@pytest.mark.asyncio
async def test_binding_cas_failure_releases_reserved_slot() -> None:
    class CasFailureCapacity(FakeCapacity):
        def __init__(self) -> None:
            super().__init__()
            self.releases: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def save_binding(
            self, user_id: str, *, allocation_token: str, **fields: object
        ) -> bool:
            del user_id, allocation_token, fields
            return False

        async def release(self, *args: object, **kwargs: object) -> bool:
            self.releases.append((args, kwargs))
            return True

    capacity = CasFailureCapacity()
    scheduler = OpenSandboxNodeScheduler(
        SingleNode(), capacity, redis_client=FakeRedis()
    )

    with pytest.raises(Exception) as exc_info:
        await scheduler.admit("user-1")

    assert exc_info.value.__class__.__name__ == "SandboxCapacityUnavailable"
    assert len(capacity.releases) == 1
    assert capacity.releases[0][0][0] == "free"


@pytest.mark.asyncio
async def test_lost_redis_lease_fails_admission_after_provision() -> None:
    class LeaseLossRedis(FakeRedis):
        async def eval(
            self,
            script: str,
            _keys: int,
            key: str,
            token: str,
            *_args: object,
        ) -> int:
            if "expire" in script:
                return 0
            return await super().eval(script, _keys, key, token, *_args)

    redis = LeaseLossRedis()
    capacity = FakeCapacity()
    scheduler = OpenSandboxNodeScheduler(
        SingleNode(), capacity, redis_client=redis, lease_ttl=1
    )

    async def provision(_binding: dict) -> None:
        await asyncio.sleep(0.4)
        assert capacity.binding is not None
        capacity.binding.update(
            sandbox_id="sandbox-1",
            allocation_state="allocated",
            sandbox_state="running",
        )

    with pytest.raises(Exception) as exc_info:
        await scheduler.admit("user-1", provision=provision)

    assert exc_info.value.__class__.__name__ == "SandboxCapacityUnavailable"


def test_node_config_rejects_duplicates_and_redacts_secrets() -> None:
    with pytest.raises(ValueError):
        OpenSandboxNodesUpdate(
            mode=OpenSandboxMode.MULTI_NODE,
            nodes=[
                {"id": "node-a", "domain": "https://a.example"},
                {"id": "node-a", "domain": "https://b.example"},
            ],
        )

    stored = StoredOpenSandboxNodes(
        OpenSandboxMode.MULTI_NODE,
        [{"id": "node-a", "domain": "https://a.example", "api_key": "secret", "max_sandboxes": 2}],
        "r1",
    )
    response = stored.to_response()
    assert response.nodes[0].has_api_key is True
    assert "secret" not in response.model_dump_json()
