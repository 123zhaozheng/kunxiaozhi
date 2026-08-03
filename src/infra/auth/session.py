"""Redis-backed idle login session state."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.infra.logging import get_logger
from src.infra.storage.redis import get_redis_client
from src.kernel.config import settings

logger = get_logger(__name__)
SESSION_KEY_PREFIX = "auth:login-session:"

# Keep the read/check/delete and read/check/write paths atomic.  In particular,
# a stale request must not win a race with an explicit activity touch and
# resurrect a session after it has expired.
_ASSERT_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {0, ''} end
local state = cjson.decode(raw)
if tostring(state.user_id) ~= ARGV[1] then return {0, ''} end
local last = tonumber(state.last_activity_epoch)
if not last then return {0, ''} end
if tonumber(ARGV[2]) - last >= tonumber(ARGV[3]) then
  redis.call('DEL', KEYS[1])
  return {0, ''}
end
return {1, raw}
"""

_TOUCH_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {0, ''} end
local state = cjson.decode(raw)
if tostring(state.user_id) ~= ARGV[1] then return {0, ''} end
local last = tonumber(state.last_activity_epoch)
if not last then return {0, ''} end
if tonumber(ARGV[2]) - last >= tonumber(ARGV[3]) then
  redis.call('DEL', KEYS[1])
  return {0, ''}
end
state.last_activity_at = ARGV[4]
state.last_activity_epoch = tonumber(ARGV[2])
local updated = cjson.encode(state)
redis.call('SET', KEYS[1], updated, 'EX', ARGV[5])
return {1, updated}
"""


class SessionStoreError(RuntimeError):
    """Redis session store is unavailable."""


class SessionInactiveError(RuntimeError):
    """Session is missing, mismatched, or idle-expired."""


def _key(sid: str) -> str:
    return f"{SESSION_KEY_PREFIX}{sid}"


def _ttl_seconds() -> int:
    return max(1, int(settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400))


async def create_session(user_id: str) -> str:
    sid = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "last_activity_at": now.isoformat(),
        "last_activity_epoch": now.timestamp(),
    }
    try:
        await get_redis_client().set(_key(sid), json.dumps(payload), ex=_ttl_seconds())
    except Exception as exc:
        logger.error("Failed to create auth session for user %s: %s", user_id, exc)
        raise SessionStoreError("会话存储暂时不可用") from exc
    return sid


async def _load(sid: str) -> dict:
    try:
        raw = await get_redis_client().get(_key(sid))
    except Exception as exc:
        raise SessionStoreError("会话存储暂时不可用") from exc
    if not raw:
        raise SessionInactiveError("登录会话已失效，请重新登录")
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SessionInactiveError("登录会话已失效，请重新登录") from exc


async def assert_active(sid: str | None, user_id: str) -> dict:
    if not sid:
        raise SessionInactiveError("登录会话已失效，请重新登录")
    now = datetime.now(timezone.utc)
    timeout = settings.LOGIN_IDLE_TIMEOUT_HOURS * 3600
    try:
        result = await get_redis_client().eval(  # type: ignore[misc]
            _ASSERT_SCRIPT,
            1,
            _key(sid),
            str(user_id),
            str(now.timestamp()),
            str(timeout),
        )
    except Exception as exc:
        raise SessionStoreError("会话存储暂时不可用") from exc
    if not result or int(result[0]) != 1:
        raise SessionInactiveError("登录会话已失效，请重新登录")
    try:
        return json.loads(result[1])
    except (TypeError, ValueError) as exc:
        raise SessionInactiveError("登录会话已失效，请重新登录") from exc


async def touch(sid: str | None, user_id: str) -> dict:
    if not sid:
        raise SessionInactiveError("登录会话已失效，请重新登录")
    now = datetime.now(timezone.utc)
    try:
        result = await get_redis_client().eval(  # type: ignore[misc]
            _TOUCH_SCRIPT,
            1,
            _key(sid),
            str(user_id),
            str(now.timestamp()),
            str(settings.LOGIN_IDLE_TIMEOUT_HOURS * 3600),
            now.isoformat(),
            str(_ttl_seconds()),
        )
    except Exception as exc:
        raise SessionStoreError("会话存储暂时不可用") from exc
    if not result or int(result[0]) != 1:
        raise SessionInactiveError("登录会话已失效，请重新登录")
    try:
        return json.loads(result[1])
    except (TypeError, ValueError) as exc:
        raise SessionInactiveError("登录会话已失效，请重新登录") from exc


async def remove(sid: str | None) -> None:
    if sid:
        try:
            await get_redis_client().delete(_key(sid))
        except Exception as exc:
            logger.warning("Failed to remove auth session: %s", exc)
