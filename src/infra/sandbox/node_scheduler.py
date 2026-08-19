"""Fail-closed OpenSandbox admission with durable capacity and fencing."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import timedelta
from typing import Any

from src.infra.sandbox.capacity_storage import OpenSandboxCapacityStorage
from src.infra.sandbox.node_storage import OpenSandboxNodeStorage
from src.infra.storage.redis import create_redis_client
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.opensandbox import OpenSandboxCapacityUnavailable, OpenSandboxMode

_LEASE_PREFIX = "opensandbox:allocation"
_LEASE_TTL_SECONDS = 120
_RENEW_LEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_LEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class OpenSandboxAllocationLease:
    """Token-owned Redis lease used only during a new user allocation."""

    def __init__(self, user_id: str, *, redis_client: Any | None = None, ttl: int = _LEASE_TTL_SECONDS):
        self.user_id = user_id
        self.token = uuid.uuid4().hex
        self.ttl = max(1, int(ttl))
        self._redis = redis_client

    @property
    def key(self) -> str:
        return f"{_LEASE_PREFIX}:{self.user_id}"

    def _client(self) -> Any:
        if self._redis is None:
            self._redis = create_redis_client(isolated_pool=True)
        return self._redis

    async def acquire(self) -> bool:
        # Redis errors deliberately propagate: allocation cannot be made safe
        # without the cross-replica lease.
        return bool(await self._client().set(self.key, self.token, nx=True, ex=self.ttl))

    async def renew(self) -> bool:
        return bool(await self._client().eval(_RENEW_LEASE_LUA, 1, self.key, self.token, self.ttl))

    async def release(self) -> bool:
        return bool(await self._client().eval(_RELEASE_LEASE_LUA, 1, self.key, self.token))


class OpenSandboxNodeScheduler:
    def __init__(
        self,
        node_storage: OpenSandboxNodeStorage | None = None,
        capacity: OpenSandboxCapacityStorage | None = None,
        *,
        redis_client: Any | None = None,
        lease_ttl: int = _LEASE_TTL_SECONDS,
    ):
        self.nodes = node_storage or OpenSandboxNodeStorage()
        self.capacity = capacity or OpenSandboxCapacityStorage()
        self._redis_client = redis_client
        self._lease_ttl = lease_ttl

    async def select_node(self, user_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            current = await self.nodes.get_current()
        except Exception as exc:
            raise OpenSandboxCapacityUnavailable() from exc
        candidates: list[tuple[float, int, str, dict[str, Any]]] = []
        for node in current.nodes:
            if not node.get("enabled", True) or node.get("draining", False) or not node.get("domain"):
                continue
            max_sandboxes = int(node.get("max_sandboxes", 1))
            try:
                await self.capacity.ensure_node(node["id"], max_sandboxes=max_sandboxes, enabled=True)
                await self.capacity.reconcile_expired_creating(node["id"])
                await self.capacity.reconcile_provider_ttl(node["id"])
                status = await self.capacity.get_status(node["id"])
            except Exception as exc:
                raise OpenSandboxCapacityUnavailable() from exc
            used = int((status or {}).get("used_sandboxes", 0))
            candidates.append(
                (
                    used / max_sandboxes if max_sandboxes else float("inf"),
                    int(node.get("priority", 100)),
                    str(node["id"]),
                    node,
                )
            )

        candidates.sort(key=lambda item: item[:3])
        # Reserve one candidate at a time.  Reserving every node first creates
        # avoidable races and transient over-reservation across replicas.
        for _, _, _, node in candidates:
            try:
                reservation = await self.capacity.reserve(
                    node["id"], user_id, max_sandboxes=int(node.get("max_sandboxes", 1))
                )
            except Exception as exc:
                # Storage failure is fail-closed rather than treated as a full
                # node, since continuing could allocate without a ledger.
                raise OpenSandboxCapacityUnavailable() from exc
            if reservation is not None:
                return node, reservation
            try:
                released = await self.capacity.reconcile_expired_managed(node)
            except Exception:
                # Ambiguous provider failures preserve occupancy. Another
                # candidate may still have atomically reservable capacity.
                released = 0
            if released:
                try:
                    reservation = await self.capacity.reserve(
                        node["id"],
                        user_id,
                        max_sandboxes=int(node.get("max_sandboxes", 1)),
                    )
                except Exception as exc:
                    raise OpenSandboxCapacityUnavailable() from exc
                if reservation is not None:
                    return node, reservation
        raise OpenSandboxCapacityUnavailable()

    @staticmethod
    def _binding_is_active(binding: dict[str, Any] | None) -> bool:
        if not binding:
            return False
        return str(binding.get("sandbox_state") or binding.get("allocation_state") or "unknown") not in {
            "terminated",
            "destroyed",
            "not_found",
            "released",
        } and bool(binding.get("allocation_token"))

    async def admit(
        self,
        user_id: str,
        *,
        provision: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        discover_legacy: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None,
    ) -> dict[str, Any]:
        """Admit and optionally provision a user while holding the token lease.

        ``provision`` is used by Web admission to keep the distributed lease
        renewable until the provider sandbox and fenced binding are durable.
        A binding in ``creating`` without a sandbox id belongs to that holder;
        another replica must fail closed instead of creating a duplicate.
        """
        try:
            current = await self.nodes.get_current()
        except Exception as exc:
            raise OpenSandboxCapacityUnavailable() from exc
        if current.mode == OpenSandboxMode.LEGACY or str(current.mode) == OpenSandboxMode.LEGACY.value:
            return {"user_id": user_id, "allocation_state": "legacy"}

        lease = OpenSandboxAllocationLease(
            user_id, redis_client=self._redis_client, ttl=self._lease_ttl
        )
        renewal_task: asyncio.Task[None] | None = None

        async def renew_lease() -> None:
            interval = max(0.1, lease.ttl / 3)
            while True:
                await asyncio.sleep(interval)
                if not await lease.renew():
                    raise OpenSandboxCapacityUnavailable()

        try:
            try:
                acquired = await lease.acquire()
            except Exception as exc:
                raise OpenSandboxCapacityUnavailable() from exc
            if not acquired:
                raise OpenSandboxCapacityUnavailable()
            renewal_task = asyncio.create_task(renew_lease())

            # Re-read after acquiring the distributed lease.  Another replica
            # may have completed this user's allocation immediately before us.
            binding = await self.capacity.get_binding(user_id)
            if self._binding_is_active(binding):
                if not binding or not binding.get("sandbox_id"):
                    raise OpenSandboxCapacityUnavailable()
                return binding or {"user_id": user_id}
            if (
                binding
                and binding.get("sandbox_id")
                and not binding.get("node_id")
                and discover_legacy is not None
            ):
                node_id = await discover_legacy(binding)
                if node_id is not None:
                    node = next(
                        (item for item in current.nodes if item.get("id") == node_id),
                        None,
                    )
                    if node is None:
                        raise OpenSandboxCapacityUnavailable()
                    await self.capacity.ensure_node(
                        node_id,
                        max_sandboxes=int(node.get("max_sandboxes", 1)),
                        enabled=bool(node.get("enabled", True)),
                    )
                    adopted = await self.capacity.adopt(
                        node_id, user_id, str(binding["sandbox_id"])
                    )
                    if adopted is None:
                        raise OpenSandboxCapacityUnavailable()
                    token = str(adopted["allocation_token"])
                    saved = await self.capacity.save_binding(
                        user_id,
                        allocation_token=token,
                        provider="opensandbox",
                        node_id=node_id,
                        reservation_id=adopted["reservation_id"],
                        sandbox_id=str(binding["sandbox_id"]),
                        allocation_state="allocated",
                        sandbox_state=str(binding.get("sandbox_state", "running")),
                    )
                    if not saved:
                        await self.capacity.release(
                            node_id,
                            adopted["reservation_id"],
                            allocation_token=token,
                        )
                        raise OpenSandboxCapacityUnavailable()
                    adopted_binding = await self.capacity.get_binding(user_id)
                    if not adopted_binding or adopted_binding.get("allocation_token") != token:
                        raise OpenSandboxCapacityUnavailable()
                    return adopted_binding
            if binding and binding.get("node_id") and binding.get("reservation_id") and binding.get("allocation_token"):
                # Authoritative provider absence/termination permits a fresh
                # allocation, but the old ledger slot must be released first.
                old_state = str(binding.get("sandbox_state") or binding.get("allocation_state") or "")
                if old_state in {"not_found", "terminated", "destroyed", "released"}:
                    await self.capacity.release(
                        str(binding["node_id"]),
                        str(binding["reservation_id"]),
                        allocation_token=str(binding["allocation_token"]),
                    )

            node, reservation = await self.select_node(user_id)
            token = str(reservation["allocation_token"])
            provider_expires_at = utc_now() + timedelta(
                seconds=int(node.get("timeout", 3600)) + 300
            )
            transitioned = await self.capacity.transition(
                node["id"],
                reservation["reservation_id"],
                token,
                "creating",
                provider_expires_at=provider_expires_at,
            )
            if not transitioned:
                await self.capacity.release(
                    node["id"], reservation["reservation_id"], allocation_token=token
                )
                raise OpenSandboxCapacityUnavailable()
            try:
                saved = await self.capacity.save_binding(
                    user_id,
                    allocation_token=token,
                    provider="opensandbox",
                    node_id=node["id"],
                    reservation_id=reservation["reservation_id"],
                    allocation_state="creating",
                    sandbox_state="creating",
                    provider_expires_at=provider_expires_at,
                )
            except Exception as exc:
                await self.capacity.release(
                    node["id"], reservation["reservation_id"], allocation_token=token
                )
                raise OpenSandboxCapacityUnavailable() from exc
            if not saved:
                await self.capacity.release(
                    node["id"], reservation["reservation_id"], allocation_token=token
                )
                raise OpenSandboxCapacityUnavailable()

            confirmed = await self.capacity.get_binding(user_id)
            if not confirmed or confirmed.get("allocation_token") != token:
                await self.capacity.release(
                    node["id"], reservation["reservation_id"], allocation_token=token
                )
                raise OpenSandboxCapacityUnavailable()
            if provision is not None:
                # Provisioning owns compensation because it can distinguish a
                # definitive rejected create from an ambiguous timeout and a
                # post-create persistence failure.
                await provision(confirmed)
                confirmed = await self.capacity.get_binding(user_id)
                if not confirmed or confirmed.get("allocation_token") != token:
                    raise OpenSandboxCapacityUnavailable()
            return confirmed
        finally:
            if renewal_task is not None:
                renewal_task.cancel()
                with suppress(asyncio.CancelledError):
                    await renewal_task
            # Token-safe release means an expired/replaced holder cannot delete
            # the newer user's lease.
            try:
                await lease.release()
            except Exception:
                # Redis failure after Mongo admission is harmless; the token
                # expires naturally and Mongo remains the fencing boundary.
                pass


async def admit_opensandbox_user(
    user_id: str,
    *,
    provision: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    discover_legacy: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None,
) -> dict[str, Any]:
    return await OpenSandboxNodeScheduler().admit(
        user_id, provision=provision, discover_legacy=discover_legacy
    )


NodeScheduler = OpenSandboxNodeScheduler
