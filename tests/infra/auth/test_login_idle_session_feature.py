import json
from datetime import datetime, timedelta, timezone

import pytest

from src.infra.auth import session


def test_assert_lua_uses_now_and_timeout_arguments() -> None:
    """Keep the Redis script aligned with assert_active(sid, user_id) args."""
    assert "tonumber(ARGV[2]) - last >= tonumber(ARGV[3])" in session._ASSERT_SCRIPT


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def eval(self, script, numkeys, key, *args):
        raw = self.data.get(key)
        if not raw:
            return [0, ""]
        payload = json.loads(raw)
        if str(payload.get("user_id")) != str(args[0]):
            return [0, ""]
        now = float(args[1])
        last = float(payload.get("last_activity_epoch", 0))
        if now - last >= float(args[2]):
            self.data.pop(key, None)
            return [0, ""]
        if "ARGV[4]" in script:
            payload["last_activity_at"] = args[3]
            payload["last_activity_epoch"] = now
        self.data[key] = json.dumps(payload)
        return [1, self.data[key]]


@pytest.mark.asyncio
async def test_idle_session_touch_and_expiry(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(session, "get_redis_client", lambda: fake)
    monkeypatch.setattr(session.settings, "LOGIN_IDLE_TIMEOUT_HOURS", 1 / 3600)
    sid = await session.create_session("u1")
    state = await session.assert_active(sid, "u1")
    assert state["user_id"] == "u1"
    touched = await session.touch(sid, "u1")
    assert touched["last_activity_at"] != state["last_activity_at"]
    payload = json.loads(fake.data[session._key(sid)])
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    payload["last_activity_at"] = expired_at.isoformat()
    payload["last_activity_epoch"] = expired_at.timestamp()
    fake.data[session._key(sid)] = json.dumps(payload)
    with pytest.raises(session.SessionInactiveError):
        await session.assert_active(sid, "u1")


@pytest.mark.asyncio
async def test_idle_session_rejects_user_mismatch(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(session, "get_redis_client", lambda: fake)
    sid = await session.create_session("u1")
    with pytest.raises(session.SessionInactiveError):
        await session.assert_active(sid, "u2")
