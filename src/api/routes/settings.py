"""
Settings API router
"""

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_current_user_required, require_permissions
from src.infra.settings.service import SettingsService, get_settings_service
from src.kernel.config import settings
from src.kernel.schemas.opensandbox import OpenSandboxNodesUpdate
from src.kernel.schemas.setting import (
    SettingItem,
    SettingResetResponse,
    SettingsResponse,
    SettingUpdate,
    SettingUpdateResponse,
)
from src.kernel.schemas.user import TokenPayload
from src.kernel.schemas.wecom_network import (
    WeComNetworkBotResult,
    WeComNetworkConfigResponse,
    WeComNetworkConfigUpdate,
    WeComNetworkOperationResponse,
    WeComNetworkOperationStatus,
)

router = APIRouter()


@router.get("/opensandbox-nodes")
async def get_opensandbox_nodes(
    _: TokenPayload = Depends(require_permissions("settings:manage")),
):
    """Return managed OpenSandbox nodes with API keys redacted."""
    from src.infra.sandbox.node_storage import get_opensandbox_node_storage

    return (await get_opensandbox_node_storage().get_current()).to_response().model_dump(mode="json")


@router.put("/opensandbox-nodes")
async def update_opensandbox_nodes(
    data: "OpenSandboxNodesUpdate",
    user: TokenPayload = Depends(require_permissions("settings:manage")),
):
    """Replace the node list using optimistic revision concurrency."""
    from src.infra.sandbox.node_storage import get_opensandbox_node_storage

    try:
        stored = await get_opensandbox_node_storage().save(data, updated_by=user.sub)
    except ValueError as exc:
        if "revision_conflict" in str(exc):
            raise HTTPException(status_code=409, detail="opensandbox_nodes_revision_conflict") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from src.infra.sandbox.session_manager import reset_session_sandbox_manager

    reset_session_sandbox_manager()
    # Reuse the existing settings fan-out channel to reset manager caches on replicas.
    await SettingsService._publish_change("OPENSANDBOX_NODES", stored.revision)
    return stored.to_response().model_dump(mode="json")


@router.post("/opensandbox-nodes/{node_id}/probe")
async def probe_opensandbox_node(
    node_id: str,
    _: TokenPayload = Depends(require_permissions("settings:manage")),
):
    """Probe a node through its adapter without exposing credentials."""
    from src.infra.sandbox.node_storage import get_opensandbox_node_storage
    from src.infra.sandbox.session_manager import OpenSandboxSandboxAdapter

    current = await get_opensandbox_node_storage().get_current()
    node = next((item for item in current.nodes if item.get("id") == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="opensandbox_node_not_found")
    try:
        adapter = OpenSandboxSandboxAdapter(
            domain=node["domain"], api_key=node.get("api_key", ""), image=node.get("image", "ubuntu"),
            timeout=int(node.get("timeout", 3600)), work_dir=node.get("work_dir", "/root"),
            use_server_proxy=bool(node.get("use_server_proxy", True)),
        )
        from src.infra.async_utils import run_blocking_io

        latency_ms = await run_blocking_io(adapter.probe)
        state = "healthy"
        detail = None
    except Exception as exc:
        state = "unavailable"
        detail = type(exc).__name__
        latency_ms = None
    try:
        from src.infra.sandbox.capacity_storage import OpenSandboxCapacityStorage
        from src.infra.utils.datetime import utc_now

        await OpenSandboxCapacityStorage()._collection().update_one(
            {"_id": node_id},
            {
                "$set": {
                    "health_state": state,
                    "last_health_at": utc_now(),
                    "last_error": detail,
                },
                "$setOnInsert": {"reservations": [], "max_sandboxes": int(node.get("max_sandboxes", 1)), "enabled": bool(node.get("enabled", True))},
            },
            upsert=True,
        )
    except Exception:
        # Probe result remains useful even if the observability write fails.
        pass
    return {"node_id": node_id, "health_state": state, "latency_ms": round(latency_ms, 2) if latency_ms is not None else None, "detail": detail}


@router.post("/opensandbox-nodes/{node_id}/drain")
async def drain_opensandbox_node(
    node_id: str,
    draining: bool = True,
    _: TokenPayload = Depends(require_permissions("settings:manage")),
):
    from src.infra.sandbox.node_storage import get_opensandbox_node_storage

    if not await get_opensandbox_node_storage().set_draining(node_id, draining):
        raise HTTPException(status_code=404, detail="opensandbox_node_not_found")
    return {"node_id": node_id, "draining": draining}


@router.get("/", response_model=SettingsResponse)
async def get_settings(
    user: TokenPayload = Depends(get_current_user_required),
    service: SettingsService = Depends(get_settings_service),
):
    """Get settings (filtered by permission)"""
    # Check if user has settings:manage permission
    has_admin = "settings:manage" in (user.permissions or [])
    settings = await service.get_all(admin_mode=has_admin)
    return SettingsResponse(settings=settings)


@router.get("/wecom-network", response_model=WeComNetworkConfigResponse)
async def get_wecom_network_config(
    _: TokenPayload = Depends(require_permissions("settings:manage")),
):
    """Return deployment-level WeCom transport settings with secrets redacted."""
    from src.infra.agent.wecom.network_config import (
        get_wecom_network_config_storage,
    )

    stored = await get_wecom_network_config_storage().get_current()
    return stored.to_response()


@router.post("/wecom-network/test", response_model=WeComNetworkOperationResponse)
async def test_wecom_network_config(
    data: WeComNetworkConfigUpdate,
    _: TokenPayload = Depends(require_permissions("settings:manage")),
):
    """Probe the candidate WSS path without persisting or authenticating a bot."""
    from src.infra.agent.wecom.network import WeComNetworkTransport
    from src.infra.agent.wecom.network_config import (
        get_wecom_network_config_storage,
    )

    current = await get_wecom_network_config_storage().get_current()
    candidate = data.merge_secret(current.config.forward_proxy_password)
    operation_id = uuid.uuid4().hex
    try:
        await WeComNetworkTransport(candidate).probe_websocket()
        status = WeComNetworkOperationStatus.TEST_OK
        detail = None
    except Exception as exc:
        status = WeComNetworkOperationStatus.TEST_FAILED
        detail = getattr(exc, "reason_code", type(exc).__name__)
    response_config = WeComNetworkConfigResponse(
        **candidate.model_dump(exclude={"forward_proxy_password"}),
        has_forward_proxy_password=bool(candidate.forward_proxy_password),
        revision=current.revision,
        updated_at=current.updated_at,
        updated_by=current.updated_by,
    )
    return WeComNetworkOperationResponse(
        operation_id=operation_id,
        status=status,
        revision=current.revision,
        config=response_config,
        detail=detail,
    )


@router.put("/wecom-network", response_model=WeComNetworkOperationResponse)
async def update_wecom_network_config(
    data: WeComNetworkConfigUpdate,
    user: TokenPayload = Depends(require_permissions("settings:manage")),
):
    """Atomically save network settings, restart all bots, and roll back on total failure."""
    from src.infra.agent.config_storage import get_agent_config_storage
    from src.infra.agent.wecom.control import request_wecom_network_reload
    from src.infra.agent.wecom.network_config import (
        get_wecom_network_config_storage,
    )

    operation_id = uuid.uuid4().hex
    storage = get_wecom_network_config_storage()
    current = await storage.get_current()
    candidate = data.merge_secret(current.config.forward_proxy_password)
    try:
        _, saved = await storage.save_candidate(
            candidate,
            updated_by=user.sub,
            expected_revision=data.expected_revision,
        )
    except ValueError as exc:
        status_code = 409 if "revision_conflict" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    configured_bots = await get_agent_config_storage().get_all_persona_wecom_configs_raw()
    raw_results = await request_wecom_network_reload(
        saved.revision,
        requested_by=user.sub,
    )
    results = [WeComNetworkBotResult.model_validate(item) for item in raw_results]
    connected_presets = {
        item.preset_id for item in results if item.state == "connected" and item.preset_id
    }

    if configured_bots and not connected_presets:
        rolled_back = await storage.rollback(saved.revision, updated_by=user.sub)
        if rolled_back is None:
            raise HTTPException(
                status_code=409,
                detail="wecom_network_config_changed_before_rollback",
            )
        await request_wecom_network_reload(
            rolled_back.revision,
            requested_by=user.sub,
        )
        return WeComNetworkOperationResponse(
            operation_id=operation_id,
            status=WeComNetworkOperationStatus.ROLLED_BACK,
            revision=rolled_back.revision,
            config=rolled_back.to_response(),
            results=results,
            detail="all_enabled_bots_failed; previous configuration restored",
        )

    await storage.promote(saved.revision)
    status = (
        WeComNetworkOperationStatus.SAVED_UNVERIFIED
        if not configured_bots
        else WeComNetworkOperationStatus.CONNECTED
        if len(connected_presets) >= len(configured_bots)
        else WeComNetworkOperationStatus.PARTIAL_FAILURE
    )
    return WeComNetworkOperationResponse(
        operation_id=operation_id,
        status=status,
        revision=saved.revision,
        config=saved.to_response(),
        results=results,
    )


@router.get("/{key}", response_model=SettingItem)
async def get_setting(
    key: str,
    _: TokenPayload = Depends(require_permissions("settings:manage")),
    service: SettingsService = Depends(get_settings_service),
):
    """Get single setting by key"""
    setting = await service._storage.get(key)
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found")
    return setting


@router.put("/{key}", response_model=SettingUpdateResponse)
async def update_setting(
    key: str,
    data: SettingUpdate,
    user: TokenPayload = Depends(require_permissions("settings:manage")),
    service: SettingsService = Depends(get_settings_service),
):
    """Update a setting (requires settings:manage permission)"""
    try:
        setting = await service.set(key, data.value, user.sub)
        if not setting:
            raise HTTPException(status_code=404, detail="Setting not found")

        requires_restart = SettingsService.requires_restart(key)

        return SettingUpdateResponse(
            setting=setting,
            message=(
                "Setting updated. Server restart required to take effect."
                if requires_restart
                else "Setting updated successfully."
            ),
            requires_restart=requires_restart,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/init", response_model=SettingResetResponse)
async def init_settings_from_env(
    _: TokenPayload = Depends(require_permissions("settings:manage")),
    service: SettingsService = Depends(get_settings_service),
):
    """Import settings from .env to database (only unset values)"""
    count = await service.init_from_env()
    return SettingResetResponse(
        message=f"Imported {count} settings from environment",
        reset_count=count,
    )


@router.post("/reset", response_model=SettingResetResponse)
async def reset_all_settings(
    _: TokenPayload = Depends(require_permissions("settings:manage")),
    service: SettingsService = Depends(get_settings_service),
):
    """Reset all settings to default values"""
    count = await service.reset()
    return SettingResetResponse(
        message="All settings reset to defaults",
        reset_count=count,
    )


@router.post("/reset/{key}", response_model=SettingResetResponse)
async def reset_setting(
    key: str,
    _: TokenPayload = Depends(require_permissions("settings:manage")),
    service: SettingsService = Depends(get_settings_service),
):
    """Reset single setting to default value"""
    count = await service.reset(key)
    if count == 0:
        raise HTTPException(status_code=404, detail="Setting not found")
    return SettingResetResponse(
        message=f"Setting {key} reset to default",
        reset_count=count,
    )


@router.get("/dify-kb/datasets")
async def list_dify_knowledge_bases(
    _: TokenPayload = Depends(require_permissions("settings:manage")),
) -> dict[str, Any]:
    """List Dify knowledge bases to populate the persona KB picker.

    Proxies Dify's ``GET /datasets`` and returns only the fields the picker
    needs. Only available when the Dify KB feature is enabled and the
    connection settings are configured.
    """
    if not (settings.DIFY_KB_ENABLED and settings.DIFY_KB_BASE_URL and settings.DIFY_KB_API_KEY):
        raise HTTPException(
            status_code=400, detail="Dify knowledge base is not enabled or configured"
        )

    base_url = str(settings.DIFY_KB_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.DIFY_KB_API_KEY}"}

    datasets: list[dict[str, Any]] = []
    page = 1
    limit = 20
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while True:
                response = await client.get(
                    f"{base_url}/datasets",
                    headers=headers,
                    params={"page": page, "limit": limit},
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list):
                    break
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    datasets.append(
                        {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "description": item.get("description"),
                            "document_count": item.get("document_count"),
                        }
                    )
                if not payload.get("has_more"):
                    break
                page += 1
                if page > 200:  # hard safety cap
                    break
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach Dify: {exc}") from exc

    return {"datasets": datasets}
