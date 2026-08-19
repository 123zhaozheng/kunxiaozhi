"""Permissioned OpenSandbox inventory and lifecycle actions."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import require_permissions
from src.kernel.schemas.opensandbox import OpenSandboxInventoryResponse
from src.kernel.schemas.user import TokenPayload

router = APIRouter()

_ACTION_STATES = {
    "pause": {"running", "started"},
    "resume": {"paused", "stopped", "archived"},
    "renew": {"running", "started", "paused", "stopped", "archived", "unknown"},
    "terminate": {
        "running",
        "started",
        "paused",
        "stopped",
        "archived",
        "creating",
        "unknown",
    },
}


def available_actions(state: str, *, has_sandbox_id: bool = True) -> dict[str, bool]:
    """Return the canonical state/action contract used by API and UI."""
    normalized = state.lower()
    return {
        action: has_sandbox_id and normalized in allowed_states
        for action, allowed_states in _ACTION_STATES.items()
    }


def _user_object_ids(user_ids: list[str]) -> list[Any]:
    from bson import ObjectId
    from bson.errors import InvalidId

    object_ids: list[Any] = []
    for user_id in user_ids:
        if not user_id:
            continue
        try:
            object_ids.append(ObjectId(str(user_id)))
        except (InvalidId, TypeError, ValueError):
            continue
    return object_ids


async def load_usernames(user_ids: list[str]) -> dict[str, str]:
    """Batch-load employee usernames for LambChat-managed bindings."""
    object_ids = _user_object_ids(user_ids)
    if not object_ids:
        return {}
    try:
        from src.infra.storage.mongodb import get_mongo_client
        from src.kernel.config import settings

        mapping: dict[str, str] = {}
        cursor = get_mongo_client()[settings.MONGODB_DB]["users"].find(
            {"_id": {"$in": object_ids}},
            {"_id": 1, "username": 1},
        )
        async for document in cursor:
            user_id = str(document.get("_id") or "")
            username = str(document.get("username") or "").strip()
            if user_id and username:
                mapping[user_id] = username
        return mapping
    except Exception:
        return {}


async def user_ids_matching_username(search: str) -> list[str]:
    """Resolve usernames (employee ids) so inventory search is not ObjectId-only."""
    pattern = re.escape(search.strip())
    if not pattern:
        return []
    try:
        from src.infra.storage.mongodb import get_mongo_client
        from src.kernel.config import settings

        cursor = (
            get_mongo_client()[settings.MONGODB_DB]["users"]
            .find({"username": {"$regex": pattern, "$options": "i"}}, {"_id": 1})
            .limit(50)
        )
        return [str(document.get("_id")) async for document in cursor if document.get("_id")]
    except Exception:
        return []


async def _inventory(
    *, skip: int, limit: int, node_id: str | None, state: str | None, search: str | None
) -> tuple[list[dict], int]:
    """Build inventory from multi-node managed bindings only.

    Legacy / single-node compatibility keeps scalar OpenSandbox settings, but
    Admin inventory stays empty there: those bindings have no node affinity and
    would otherwise surface stale ``running`` rows that cannot be probed.

    Multi-node rows still come from LambChat bindings that already record a
    ``node_id``; per-row provider probing is best effort and never invents
    external sandboxes.
    """
    from src.infra.async_utils import run_blocking_io
    from src.infra.sandbox.capacity_storage import OpenSandboxCapacityStorage
    from src.infra.sandbox.node_storage import get_opensandbox_node_storage
    from src.infra.sandbox.session_manager import OpenSandboxSandboxAdapter
    from src.kernel.schemas.opensandbox import OpenSandboxMode

    configured = await get_opensandbox_node_storage().get_current()
    if configured.mode == OpenSandboxMode.LEGACY:
        return [], 0

    storage = OpenSandboxCapacityStorage()
    collection = storage._bindings()
    # Require node affinity so pre-multi-node documents never appear as live inventory.
    query: dict = (
        {"node_id": node_id} if node_id else {"node_id": {"$exists": True, "$nin": [None, ""]}}
    )
    if state:
        query["sandbox_state"] = state
    if search:
        clauses: list[dict] = [
            {"sandbox_id": {"$regex": search, "$options": "i"}},
            {"user_id": {"$regex": search, "$options": "i"}},
        ]
        matching_user_ids = await user_ids_matching_username(search)
        if matching_user_ids:
            clauses.append({"user_id": {"$in": matching_user_ids}})
        query["$or"] = clauses
    total = await collection.count_documents(query) if hasattr(collection, "count_documents") else 0
    cursor = collection.find(query).sort([("updated_at", -1), ("_id", 1)]).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit) if hasattr(cursor, "to_list") else []
    usernames = await load_usernames(
        [str(doc.get("user_id")) for doc in docs if doc.get("user_id")]
    )
    items = []
    node_map = {str(node.get("id")): node for node in configured.nodes}
    for doc in docs:
        current_state = str(doc.get("sandbox_state", "unknown"))
        provider_state = current_state
        node = node_map.get(str(doc.get("node_id", "")))
        sandbox_id = str(doc.get("sandbox_id", ""))
        if node and sandbox_id and current_state not in {"paused", "stopped", "archived"}:
            try:
                adapter = OpenSandboxSandboxAdapter(
                    domain=str(node.get("domain", "")),
                    api_key=str(node.get("api_key", "")),
                    image=str(node.get("image", "ubuntu")),
                    timeout=int(node.get("timeout", 3600)),
                    work_dir=str(node.get("work_dir", "/root")),
                    use_server_proxy=bool(node.get("use_server_proxy", True)),
                    sync_settings=False,
                )
                provider = await run_blocking_io(adapter.get_sandbox, sandbox_id)
                if provider is not None:
                    provider_state = str(
                        (await run_blocking_io(adapter.get_sandbox_info, provider)).get(
                            "state", "unknown"
                        )
                    )
                else:
                    provider_state = "not_found"
                    released_binding = await storage.release_binding(
                        str(doc.get("user_id", "")),
                        str(doc.get("node_id", "")),
                        str(doc.get("reservation_id", "")),
                        str(doc.get("allocation_token", "")),
                    )
                    if released_binding:
                        await storage.release(
                            str(doc.get("node_id", "")),
                            str(doc.get("reservation_id", "")),
                            allocation_token=str(doc.get("allocation_token", "")),
                        )
            except Exception:
                provider_state = "unknown"
        actions = available_actions(current_state, has_sandbox_id=bool(sandbox_id))
        user_id = str(doc.get("user_id") or "") or None
        items.append(
            {
                "node_id": doc.get("node_id", ""),
                "sandbox_id": sandbox_id,
                "state": provider_state,
                "managed": True,
                "user_id": user_id,
                "username": usernames.get(user_id or ""),
                "binding_state": doc.get("allocation_state"),
                "created_at": doc.get("sandbox_created_at"),
                "last_used_at": doc.get("sandbox_last_used_at"),
                "expires_at": doc.get("lease_expires_at"),
                "actions": actions,
            }
        )
    return items, total


@router.get("/sandboxes", response_model=OpenSandboxInventoryResponse)
async def list_opensandbox_sandboxes(
    skip: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=200),
    node_id: str | None = None,
    state: str | None = None,
    search: str | None = None,
    _: TokenPayload = Depends(require_permissions("settings:manage")),
):
    items, total = await _inventory(
        skip=skip, limit=limit, node_id=node_id, state=state, search=search
    )
    return OpenSandboxInventoryResponse(items=items, total=total, skip=skip, limit=limit)


async def _action(node_id: str, sandbox_id: str, action: str) -> dict:
    from src.infra.async_utils import run_blocking_io
    from src.infra.sandbox.capacity_storage import OpenSandboxCapacityStorage
    from src.infra.sandbox.node_storage import get_opensandbox_node_storage
    from src.infra.sandbox.session_manager import OpenSandboxSandboxAdapter

    storage = OpenSandboxCapacityStorage()
    binding = await storage._bindings().find_one({"node_id": node_id, "sandbox_id": sandbox_id})
    if not binding:
        raise HTTPException(status_code=404, detail="opensandbox_sandbox_not_found")
    binding_state = str(
        binding.get("sandbox_state") or binding.get("allocation_state") or "unknown"
    )
    if not available_actions(
        binding_state,
        has_sandbox_id=bool(sandbox_id),
    ).get(action, False):
        raise HTTPException(status_code=409, detail="opensandbox_action_not_allowed")
    nodes = await get_opensandbox_node_storage().get_current()
    node = next((item for item in nodes.nodes if item.get("id") == node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail="opensandbox_node_not_found")
    adapter = OpenSandboxSandboxAdapter(
        domain=str(node.get("domain", "")),
        api_key=str(node.get("api_key", "")),
        image=str(node.get("image", "ubuntu")),
        timeout=int(node.get("timeout", 3600)),
        work_dir=str(node.get("work_dir", "/root")),
        use_server_proxy=bool(node.get("use_server_proxy", True)),
        sync_settings=False,
    )
    try:
        if action == "resume":
            provider = None
        elif action in {"renew", "terminate"} and binding_state not in {
            "running",
            "started",
        }:
            provider = await run_blocking_io(adapter.get_sandbox_unchecked, sandbox_id)
        else:
            provider = await run_blocking_io(adapter.get_sandbox, sandbox_id)
        if provider is None:
            if action == "resume":
                provider = await run_blocking_io(adapter.resume_sandbox_by_id, sandbox_id)
            else:
                # A provider-confirmed 404 is terminal evidence. Reconcile both
                # records before reporting the row as gone.
                released_binding = await storage.release_binding(
                    str(binding.get("user_id", "")),
                    node_id,
                    str(binding.get("reservation_id", "")),
                    str(binding.get("allocation_token", "")),
                )
                if not released_binding:
                    raise HTTPException(status_code=500, detail="opensandbox_bookkeeping_failed")
                await storage.release(
                    node_id,
                    str(binding.get("reservation_id", "")),
                    allocation_token=str(binding.get("allocation_token", "")),
                )
                raise HTTPException(status_code=404, detail="opensandbox_sandbox_not_found")
        if action == "terminate":
            await run_blocking_io(adapter.kill_sandbox, provider)
        elif action == "pause":
            await run_blocking_io(adapter.pause_sandbox, provider)
        elif action == "resume":
            # Resume-by-id above returns the new connected provider object.
            pass
        elif action == "renew":
            await run_blocking_io(adapter.extend_timeout, provider, adapter._timeout)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="opensandbox_provider_action_failed") from exc
    reservation_id = str(binding.get("reservation_id", ""))
    allocation_token = str(binding.get("allocation_token", ""))
    binding_query = {
        "_id": binding.get("_id"),
        "allocation_token": allocation_token,
    }
    if action == "terminate":
        binding_updated = await storage.release_binding(
            str(binding.get("user_id", "")), node_id, reservation_id, allocation_token
        )
        reservation_updated = await storage.release(
            node_id, reservation_id, allocation_token=allocation_token
        )
    elif action == "pause":
        binding_result = await storage._bindings().update_one(
            binding_query, {"$set": {"sandbox_state": "paused", "allocation_state": "paused"}}
        )
        binding_updated = bool(getattr(binding_result, "modified_count", 0))
        reservation_updated = await storage.transition(
            node_id, reservation_id, allocation_token, "paused", sandbox_state="paused"
        )
    elif action == "resume":
        binding_result = await storage._bindings().update_one(
            binding_query, {"$set": {"sandbox_state": "running", "allocation_state": "allocated"}}
        )
        binding_updated = bool(getattr(binding_result, "modified_count", 0))
        reservation_updated = await storage.transition(
            node_id, reservation_id, allocation_token, "allocated", sandbox_state="running"
        )
    elif action == "renew":
        from datetime import timedelta

        from src.infra.utils.datetime import utc_now

        timeout = int(node.get("timeout", 3600))
        expires_at = utc_now() + timedelta(seconds=timeout)
        binding_result = await storage._bindings().update_one(
            binding_query,
            {"$set": {"lease_expires_at": expires_at}},
        )
        binding_updated = bool(getattr(binding_result, "modified_count", 0))
        reservation_updated = await storage.transition(
            node_id,
            reservation_id,
            allocation_token,
            binding.get("allocation_state", "allocated"),
            lease_expires_at=expires_at,
        )
    else:  # pragma: no cover - guarded by route literals
        raise HTTPException(status_code=400, detail="opensandbox_action_not_allowed")
    if not binding_updated or not reservation_updated:
        raise HTTPException(status_code=500, detail="opensandbox_bookkeeping_failed")
    state = {
        "terminate": "terminated",
        "pause": "paused",
        "resume": "running",
        "renew": binding_state,
    }[action]
    return {"node_id": node_id, "sandbox_id": sandbox_id, "action": action, "state": state}


@router.post("/sandboxes/{node_id}/{sandbox_id}/pause")
async def pause_sandbox(
    node_id: str, sandbox_id: str, _: TokenPayload = Depends(require_permissions("settings:manage"))
):
    return await _action(node_id, sandbox_id, "pause")


@router.post("/sandboxes/{node_id}/{sandbox_id}/resume")
async def resume_sandbox(
    node_id: str, sandbox_id: str, _: TokenPayload = Depends(require_permissions("settings:manage"))
):
    return await _action(node_id, sandbox_id, "resume")


@router.post("/sandboxes/{node_id}/{sandbox_id}/renew")
async def renew_sandbox(
    node_id: str, sandbox_id: str, _: TokenPayload = Depends(require_permissions("settings:manage"))
):
    return await _action(node_id, sandbox_id, "renew")


@router.delete("/sandboxes/{node_id}/{sandbox_id}")
async def terminate_sandbox(
    node_id: str,
    sandbox_id: str,
    confirm: bool = Query(False),
    _: TokenPayload = Depends(require_permissions("settings:manage")),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="opensandbox_terminate_confirmation_required")
    return await _action(node_id, sandbox_id, "terminate")
