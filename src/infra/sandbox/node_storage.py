"""Revisioned, secret-safe OpenSandbox node configuration storage."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.infra.async_utils import run_blocking_io
from src.infra.mcp.encryption import decrypt_value, encrypt_value
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
                reservations = status.get("reservations") or []
                used = sum(
                    1
                    for reservation in reservations
                    if reservation.get("allocation_state", "unknown")
                    not in {"released", "terminated", "destroyed", "not_found"}
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
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            capacity = self._capacity_override or db["opensandbox_node_capacity"]
            binding = self._binding_override or db["user_sandbox_bindings"]
            for node_id in existing:
                cap = await capacity.find_one({"_id": node_id})
                if cap and any(
                    reservation.get("allocation_state", "unknown")
                    not in {"released", "terminated", "destroyed", "not_found"}
                    for reservation in cap.get("reservations") or []
                ):
                    raise ValueError("opensandbox_legacy_mode_in_use")
            if await binding.find_one(
                {
                    "node_id": {"$in": list(existing)},
                    "sandbox_state": {
                        "$nin": ["terminated", "destroyed", "not_found", "released"]
                    },
                }
            ):
                raise ValueError("opensandbox_legacy_mode_in_use")
        if removed_ids:
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            capacity = self._capacity_override or db["opensandbox_node_capacity"]
            binding = self._binding_override or db["user_sandbox_bindings"]
            for node_id in removed_ids:
                cap = await capacity.find_one({"_id": node_id})
                if cap and any(
                    reservation.get("allocation_state", "unknown")
                    not in {"released", "terminated", "destroyed", "not_found"}
                    for reservation in cap.get("reservations") or []
                ):
                    raise ValueError("opensandbox_node_in_use")
                if await binding.find_one(
                    {"node_id": node_id, "sandbox_state": {"$nin": ["terminated", "destroyed", "not_found", "released"]}}
                ):
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
            client = get_mongo_client()
            capacity = client[settings.MONGODB_DB]["opensandbox_node_capacity"]
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
        return StoredOpenSandboxNodes(update.mode, [{**n, "api_key": (node.api_key or existing.get(node.id, {}).get("api_key", "")) if not node.clear_api_key else ""} for n, node in zip(payload_nodes, update.nodes)], revision, utc_now(), updated_by)

    async def set_draining(self, node_id: str, draining: bool) -> bool:
        result = await self._collection().update_one({"_id": _CURRENT_ID, "nodes.id": node_id}, {"$set": {"nodes.$.draining": draining}})
        return bool(getattr(result, "matched_count", 0))

    async def remove(self, node_id: str) -> bool:
        """Remove only an unused node; callers must drain/disable first."""
        client = get_mongo_client() if self._capacity_override is None or self._binding_override is None else None
        db = client[settings.MONGODB_DB] if client is not None else None
        if db is None:
            assert self._capacity_override is not None and self._binding_override is not None
        capacity = self._capacity_override or db["opensandbox_node_capacity"]  # type: ignore[index]
        binding = self._binding_override or db["user_sandbox_bindings"]  # type: ignore[index]
        cap = await capacity.find_one({"_id": node_id})
        if cap and cap.get("reservations"):
            raise ValueError("opensandbox_node_in_use")
        if await binding.find_one(
            {
                "node_id": node_id,
                "sandbox_state": {"$nin": ["terminated", "destroyed", "not_found", "released"]},
            }
        ):
            raise ValueError("opensandbox_node_in_use")
        result = await self._collection().update_one({"_id": _CURRENT_ID, "nodes.id": node_id}, {"$pull": {"nodes": {"id": node_id}}})
        return bool(getattr(result, "modified_count", 0))


_storage: OpenSandboxNodeStorage | None = None


def get_opensandbox_node_storage() -> OpenSandboxNodeStorage:
    global _storage
    if _storage is None:
        _storage = OpenSandboxNodeStorage()
    return _storage
