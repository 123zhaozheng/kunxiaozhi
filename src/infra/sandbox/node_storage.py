"""Revisioned, secret-safe OpenSandbox node configuration storage."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.infra.async_utils import run_blocking_io
from src.infra.mcp.encryption import decrypt_value, encrypt_value
from src.infra.sandbox.capacity_storage import (
    TERMINAL_RESERVATION_STATES,
    OpenSandboxCapacityStorage,
)
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.opensandbox import (
    OpenSandboxMode,
    OpenSandboxNodeResponse,
    OpenSandboxNodesResponse,
    OpenSandboxNodesUpdate,
)

_COLLECTION = "opensandbox_node_config"
_CURRENT_ID = "current"


@dataclass(frozen=True)
class StoredOpenSandboxNodes:
    mode: OpenSandboxMode
    nodes: list[dict[str, Any]]
    revision: str
    updated_at: Any | None = None
    updated_by: str | None = None

    def to_response(self, capacity: dict[str, dict[str, Any]] | None = None) -> OpenSandboxNodesResponse:
        capacity = capacity or {}
        result: list[OpenSandboxNodeResponse] = []
        for node in self.nodes:
            status = capacity.get(node["id"], {})
            result.append(
                OpenSandboxNodeResponse(
                    id=node["id"], domain=node["domain"], has_api_key=bool(node.get("api_key")),
                    image=node.get("image", "ubuntu"), timeout=int(node.get("timeout", 3600)),
                    work_dir=node.get("work_dir", "/root"), use_server_proxy=bool(node.get("use_server_proxy", True)),
                    max_sandboxes=int(node.get("max_sandboxes", 1)), enabled=bool(node.get("enabled", True)),
                    priority=int(node.get("priority", 100)), draining=bool(node.get("draining", False)),
                    health_state=status.get("health_state", node.get("health_state", "unknown")),
                    last_health_at=status.get("last_health_at", node.get("last_health_at")),
                    last_error=status.get("last_error", node.get("last_error")),
                    used_sandboxes=int(status.get("used_sandboxes", node.get("used_sandboxes", 0))),
                    over_capacity=bool(status.get("over_capacity", node.get("over_capacity", False))),
                )
            )
        return OpenSandboxNodesResponse(mode=self.mode, nodes=result, revision=self.revision, updated_at=self.updated_at, updated_by=self.updated_by)


def _legacy_node() -> dict[str, Any]:
    return {
        "id": "legacy-default",
        "domain": getattr(settings, "OPENSANDBOX_DOMAIN", "") or "http://localhost:8080",
        "api_key": getattr(settings, "OPENSANDBOX_API_KEY", "") or "",
        "image": getattr(settings, "OPENSANDBOX_IMAGE", "ubuntu"),
        "timeout": getattr(settings, "OPENSANDBOX_TIMEOUT", 3600),
        "work_dir": getattr(settings, "OPENSANDBOX_WORK_DIR", "/root"),
        "use_server_proxy": getattr(settings, "OPENSANDBOX_USE_SERVER_PROXY", True),
        "max_sandboxes": 100000,
        "enabled": True,
        "priority": 100,
        "draining": False,
    }


class OpenSandboxNodeStorage:
    def __init__(self, collection: Any | None = None, capacity_collection: Any | None = None, binding_collection: Any | None = None):
        self._collection_override = collection
        self._capacity_override = capacity_collection
        self._binding_override = binding_collection

    def _collection(self):
        if self._collection_override is not None:
            return self._collection_override
        client = get_mongo_client()
        return client[settings.MONGODB_DB][_COLLECTION]

    def _capacity_collection(self):
        if self._capacity_override is not None:
            return self._capacity_override
        return get_mongo_client()[settings.MONGODB_DB]["opensandbox_node_capacity"]

    def _binding_collection(self):
        if self._binding_override is not None:
            return self._binding_override
        return get_mongo_client()[settings.MONGODB_DB]["user_sandbox_bindings"]

    def _capacity_storage(self) -> OpenSandboxCapacityStorage:
        return OpenSandboxCapacityStorage(
            collection=self._capacity_override,
            binding_collection=self._binding_override,
        )

    @staticmethod
    def _has_active_reservations(document: dict[str, Any] | None) -> bool:
        if not document:
            return False
        return any(
            str(reservation.get("allocation_state", "unknown")) not in TERMINAL_RESERVATION_STATES
            for reservation in document.get("reservations") or []
        )

    async def _has_active_binding(self, node_id: str | list[str]) -> bool:
        query: dict[str, Any]
        if isinstance(node_id, list):
            query = {"node_id": {"$in": node_id}}
        else:
            query = {"node_id": node_id}
        query["sandbox_state"] = {"$nin": sorted(TERMINAL_RESERVATION_STATES)}
        return bool(await self._binding_collection().find_one(query))

    async def get_current(self) -> StoredOpenSandboxNodes:
        doc = await self._collection().find_one({"_id": _CURRENT_ID})
        if not doc:
            return StoredOpenSandboxNodes(OpenSandboxMode.LEGACY, [_legacy_node()], "legacy")
        mode = OpenSandboxMode(doc.get("mode", "multi_node"))
        if mode == OpenSandboxMode.LEGACY:
            return StoredOpenSandboxNodes(
                mode,
                [_legacy_node()],
                str(doc.get("revision", "default")),
                doc.get("updated_at"),
                doc.get("updated_by"),
            )
        nodes = []
        for raw in doc.get("nodes", []):
            item = dict(raw)
            encrypted = item.get("encrypted_api_key")
            if encrypted:
                decrypted = await run_blocking_io(decrypt_value, encrypted)
                item["api_key"] = str(decrypted.get("v") or "") if isinstance(decrypted, dict) else ""
            item.pop("encrypted_api_key", None)
            nodes.append(item)
        # Merge the durable ledger into the read model so Admin sees occupancy,
        # health and last-error data without exposing credentials.
        capacity = self._capacity_override
        if capacity is None and self._collection_override is None:
            client = get_mongo_client()
            capacity = client[settings.MONGODB_DB]["opensandbox_node_capacity"]
        if capacity is not None:
            for node in nodes:
                status = await capacity.find_one({"_id": node["id"]})
                if not status:
                    continue
                used = sum(
                    1
                    for reservation in status.get("reservations") or []
                    if str(reservation.get("allocation_state", "unknown"))
                    not in TERMINAL_RESERVATION_STATES
                )
                node.update(
                    health_state=status.get("health_state", "unknown"),
                    last_health_at=status.get("last_health_at"),
                    last_error=status.get("last_error"),
                    used_sandboxes=used,
                    over_capacity=used > int(node.get("max_sandboxes", 0) or 0),
                )
        return StoredOpenSandboxNodes(mode, nodes, str(doc.get("revision", "default")), doc.get("updated_at"), doc.get("updated_by"))

    async def save(self, update: OpenSandboxNodesUpdate, *, updated_by: str) -> StoredOpenSandboxNodes:
        current = await self.get_current()
        if update.expected_revision is not None and update.expected_revision != current.revision:
            raise ValueError("opensandbox_nodes_revision_conflict")
        existing = {node["id"]: node for node in current.nodes}
        requested_ids = {node.id for node in update.nodes}
        removed_ids = set(existing) - requested_ids
        requested_domains = {node.domain.lower(): node.id for node in update.nodes}
        for removed_id in removed_ids:
            old_domain = str(existing[removed_id].get("domain", "")).lower()
            if old_domain and old_domain in requested_domains:
                raise ValueError("opensandbox_node_id_immutable")
        if current.mode == OpenSandboxMode.MULTI_NODE and update.mode == OpenSandboxMode.LEGACY:
            capacity = self._capacity_collection()
            for node_id in existing:
                cap = await capacity.find_one({"_id": node_id})
                if self._has_active_reservations(cap):
                    raise ValueError("opensandbox_legacy_mode_in_use")
            if await self._has_active_binding(list(existing)):
                raise ValueError("opensandbox_legacy_mode_in_use")
        if removed_ids:
            capacity = self._capacity_collection()
            for node_id in removed_ids:
                cap = await capacity.find_one({"_id": node_id})
                if self._has_active_reservations(cap):
                    raise ValueError("opensandbox_node_in_use")
                if await self._has_active_binding(node_id):
                    raise ValueError("opensandbox_node_in_use")
        payload_nodes: list[dict[str, Any]] = []
        for node in update.nodes:
            item = node.model_dump(exclude={"api_key", "clear_api_key"})
            secret = "" if node.clear_api_key else (node.api_key if node.api_key else existing.get(node.id, {}).get("api_key", ""))
            item["encrypted_api_key"] = await run_blocking_io(encrypt_value, {"v": secret}) if secret else None
            item["draining"] = bool(existing.get(node.id, {}).get("draining", False))
            payload_nodes.append(item)
        revision = uuid.uuid4().hex
        query: dict[str, Any] = {"_id": _CURRENT_ID, "revision": current.revision}
        if current.revision == "legacy":
            query = {"_id": _CURRENT_ID, "$or": [{"revision": "legacy"}, {"revision": {"$exists": False}}]}
        result = await self._collection().update_one(query, {"$set": {"mode": update.mode.value, "nodes": payload_nodes, "revision": revision, "updated_at": utc_now(), "updated_by": updated_by}}, upsert=current.revision == "legacy")
        if not getattr(result, "acknowledged", True) or (not getattr(result, "matched_count", 1) and current.revision != "legacy"):
            raise ValueError("opensandbox_nodes_revision_conflict")
        capacity = self._capacity_override
        if capacity is None and self._collection_override is None:
            capacity = self._capacity_collection()
        if capacity is not None:
            for node in update.nodes:
                await capacity.update_one(
                    {"_id": node.id},
                    {
                        "$set": {
                            "enabled": node.enabled,
                            "max_sandboxes": node.max_sandboxes,
                        },
                        "$setOnInsert": {"reservations": [], "health_state": "unknown"},
                    },
                    upsert=True,
                )
            for removed_id in removed_ids:
                await capacity.delete_one({"_id": removed_id})
        return StoredOpenSandboxNodes(update.mode, [{**n, "api_key": (node.api_key or existing.get(node.id, {}).get("api_key", "")) if not node.clear_api_key else ""} for n, node in zip(payload_nodes, update.nodes)], revision, utc_now(), updated_by)

    async def set_draining(self, node_id: str, draining: bool) -> bool:
        result = await self._collection().update_one({"_id": _CURRENT_ID, "nodes.id": node_id}, {"$set": {"nodes.$.draining": draining}})
        return bool(getattr(result, "matched_count", 0))

    async def remove(self, node_id: str) -> bool:
        """Remove only an unused node; callers must drain/disable first."""
        capacity = self._capacity_collection()
        cap = await capacity.find_one({"_id": node_id})
        if self._has_active_reservations(cap):
            raise ValueError("opensandbox_node_in_use")
        if await self._has_active_binding(node_id):
            raise ValueError("opensandbox_node_in_use")
        result = await self._collection().update_one({"_id": _CURRENT_ID, "nodes.id": node_id}, {"$pull": {"nodes": {"id": node_id}}})
        if getattr(result, "modified_count", 0):
            await capacity.delete_one({"_id": node_id})
        return bool(getattr(result, "modified_count", 0))

    async def force_remove(
        self,
        node_id: str,
        *,
        expected_revision: str,
        updated_by: str,
    ) -> tuple[StoredOpenSandboxNodes, int, bool]:
        """Purge occupancy and pull a node, switching to legacy when none remain."""
        current = await self.get_current()
        if expected_revision != current.revision:
            raise ValueError("opensandbox_nodes_revision_conflict")
        if current.mode == OpenSandboxMode.LEGACY:
            # The legacy read model is synthesized from scalar settings, so
            # there is no managed node document to purge or pull.
            raise ValueError("opensandbox_node_not_found")
        node = next((item for item in current.nodes if item.get("id") == node_id), None)
        if node is None:
            raise ValueError("opensandbox_node_not_found")
        health_state = str(node.get("health_state") or "unknown").lower()
        capacity = self._capacity_collection()
        cap = await capacity.find_one({"_id": node_id})
        occupied = self._has_active_reservations(cap) or await self._has_active_binding(node_id)
        if health_state == "healthy" and occupied:
            raise ValueError("opensandbox_node_in_use")
        raw = await self._collection().find_one({"_id": _CURRENT_ID})
        if not raw:
            raise ValueError("opensandbox_nodes_revision_conflict")
        # Rewrite the stored node dicts verbatim so encrypted_api_key survives;
        # the decrypted read model must never be written back.
        remaining = [dict(item) for item in raw.get("nodes") or [] if item.get("id") != node_id]
        switched_to_legacy = not remaining
        mode = OpenSandboxMode.LEGACY if switched_to_legacy else current.mode
        revision = uuid.uuid4().hex
        # Legacy mode is rejected above, so the stored revision is always a real
        # CAS token here: no upsert, no synthesized-revision special case.
        result = await self._collection().update_one(
            {"_id": _CURRENT_ID, "revision": current.revision},
            {
                "$set": {
                    "mode": mode.value,
                    "nodes": remaining,
                    "revision": revision,
                    "updated_at": utc_now(),
                    "updated_by": updated_by,
                }
            },
        )
        if not getattr(result, "acknowledged", True) or not getattr(result, "matched_count", 1):
            raise ValueError("opensandbox_nodes_revision_conflict")
        # The config CAS runs first: a lost race must not leave a still
        # configured node whose occupancy ledger was already wiped.
        released_count = await self._capacity_storage().purge_node_occupancy(node_id)
        await self._capacity_storage().delete_node_capacity(node_id)
        stored = await self.get_current()
        return stored, released_count, switched_to_legacy


_storage: OpenSandboxNodeStorage | None = None


def get_opensandbox_node_storage() -> OpenSandboxNodeStorage:
    global _storage
    if _storage is None:
        _storage = OpenSandboxNodeStorage()
    return _storage
