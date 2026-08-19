"""Atomic capacity reservations and fenced user bindings."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import ensure_utc, parse_iso, utc_now
from src.kernel.config import settings

TERMINAL_RESERVATION_STATES = {
    "released",
    "terminated",
    "destroyed",
    "not_found",
}
_BINDING_INDEX_READY = False


class OpenSandboxCapacityStorage:
    def __init__(self, collection: Any | None = None, binding_collection: Any | None = None):
        self._collection_override = collection
        self._binding_override = binding_collection

    def _collection(self):
        if self._collection_override is not None:
            return self._collection_override
        return get_mongo_client()[settings.MONGODB_DB]["opensandbox_node_capacity"]

    def _bindings(self):
        if self._binding_override is not None:
            return self._binding_override
        return get_mongo_client()[settings.MONGODB_DB]["user_sandbox_bindings"]

    async def ensure_indexes(self) -> None:
        global _BINDING_INDEX_READY
        if _BINDING_INDEX_READY:
            return
        await self._bindings().create_index(
            "user_id",
            unique=True,
            name="user_id_unique_idx",
            background=True,
        )
        _BINDING_INDEX_READY = True

    async def ensure_node(self, node_id: str, *, max_sandboxes: int, enabled: bool = True) -> None:
        await self._collection().update_one(
            {"_id": node_id},
            {
                "$set": {"enabled": enabled, "max_sandboxes": max_sandboxes},
                "$setOnInsert": {"reservations": [], "health_state": "unknown"},
            },
            upsert=True,
        )
        # Terminal entries are authoritative release evidence and need not
        # accumulate forever in the array-backed ledger.
        await self._collection().update_one(
            {"_id": node_id},
            {
                "$pull": {
                    "reservations": {
                        "allocation_state": {"$in": sorted(TERMINAL_RESERVATION_STATES)}
                    }
                }
            },
        )

    @staticmethod
    def _active_reservation_count(document: dict[str, Any] | None) -> int:
        if not document:
            return 0
        return sum(
            1
            for item in document.get("reservations") or []
            if str(item.get("allocation_state", "unknown")) not in TERMINAL_RESERVATION_STATES
        )

    async def get_status(self, node_id: str) -> dict[str, Any] | None:
        document = await self._collection().find_one({"_id": node_id})
        if document is None:
            return None
        status = dict(document)
        status["used_sandboxes"] = self._active_reservation_count(status)
        status["over_capacity"] = status["used_sandboxes"] > int(status.get("max_sandboxes", 0) or 0)
        return status

    async def reserve(self, node_id: str, user_id: str, *, max_sandboxes: int | None = None, lease_seconds: int = 120) -> dict[str, Any] | None:
        now = utc_now()
        reservation = {"reservation_id": uuid.uuid4().hex, "user_id": user_id, "allocation_token": uuid.uuid4().hex, "allocation_state": "reserved", "created_at": now, "updated_at": now, "lease_expires_at": now + timedelta(seconds=lease_seconds)}
        # Lease expiry is not evidence of provider termination. All non-terminal
        # reservations, including expired creates and paused sandboxes, consume
        # capacity until conservative reconciliation releases them.
        active_reservations = {
            "$filter": {
                "input": {"$ifNull": ["$reservations", []]},
                "as": "reservation",
                "cond": {
                    "$not": [
                        {
                            "$in": [
                                "$$reservation.allocation_state",
                                sorted(TERMINAL_RESERVATION_STATES),
                            ]
                        }
                    ]
                },
            }
        }
        query: dict[str, Any] = {
            "_id": node_id,
            "enabled": True,
            "$expr": {
                "$lt": [
                    {"$size": active_reservations},
                    {"$ifNull": ["$max_sandboxes", max_sandboxes or 1]},
                ]
            },
        }
        update = {"$push": {"reservations": reservation}}
        result = await self._collection().update_one(query, update)
        return reservation if getattr(result, "modified_count", 0) else None

    async def adopt(
        self,
        node_id: str,
        user_id: str,
        sandbox_id: str,
        *,
        lease_seconds: int = 120,
    ) -> dict[str, Any] | None:
        """Account for an existing legacy sandbox, even above current capacity."""
        now = utc_now()
        reservation = {
            "reservation_id": uuid.uuid4().hex,
            "user_id": user_id,
            "allocation_token": uuid.uuid4().hex,
            "allocation_state": "allocated",
            "sandbox_id": sandbox_id,
            "sandbox_state": "running",
            "created_at": now,
            "updated_at": now,
            "lease_expires_at": now + timedelta(seconds=lease_seconds),
        }
        result = await self._collection().update_one(
            {
                "_id": node_id,
                "reservations": {
                    "$not": {"$elemMatch": {"user_id": user_id}}
                },
            },
            {"$push": {"reservations": reservation}},
        )
        return reservation if getattr(result, "modified_count", 0) else None

    async def release(self, node_id: str, reservation_id: str, *, allocation_token: str | None = None) -> bool:
        predicate: dict[str, Any] = {"_id": node_id, "reservations.reservation_id": reservation_id}
        if allocation_token:
            predicate["reservations.allocation_token"] = allocation_token
        result = await self._collection().update_one(predicate, {"$pull": {"reservations": {"reservation_id": reservation_id, **({"allocation_token": allocation_token} if allocation_token else {})}}})
        return bool(getattr(result, "modified_count", 0))

    async def transition(self, node_id: str, reservation_id: str, allocation_token: str, state: str, **fields: Any) -> bool:
        set_fields = {"reservations.$.allocation_state": state, "reservations.$.updated_at": utc_now()}
        set_fields.update({f"reservations.$.{key}": value for key, value in fields.items()})
        result = await self._collection().update_one({"_id": node_id, "reservations": {"$elemMatch": {"reservation_id": reservation_id, "allocation_token": allocation_token}}}, {"$set": set_fields})
        return bool(getattr(result, "modified_count", 0))

    async def reconcile_expired_creating(self, node_id: str, *, now: datetime | None = None) -> int:
        """Conservatively fence expired creates as unknown.

        An expired allocation lease does not prove that the provider sandbox is
        gone, so the reservation remains capacity-bearing until a managed probe
        can release it authoritatively.
        """
        now = now or utc_now()
        result = await self._collection().update_many(
            {
                "_id": node_id,
                "reservations": {
                    "$elemMatch": {
                        "allocation_state": {"$in": ["reserved", "creating"]},
                        "lease_expires_at": {"$lte": now},
                    }
                },
            },
            {
                "$set": {
                    "reservations.$[expired].allocation_state": "unknown",
                    "reservations.$[expired].updated_at": now,
                }
            },
            array_filters=[
                {
                    "expired.allocation_state": {"$in": ["reserved", "creating"]},
                    "expired.lease_expires_at": {"$lte": now},
                }
            ],
        )
        return int(getattr(result, "modified_count", 0) or 0)

    async def reconcile_provider_ttl(self, node_id: str, *, now: datetime | None = None) -> int:
        """Release orphaned creates only after their provider TTL is terminal.

        Redis/allocation lease expiry is deliberately ignored. A create whose
        response was lost can only be released after the maximum configured
        provider lifetime (plus the scheduler's safety margin) has elapsed.
        Token-fenced binding/release writes make a concurrent successful
        finalize or renewal win safely.
        """
        now = now or utc_now()
        document = await self._collection().find_one({"_id": node_id})
        if not document:
            return 0
        released = 0
        for reservation in document.get("reservations") or []:
            state = str(reservation.get("allocation_state", "unknown"))
            expires_at = reservation.get("provider_expires_at")
            if state not in {"creating", "unknown"} or not expires_at or expires_at > now:
                continue
            reservation_id = str(reservation.get("reservation_id", ""))
            token = str(reservation.get("allocation_token", ""))
            user_id = str(reservation.get("user_id", ""))
            await self.release_binding(user_id, node_id, reservation_id, token)
            if await self.release(
                node_id, reservation_id, allocation_token=token
            ):
                released += 1
        return released

    async def reconcile_expired_managed(
        self,
        node: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> int:
        """Probe locally expired managed rows when a node is full.

        This is intentionally lazy and bounded to rows whose recorded provider
        TTL has elapsed. A live row, paused row that still exists, or any
        ambiguous provider error keeps its slot. Only SDK-confirmed 404 frees it.
        """
        now = now or utc_now()
        status = await self.get_status(str(node["id"]))
        if not status:
            return 0
        from src.infra.async_utils import run_blocking_io
        from src.infra.sandbox.session_manager import OpenSandboxSandboxAdapter

        adapter = OpenSandboxSandboxAdapter(
            domain=str(node.get("domain", "")),
            api_key=str(node.get("api_key", "")),
            image=str(node.get("image", "ubuntu")),
            timeout=int(node.get("timeout", 3600)),
            work_dir=str(node.get("work_dir", "/root")),
            use_server_proxy=bool(node.get("use_server_proxy", True)),
            sync_settings=False,
        )
        released = 0
        for reservation in status.get("reservations") or []:
            if str(reservation.get("allocation_state", "unknown")) in TERMINAL_RESERVATION_STATES:
                continue
            sandbox_id = reservation.get("sandbox_id")
            expires_at = reservation.get("lease_expires_at")
            if isinstance(expires_at, str):
                expires_at = parse_iso(expires_at)
            elif isinstance(expires_at, datetime):
                expires_at = ensure_utc(expires_at)
            if not sandbox_id or not expires_at or expires_at > now:
                continue
            provider = await run_blocking_io(
                adapter.get_sandbox_unchecked, str(sandbox_id)
            )
            if provider is not None:
                continue
            node_id = str(node["id"])
            reservation_id = str(reservation.get("reservation_id", ""))
            token = str(reservation.get("allocation_token", ""))
            await self.release_binding(
                str(reservation.get("user_id", "")),
                node_id,
                reservation_id,
                token,
            )
            if await self.release(
                node_id, reservation_id, allocation_token=token
            ):
                released += 1
        return released

    async def get_binding(self, user_id: str) -> dict[str, Any] | None:
        return await self._bindings().find_one({"user_id": user_id})

    async def save_binding(self, user_id: str, *, allocation_token: str, **fields: Any) -> bool:
        # The allocation token is the Mongo fencing boundary: a stale lease
        # holder cannot overwrite a newer binding or release its reservation.
        await self.ensure_indexes()
        result = await self._bindings().update_one(
            {
                "user_id": user_id,
                "$or": [
                    {"allocation_token": allocation_token},
                    {"allocation_token": {"$exists": False}},
                    {"allocation_state": {"$in": ["released", "terminated"]}},
                ],
            },
            {
                "$set": {
                    **fields,
                    "allocation_token": allocation_token,
                    "updated_at": utc_now(),
                }
            },
            upsert=True,
        )
        return bool(getattr(result, "modified_count", 0) or getattr(result, "upserted_id", None))

    async def release_binding(self, user_id: str, node_id: str, reservation_id: str, allocation_token: str) -> bool:
        result = await self._bindings().update_one({"user_id": user_id, "node_id": node_id, "reservation_id": reservation_id, "allocation_token": allocation_token}, {"$set": {"allocation_state": "released", "sandbox_state": "terminated", "updated_at": utc_now()}})
        return bool(getattr(result, "modified_count", 0))


CapacityStorage = OpenSandboxCapacityStorage
