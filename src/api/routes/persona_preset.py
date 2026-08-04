"""Persona preset routes."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from src.api.deps import require_permissions
from src.infra.agent.config_storage import get_agent_config_storage
from src.infra.agent.wecom.binding import WeComNotifyBindingStorage
from src.infra.logging import get_logger
from src.infra.persona_preset.manager import PersonaPresetManager, PersonaSkillBindingError
from src.kernel.exceptions import AuthorizationError, NotFoundError
from src.kernel.schemas.persona_preset import (
    PersonaPreset,
    PersonaPresetCreate,
    PersonaPresetListResponse,
    PersonaPresetPreferenceUpdate,
    PersonaPresetScope,
    PersonaPresetSnapshot,
    PersonaPresetUpdate,
)
from src.kernel.schemas.user import TokenPayload
from src.kernel.schemas.wecom import PersonaWeComConfig, PersonaWeComConfigCreate
from src.kernel.schemas.wecom_notify import (
    NotifyTargetItem,
    NotifyTargetsResponse,
    NotifyTargetsUpdate,
)
from src.kernel.schemas.wecom_status import (
    WeComConnectionStatus,
    WeComStatusBatchRequest,
    WeComStatusBatchResponse,
)

router = APIRouter()
logger = get_logger(__name__)


def _is_admin(user: TokenPayload) -> bool:
    return "persona_preset:admin" in set(user.permissions or [])


def _manager() -> PersonaPresetManager:
    return PersonaPresetManager()


async def _wecom_configured_preset_ids() -> set[str]:
    storage = get_agent_config_storage()
    return await storage.list_persona_ids_with_wecom()


async def _attach_has_wecom(presets: list[PersonaPreset]) -> list[PersonaPreset]:
    if not presets:
        return presets
    configured = await _wecom_configured_preset_ids()
    return [
        p.model_copy(
            update={"has_wecom": p.id in configured and p.scope == PersonaPresetScope.GLOBAL}
        )
        for p in presets
    ]


async def _attach_has_wecom_one(preset: PersonaPreset) -> PersonaPreset:
    if preset.scope != PersonaPresetScope.GLOBAL:
        return preset.model_copy(update={"has_wecom": False})
    storage = get_agent_config_storage()
    has = await storage.preset_has_wecom(preset.id)
    return preset.model_copy(update={"has_wecom": has})


@router.get("/", response_model=PersonaPresetListResponse)
async def list_persona_presets(
    scope: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    favorite: bool | None = None,
    pinned: bool | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: TokenPayload = Depends(require_permissions("persona_preset:read")),
):
    """List visible persona presets."""
    presets = await _manager().list_presets(
        user_id=user.sub,
        is_admin=_is_admin(user),
        scope=scope,
        status=status,
        tag=tag,
        q=q,
        favorite=favorite,
        pinned=pinned,
        skip=skip,
        limit=limit,
    )
    total = await _manager().count_presets(
        user_id=user.sub,
        is_admin=_is_admin(user),
        scope=scope,
        status=status,
        tag=tag,
        q=q,
        favorite=favorite,
        pinned=pinned,
        skip=skip,
        limit=limit,
    )
    return PersonaPresetListResponse(
        presets=await _attach_has_wecom(presets),
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=PersonaPreset)
async def create_persona_preset(
    preset_data: PersonaPresetCreate,
    user: TokenPayload = Depends(require_permissions("persona_preset:write")),
):
    """Create a user preset or, for admins, a global preset."""
    try:
        return await _manager().create_preset(
            preset_data,
            user_id=user.sub,
            is_admin=_is_admin(user),
        )
    except AuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except PersonaSkillBindingError as e:
        raise HTTPException(status_code=409, detail=e.as_detail())


@router.post("/batch", response_model=list[PersonaPreset])
async def batch_create_persona_presets(
    items: Annotated[list[PersonaPresetCreate], Body(max_length=100)],
    user: TokenPayload = Depends(require_permissions("persona_preset:write")),
):
    """Batch create persona presets."""
    try:
        return await _manager().batch_create_presets(
            items,
            user_id=user.sub,
            is_admin=_is_admin(user),
        )
    except PersonaSkillBindingError as e:
        raise HTTPException(status_code=409, detail=e.as_detail())


@router.post("/wecom/status", response_model=WeComStatusBatchResponse)
async def batch_wecom_connection_status(
    body: WeComStatusBatchRequest,
    _: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """Batch read WeCom connection status for plaza polling."""
    from src.infra.agent.wecom.status import resolve_wecom_status

    storage = get_agent_config_storage()
    statuses: list[WeComConnectionStatus] = []
    for preset_id in body.preset_ids:
        has_wecom = await storage.preset_has_wecom(preset_id)
        raw = await resolve_wecom_status(preset_id, has_wecom=has_wecom)
        statuses.append(WeComConnectionStatus(**raw))
    return WeComStatusBatchResponse(statuses=statuses)


@router.get("/{preset_id}", response_model=PersonaPreset)
async def get_persona_preset(
    preset_id: str,
    user: TokenPayload = Depends(require_permissions("persona_preset:read")),
):
    """Get a visible persona preset."""
    try:
        return await _attach_has_wecom_one(
            await _manager().get_preset(
                preset_id,
                user_id=user.sub,
                is_admin=_is_admin(user),
            )
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="persona_preset_not_found")


@router.put("/{preset_id}", response_model=PersonaPreset)
async def update_persona_preset(
    preset_id: str,
    preset_data: PersonaPresetUpdate,
    user: TokenPayload = Depends(require_permissions("persona_preset:write")),
):
    """Update an editable persona preset."""
    try:
        return await _manager().update_preset(
            preset_id,
            preset_data,
            user_id=user.sub,
            is_admin=_is_admin(user),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="persona_preset_not_found")
    except AuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except PersonaSkillBindingError as e:
        raise HTTPException(status_code=409, detail=e.as_detail())


@router.delete("/{preset_id}")
async def delete_persona_preset(
    preset_id: str,
    user: TokenPayload = Depends(require_permissions("persona_preset:write")),
):
    """Delete an editable persona preset."""
    try:
        await _manager().delete_preset(
            preset_id,
            user_id=user.sub,
            is_admin=_is_admin(user),
        )
        return {"status": "deleted"}
    except NotFoundError:
        raise HTTPException(status_code=404, detail="persona_preset_not_found")
    except AuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{preset_id}/copy", response_model=PersonaPreset)
async def copy_persona_preset(
    preset_id: str,
    user: TokenPayload = Depends(require_permissions("persona_preset:write")),
):
    """Copy a visible preset into the current user's private presets."""
    try:
        return await _manager().copy_preset(
            preset_id,
            user_id=user.sub,
            is_admin=_is_admin(user),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="persona_preset_not_found")


@router.post("/{preset_id}/use", response_model=PersonaPresetSnapshot)
async def use_persona_preset(
    preset_id: str,
    user: TokenPayload = Depends(require_permissions("persona_preset:read")),
):
    """Resolve a persona preset into a runtime snapshot."""
    try:
        return await _manager().use_preset(
            preset_id,
            user_id=user.sub,
            is_admin=_is_admin(user),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="persona_preset_not_found")


@router.patch("/{preset_id}/preference", response_model=PersonaPreset)
async def update_persona_preset_preference(
    preset_id: str,
    preference: PersonaPresetPreferenceUpdate,
    user: TokenPayload = Depends(require_permissions("persona_preset:read")),
):
    """Update the current user's favorite/pinned state for a visible preset."""
    try:
        return await _attach_has_wecom_one(
            await _manager().update_preference(
                preset_id,
                user_id=user.sub,
                is_admin=_is_admin(user),
                is_favorite=preference.is_favorite,
                is_pinned=preference.is_pinned,
            )
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="persona_preset_not_found")


# ============================================
# WeCom 配置
# ============================================


async def _validate_global_preset(preset_id: str) -> PersonaPreset:
    """Validate that a preset exists and has global scope."""
    try:
        preset = await _manager().get_preset(
            preset_id,
            user_id="",
            is_admin=True,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="persona_preset_not_found")
    if preset.scope != PersonaPresetScope.GLOBAL:
        raise HTTPException(
            status_code=400,
            detail="wecom_config_requires_global_preset",
        )
    return preset


@router.get("/{preset_id}/wecom/status", response_model=WeComConnectionStatus)
async def get_persona_wecom_connection_status(
    preset_id: str,
    _: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """Get live WeCom WebSocket connection status for a global persona preset."""
    from src.infra.agent.wecom.status import resolve_wecom_status

    await _validate_global_preset(preset_id)
    storage = get_agent_config_storage()
    has_wecom = await storage.preset_has_wecom(preset_id)
    raw = await resolve_wecom_status(preset_id, has_wecom=has_wecom)
    return WeComConnectionStatus(**raw)


@router.post("/{preset_id}/wecom/reconnect", response_model=WeComConnectionStatus)
async def reconnect_persona_wecom(
    preset_id: str,
    user: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """Restart WeCom bot for this preset (reload_preset)."""
    from src.infra.agent.wecom.control import request_wecom_reload
    from src.infra.agent.wecom.status import resolve_wecom_status

    await _validate_global_preset(preset_id)
    storage = get_agent_config_storage()
    if not await storage.preset_has_wecom(preset_id):
        raise HTTPException(status_code=404, detail="wecom_config_not_found")

    reloaded = await request_wecom_reload(preset_id, requested_by=user.sub)
    if not reloaded:
        raise HTTPException(status_code=503, detail="wecom_reconnect_not_executed_on_this_node")

    raw = await resolve_wecom_status(preset_id, has_wecom=True)
    return WeComConnectionStatus(**raw)


@router.get("/{preset_id}/wecom", response_model=PersonaWeComConfig)
async def get_persona_wecom_config(
    preset_id: str,
    _: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """获取 persona preset 的企业微信配置"""
    await _validate_global_preset(preset_id)

    storage = get_agent_config_storage()
    config = await storage.get_persona_wecom_config(preset_id)
    if not config:
        raise HTTPException(status_code=404, detail="wecom_config_not_found")

    return config


@router.put("/{preset_id}/wecom", response_model=PersonaWeComConfig)
async def set_persona_wecom_config(
    preset_id: str,
    config_data: PersonaWeComConfigCreate,
    _: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """创建或更新 persona preset 的企业微信配置"""
    await _validate_global_preset(preset_id)

    storage = get_agent_config_storage()
    config = await storage.set_persona_wecom_config(
        preset_id=preset_id,
        aibotid=config_data.aibotid,
        secret=config_data.secret,
        stream_reply=config_data.stream_reply,
        send_thinking_message=config_data.send_thinking_message,
        segmented_reply=config_data.segmented_reply,
        segment_target_chars=config_data.segment_target_chars,
        session_ttl_hours=config_data.session_ttl_hours,
    )

    # Notify the embedded or external WeCom runtime to reload this preset's bot.
    try:
        from src.infra.agent.wecom.control import request_wecom_reload

        await request_wecom_reload(
            preset_id,
            wait_for_result=False,
        )
    except Exception as e:
        logger.warning("Failed to reload WeCom bot for preset %s: %s", preset_id, e)

    return config


@router.get("/{preset_id}/wecom/notify-targets", response_model=NotifyTargetsResponse)
async def get_persona_wecom_notify_targets(
    preset_id: str,
    _: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """获取 persona preset 的企业微信通知目标（含绑定状态）"""
    await _validate_global_preset(preset_id)

    storage = get_agent_config_storage()
    config = await storage.get_persona_wecom_config(preset_id)
    if not config:
        raise HTTPException(status_code=404, detail="wecom_config_not_found")

    targets = config.feedback_notify_targets or []
    bound = await WeComNotifyBindingStorage().list_bound(config.aibotid, targets)
    return NotifyTargetsResponse(
        targets=[NotifyTargetItem(username=t, bound=t in bound) for t in targets]
    )


@router.put("/{preset_id}/wecom/notify-targets", response_model=NotifyTargetsResponse)
async def update_persona_wecom_notify_targets(
    preset_id: str,
    data: NotifyTargetsUpdate,
    _: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """全量更新 persona preset 的企业微信通知目标（含绑定状态）"""
    await _validate_global_preset(preset_id)

    storage = get_agent_config_storage()
    config = await storage.get_persona_wecom_config(preset_id)
    if not config:
        raise HTTPException(status_code=404, detail="wecom_config_not_found")

    config = await storage.set_persona_wecom_config(
        preset_id=preset_id,
        aibotid=config.aibotid,
        feedback_notify_targets=data.targets,
    )

    targets = config.feedback_notify_targets or []
    bound = await WeComNotifyBindingStorage().list_bound(config.aibotid, targets)
    return NotifyTargetsResponse(
        targets=[NotifyTargetItem(username=t, bound=t in bound) for t in targets]
    )


@router.delete("/{preset_id}/wecom")
async def delete_persona_wecom_config(
    preset_id: str,
    _: TokenPayload = Depends(require_permissions("channel:manage")),
):
    """删除 persona preset 的企业微信配置"""
    await _validate_global_preset(preset_id)

    storage = get_agent_config_storage()
    deleted = await storage.delete_persona_wecom_config(preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="wecom_config_not_found")

    # Notify the embedded or external WeCom runtime to reload this preset's bot.
    try:
        from src.infra.agent.wecom.control import request_wecom_reload

        await request_wecom_reload(
            preset_id,
            wait_for_result=False,
        )
    except Exception as e:
        logger.warning("Failed to reload WeCom bot for preset %s after delete: %s", preset_id, e)

    return {"message": "企业微信配置已删除"}
