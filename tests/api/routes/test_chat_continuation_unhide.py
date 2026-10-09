"""续聊定时会话时取消隐藏：纯函数与 ``_update_session_config`` 透传。"""

from __future__ import annotations

from typing import Any

import pytest

from src.api.routes import chat as chat_routes
from src.api.routes.chat import _continuation_metadata_extra, _update_session_config
from src.kernel.schemas.agent import AgentRequest


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"hidden_from_conversation_list": True}, {"hidden_from_conversation_list": False}),
        ({"hidden_from_conversation_list": False}, None),
        ({"hidden_from_conversation_list": "true"}, None),
        ({"hidden_from_conversation_list": 1}, None),
        ({}, None),
        (None, None),
    ],
)
def test_continuation_metadata_extra_only_unhides_hidden_sessions(
    metadata: dict | None, expected: dict | None
) -> None:
    assert _continuation_metadata_extra(metadata) == expected


class _RecordingSessionManager:
    updates: list[tuple[str, dict[str, Any]]] = []

    async def update_session_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append((session_id, metadata))


@pytest.mark.asyncio
async def test_update_session_config_passes_extra_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingSessionManager.updates = []
    monkeypatch.setattr(chat_routes, "SessionManager", _RecordingSessionManager)
    request = AgentRequest(message="hi")

    await _update_session_config(
        "s1",
        "run-1",
        "fast",
        request,
        "zh",
        extra=_continuation_metadata_extra({"hidden_from_conversation_list": True}),
    )

    session_id, metadata = _RecordingSessionManager.updates[0]
    assert session_id == "s1"
    assert metadata["hidden_from_conversation_list"] is False
    assert metadata["current_run_id"] == "run-1"
    assert metadata["agent_id"] == "fast"


@pytest.mark.asyncio
async def test_update_session_config_without_extra_leaves_hidden_flag_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingSessionManager.updates = []
    monkeypatch.setattr(chat_routes, "SessionManager", _RecordingSessionManager)

    await _update_session_config("s1", "run-1", "fast", AgentRequest(message="hi"), "zh")

    _, metadata = _RecordingSessionManager.updates[0]
    assert "hidden_from_conversation_list" not in metadata
