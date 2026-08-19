from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import opensandbox_admin
from src.infra.sandbox.node_storage import StoredOpenSandboxNodes
from src.kernel.schemas.opensandbox import OpenSandboxMode
from src.kernel.schemas.user import TokenPayload


def _admin() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(opensandbox_admin.router, prefix="/api/opensandbox")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin
    return app


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.offset = 0
        self.count = len(documents)

    def sort(self, _fields: list[tuple[str, int]]) -> "FakeCursor":
        return self

    def skip(self, value: int) -> "FakeCursor":
        self.offset = value
        return self

    def limit(self, value: int) -> "FakeCursor":
        self.count = value
        return self

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return deepcopy(self.documents[self.offset : self.offset + min(length, self.count)])


class FakeBindings:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.last_query: dict[str, Any] | None = None
        self.updates: list[tuple[dict, dict]] = []

    async def count_documents(self, query: dict[str, Any]) -> int:
        self.last_query = deepcopy(query)
        return len(self.documents)

    def find(self, query: dict[str, Any]) -> FakeCursor:
        self.last_query = deepcopy(query)
        return FakeCursor(self.documents)

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (
                deepcopy(item)
                for item in self.documents
                if item.get("node_id") == query.get("node_id")
                and item.get("sandbox_id") == query.get("sandbox_id")
            ),
            None,
        )

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        self.updates.append((deepcopy(query), deepcopy(update)))
        return SimpleNamespace(modified_count=1, matched_count=1)


class FakeCapacity:
    def __init__(self, bindings: FakeBindings) -> None:
        self.bindings = bindings
        self.transitions: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.released_bindings: list[tuple[Any, ...]] = []
        self.released_reservations: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _bindings(self) -> FakeBindings:
        return self.bindings

    async def transition(self, *args: Any, **kwargs: Any) -> bool:
        self.transitions.append((args, kwargs))
        return True

    async def release_binding(self, *args: Any, **_kwargs: Any) -> bool:
        self.released_bindings.append(args)
        return True

    async def release(self, *args: Any, **kwargs: Any) -> bool:
        self.released_reservations.append((args, kwargs))
        return True


class FakeNodeStorage:
    mode = OpenSandboxMode.MULTI_NODE

    async def get_current(self) -> StoredOpenSandboxNodes:
        return StoredOpenSandboxNodes(
            self.mode,
            [
                {
                    "id": "node-a",
                    "domain": "https://node-a.example",
                    "api_key": "secret",
                    "image": "ubuntu",
                    "timeout": 600,
                    "work_dir": "/root",
                    "use_server_proxy": True,
                    "max_sandboxes": 10,
                }
            ],
            "rev-1",
        )


class FakeAdapter:
    provider_state = "running"
    fail_action: str | None = None
    calls: list[str] = []

    def __init__(self, **_kwargs: Any) -> None:
        self._timeout = 600

    def get_sandbox(self, _sandbox_id: str) -> object:
        self.calls.append("connect")
        return object()

    def get_sandbox_unchecked(self, _sandbox_id: str) -> object:
        self.calls.append("connect-unchecked")
        return object()

    def get_sandbox_info(self, _provider: object) -> dict[str, str]:
        return {"state": self.provider_state}

    def resume_sandbox_by_id(self, _sandbox_id: str) -> object:
        self.calls.append("resume")
        return object()

    def pause_sandbox(self, _provider: object) -> None:
        self.calls.append("pause")
        if self.fail_action == "pause":
            raise TimeoutError("pause failed")

    def extend_timeout(self, _provider: object, timeout: int) -> None:
        assert timeout == 600
        self.calls.append("renew")

    def kill_sandbox(self, _provider: object) -> None:
        self.calls.append("terminate")


def _binding(state: str, *, sandbox_id: str = "sandbox-a") -> dict[str, Any]:
    return {
        "_id": "binding-a",
        "user_id": "user-a",
        "node_id": "node-a",
        "sandbox_id": sandbox_id,
        "reservation_id": "reservation-a",
        "allocation_token": "token-a",
        "allocation_state": "allocated" if state == "running" else state,
        "sandbox_state": state,
        "sandbox_created_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
        "sandbox_last_used_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
        "lease_expires_at": datetime(2026, 8, 19, 1, tzinfo=timezone.utc),
    }


def _patch_services(
    monkeypatch: pytest.MonkeyPatch,
    capacity: FakeCapacity,
    *,
    mode: OpenSandboxMode = OpenSandboxMode.MULTI_NODE,
) -> None:
    FakeAdapter.calls = []
    FakeAdapter.fail_action = None
    FakeAdapter.provider_state = "running"
    FakeNodeStorage.mode = mode
    monkeypatch.setattr(
        "src.infra.sandbox.capacity_storage.OpenSandboxCapacityStorage",
        lambda: capacity,
    )
    monkeypatch.setattr(
        "src.infra.sandbox.node_storage.get_opensandbox_node_storage",
        lambda: FakeNodeStorage(),
    )
    monkeypatch.setattr(
        "src.infra.sandbox.session_manager.OpenSandboxSandboxAdapter",
        FakeAdapter,
    )

    async def _empty_usernames(_user_ids: list[str]) -> dict[str, str]:
        return {}

    async def _empty_username_matches(_search: str) -> list[str]:
        return []

    monkeypatch.setattr(opensandbox_admin, "load_usernames", _empty_usernames)
    monkeypatch.setattr(opensandbox_admin, "user_ids_matching_username", _empty_username_matches)


def test_action_matrix_is_canonical_for_all_managed_states() -> None:
    assert opensandbox_admin.available_actions("running") == {
        "pause": True,
        "resume": False,
        "renew": True,
        "terminate": True,
    }
    assert opensandbox_admin.available_actions("paused") == {
        "pause": False,
        "resume": True,
        "renew": True,
        "terminate": True,
    }
    assert opensandbox_admin.available_actions("creating") == {
        "pause": False,
        "resume": False,
        "renew": False,
        "terminate": True,
    }
    assert opensandbox_admin.available_actions("unknown") == {
        "pause": False,
        "resume": False,
        "renew": True,
        "terminate": True,
    }
    assert not any(opensandbox_admin.available_actions("terminated").values())
    assert not any(opensandbox_admin.available_actions("running", has_sandbox_id=False).values())


@pytest.mark.asyncio
async def test_inventory_applies_filters_pagination_and_api_action_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [_binding("running", sandbox_id=f"sandbox-{index}") for index in range(30)]
    bindings = FakeBindings(documents)
    capacity = FakeCapacity(bindings)
    _patch_services(monkeypatch, capacity)

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/opensandbox/sandboxes",
            params={
                "skip": 25,
                "limit": 5,
                "node_id": "node-a",
                "state": "running",
                "search": "user-a",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 30
    assert len(payload["items"]) == 5
    assert payload["items"][0]["sandbox_id"] == "sandbox-25"
    assert payload["items"][0]["actions"] == opensandbox_admin.available_actions("running")
    assert bindings.last_query == {
        "node_id": "node-a",
        "sandbox_state": "running",
        "$or": [
            {"sandbox_id": {"$regex": "user-a", "$options": "i"}},
            {"user_id": {"$regex": "user-a", "$options": "i"}},
        ],
    }
    assert payload["items"][0]["username"] is None


@pytest.mark.asyncio
async def test_inventory_is_empty_in_legacy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = FakeBindings([_binding("running")])
    capacity = FakeCapacity(bindings)
    _patch_services(monkeypatch, capacity, mode=OpenSandboxMode.LEGACY)

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/opensandbox/sandboxes")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "skip": 0, "limit": 50}
    assert bindings.last_query is None


@pytest.mark.asyncio
async def test_inventory_joins_username_and_searches_by_employee_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "507f1f77bcf86cd799439011"
    documents = [_binding("running")]
    documents[0]["user_id"] = user_id
    bindings = FakeBindings(documents)
    capacity = FakeCapacity(bindings)
    _patch_services(monkeypatch, capacity)

    async def fake_load_usernames(user_ids: list[str]) -> dict[str, str]:
        return {uid: "zhangsan" for uid in user_ids if uid == user_id}

    async def fake_matching(search: str) -> list[str]:
        return [user_id] if "zhang" in search.lower() else []

    monkeypatch.setattr(opensandbox_admin, "load_usernames", fake_load_usernames)
    monkeypatch.setattr(opensandbox_admin, "user_ids_matching_username", fake_matching)

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://testserver"
    ) as client:
        listed = await client.get("/api/opensandbox/sandboxes")
        searched = await client.get(
            "/api/opensandbox/sandboxes",
            params={"search": "zhang"},
        )

    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["user_id"] == user_id
    assert item["username"] == "zhangsan"
    assert searched.status_code == 200
    assert searched.json()["items"][0]["username"] == "zhangsan"
    assert bindings.last_query == {
        "node_id": {"$exists": True, "$nin": [None, ""]},
        "$or": [
            {"sandbox_id": {"$regex": "zhang", "$options": "i"}},
            {"user_id": {"$regex": "zhang", "$options": "i"}},
            {"user_id": {"$in": [user_id]}},
        ],
    }


@pytest.mark.asyncio
async def test_load_usernames_queries_users_by_object_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bson import ObjectId

    user_id = "507f1f77bcf86cd799439011"
    captured: dict[str, Any] = {}

    class FakeCursor:
        def __aiter__(self):
            async def iterator():
                yield {"_id": ObjectId(user_id), "username": "zhangsan"}

            return iterator()

    class FakeUsers:
        def find(self, query: dict[str, Any], projection: dict[str, int]) -> FakeCursor:
            captured["query"] = query
            captured["projection"] = projection
            return FakeCursor()

    class FakeClient(dict):
        def __getitem__(self, _name: str) -> dict[str, FakeUsers]:
            return {"users": FakeUsers()}

    monkeypatch.setattr(
        "src.infra.storage.mongodb.get_mongo_client",
        lambda: FakeClient(),
    )

    mapping = await opensandbox_admin.load_usernames([user_id, "not-an-id"])

    assert mapping == {user_id: "zhangsan"}
    assert "id" not in captured["query"]
    assert captured["projection"] == {"_id": 1, "username": 1}
    object_ids = captured["query"]["_id"]["$in"]
    assert object_ids == [ObjectId(user_id)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "action", "expected_call"),
    [
        ("running", "pause", "pause"),
        ("paused", "resume", "resume"),
        ("paused", "renew", "renew"),
        ("unknown", "terminate", "terminate"),
    ],
)
async def test_lifecycle_actions_update_provider_binding_and_reservation(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    action: str,
    expected_call: str,
) -> None:
    bindings = FakeBindings([_binding(state)])
    capacity = FakeCapacity(bindings)
    _patch_services(monkeypatch, capacity)

    result = await opensandbox_admin._action("node-a", "sandbox-a", action)

    assert result["action"] == action
    assert expected_call in FakeAdapter.calls
    if action == "terminate":
        assert capacity.released_bindings
        assert capacity.released_reservations
    else:
        assert bindings.updates
        assert capacity.transitions
    if action == "renew" and state == "paused":
        assert "connect-unchecked" in FakeAdapter.calls


@pytest.mark.asyncio
async def test_disallowed_and_provider_failed_actions_do_not_change_bookkeeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = FakeBindings([_binding("paused")])
    capacity = FakeCapacity(bindings)
    _patch_services(monkeypatch, capacity)

    with pytest.raises(Exception) as disallowed:
        await opensandbox_admin._action("node-a", "sandbox-a", "pause")
    assert getattr(disallowed.value, "status_code", None) == 409

    bindings.documents = [_binding("running")]
    FakeAdapter.fail_action = "pause"
    with pytest.raises(Exception) as failed:
        await opensandbox_admin._action("node-a", "sandbox-a", "pause")
    assert getattr(failed.value, "status_code", None) == 502
    assert bindings.updates == []
    assert capacity.transitions == []
