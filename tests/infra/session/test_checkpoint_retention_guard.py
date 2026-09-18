"""清理过 checkpoint 的会话不允许分叉（避免"看似完整实则失忆"的降级克隆）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.infra.session.manager import SessionManager
from src.infra.session.retention import is_session_checkpoints_cleaned
from src.kernel.exceptions import SessionError

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _session(**metadata):
    return SimpleNamespace(
        id="s1",
        user_id="u1",
        metadata=dict(metadata),
        updated_at=NOW - timedelta(days=60),
    )


def test_unstamped_session_is_resumable() -> None:
    assert is_session_checkpoints_cleaned(_session()) is False


def test_stamp_after_last_activity_marks_cleaned() -> None:
    assert is_session_checkpoints_cleaned(_session(checkpoints_cleaned_at=NOW)) is True


def test_stamp_older_than_last_activity_is_stale() -> None:
    # The user chatted again after cleanup, so checkpoints exist once more.
    session = _session(checkpoints_cleaned_at=NOW - timedelta(days=90))
    assert is_session_checkpoints_cleaned(session) is False


@pytest.mark.asyncio
async def test_fork_refuses_cleaned_session(monkeypatch) -> None:
    manager = SessionManager()

    async def _get_session(session_id: str):
        return _session(checkpoints_cleaned_at=NOW)

    monkeypatch.setattr(manager, "get_session", _get_session)

    with pytest.raises(SessionError) as excinfo:
        await manager.fork_session_from_message("s1", "m1", "u1")
    assert "checkpoints_cleaned" in str(excinfo.value)
