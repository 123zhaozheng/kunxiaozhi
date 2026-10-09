"""Persona (expert) helpers for scheduled tasks: validation and display briefs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from src.infra.logging import get_logger
from src.infra.persona_preset.manager import PersonaPresetManager
from src.kernel.exceptions import AuthorizationError, NotFoundError
from src.kernel.schemas.persona_preset import PersonaPreset
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

PERSONA_ADMIN_PERMISSION = "persona_preset:admin"
PERSONA_UNAVAILABLE_RUN_MESSAGE = "所选专家已被删除、下架或你已无权使用，请编辑任务更换专家"


class ScheduledTaskPersonaError(ValueError):
    """User-facing error (Chinese): the chosen persona is missing or not usable."""

    def __init__(self, message: str = "所选专家不存在或你无权使用") -> None:
        super().__init__(message)


def _persona_manager() -> PersonaPresetManager:
    return PersonaPresetManager()


def _is_persona_admin(user: TokenPayload) -> bool:
    return PERSONA_ADMIN_PERMISSION in (user.permissions or [])


async def ensure_persona_usable(preset_id: str, actor: TokenPayload) -> PersonaPreset:
    """Check visibility with the same rules as chat, without bumping usage counters."""
    try:
        return await _persona_manager().get_preset(
            preset_id, user_id=actor.sub, is_admin=_is_persona_admin(actor)
        )
    except (NotFoundError, AuthorizationError) as exc:
        raise ScheduledTaskPersonaError() from exc


async def persona_briefs(
    preset_ids: Iterable[str], *, user_id: str, is_admin: bool
) -> dict[str, dict[str, Any]]:
    """Resolve ``{id: {name, avatar, available}}`` for display.

    ``available`` is None when the lookup itself failed (status unknown).
    """
    ids = list(dict.fromkeys(i for i in preset_ids if i))
    if not ids:
        return {}
    manager = _persona_manager()

    async def _one(preset_id: str) -> dict[str, Any]:
        try:
            preset = await manager.get_preset(preset_id, user_id=user_id, is_admin=is_admin)
        except (NotFoundError, AuthorizationError):
            return {"name": None, "avatar": None, "available": False}
        except Exception as exc:  # noqa: BLE001 - display only, never fail the listing
            logger.warning("[ScheduledTask] persona lookup failed id=%s: %s", preset_id, exc)
            return {"name": None, "avatar": None, "available": None}
        return {"name": preset.name, "avatar": preset.avatar, "available": True}

    results = await asyncio.gather(*(_one(i) for i in ids))
    return dict(zip(ids, results, strict=True))
