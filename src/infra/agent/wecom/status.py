"""
Redis-backed WeCom connection status per persona preset.

Written by WeComBot on state changes; read by status API for channel:manage users.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from src.infra.agent.wecom.state import ConnectionState
from src.infra.logging import get_logger
from src.infra.storage.redis import get_redis_client

logger = get_logger(__name__)

WECOM_STATUS_KEY_PREFIX = "wecom:status:"
WECOM_STATUS_TTL_SECONDS = 7 * 24 * 3600


class WeComStatusReasonCode(str, Enum):
    REPLACED = "replaced"
    RECONNECT_EXHAUSTED = "reconnect_exhausted"
    AUTH_FAILED = "auth_failed"
    LEASE_LOST = "lease_lost"
    DISCONNECTED = "disconnected"


def wecom_status_redis_key(preset_id: str) -> str:
    return f"{WECOM_STATUS_KEY_PREFIX}{preset_id}"


def map_error_to_reason_code(error: BaseException) -> WeComStatusReasonCode:
    """Map SDK / application errors to API reason_code."""
    name = type(error).__name__
    if name == "WSReconnectExhaustedError":
        return WeComStatusReasonCode.RECONNECT_EXHAUSTED
    if name == "WSAuthFailureError":
        return WeComStatusReasonCode.AUTH_FAILED
    module = getattr(type(error), "__module__", "") or ""
    if module.startswith("wecom_aibot_sdk"):
        if "ReconnectExhausted" in name:
            return WeComStatusReasonCode.RECONNECT_EXHAUSTED
        if "AuthFailure" in name:
            return WeComStatusReasonCode.AUTH_FAILED
    message = str(error).lower()
    if "authentication failed" in message or ("auth" in message and "fail" in message):
        return WeComStatusReasonCode.AUTH_FAILED
    return WeComStatusReasonCode.DISCONNECTED


def connection_state_to_api_state(state: ConnectionState) -> str:
    return state.value


async def write_wecom_status(
    preset_id: str,
    *,
    state: ConnectionState,
    reason_code: WeComStatusReasonCode | str | None = None,
    reason_detail: str | None = None,
    node_id: str | None = None,
    aibotid: str | None = None,
) -> None:
    if not preset_id:
        return
    payload: dict[str, Any] = {
        "preset_id": preset_id,
        "state": connection_state_to_api_state(state),
        "reason_code": reason_code.value if isinstance(reason_code, WeComStatusReasonCode) else reason_code,
        "reason_detail": reason_detail,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if node_id:
        payload["node_id"] = node_id
    if aibotid:
        payload["aibotid"] = aibotid
    try:
        redis = get_redis_client()
        await redis.set(
            wecom_status_redis_key(preset_id),
            json.dumps(payload),
            ex=WECOM_STATUS_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("Failed to write WeCom status for preset %s: %s", preset_id, e)


async def clear_wecom_status(preset_id: str) -> None:
    if not preset_id:
        return
    try:
        redis = get_redis_client()
        await redis.delete(wecom_status_redis_key(preset_id))
    except Exception as e:
        logger.warning("Failed to clear WeCom status for preset %s: %s", preset_id, e)


async def read_wecom_status(preset_id: str) -> Optional[dict[str, Any]]:
    try:
        redis = get_redis_client()
        raw = await redis.get(wecom_status_redis_key(preset_id))
        if not raw:
            return None
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning("Failed to read WeCom status for preset %s: %s", preset_id, e)
    return None


def default_status_for_configured_preset(preset_id: str) -> dict[str, Any]:
    """Sensible default when Redis has no entry but preset has WeCom configured."""
    return {
        "preset_id": preset_id,
        "state": "disconnected",
        "reason_code": None,
        "reason_detail": None,
        "updated_at": None,
    }


async def resolve_wecom_status(
    preset_id: str,
    *,
    has_wecom: bool,
) -> dict[str, Any]:
    if not has_wecom:
        return {
            "preset_id": preset_id,
            "state": "disconnected",
            "reason_code": None,
            "reason_detail": "wecom_not_configured",
            "updated_at": None,
        }
    stored = await read_wecom_status(preset_id)
    if stored:
        return stored
    return default_status_for_configured_preset(preset_id)
