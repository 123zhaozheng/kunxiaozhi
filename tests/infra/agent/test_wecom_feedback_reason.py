from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.agent.wecom.handler import _handle_wecom_feedback


class _CaptureManager:
    """Fake FeedbackManager that records the submitted FeedbackCreate."""

    def __init__(self) -> None:
        self.submitted: list[Any] = []

    async def submit_feedback(self, user_id: str, username: str, data: Any) -> Any:
        self.submitted.append(data)
        return SimpleNamespace(id="fb-1")


class _FakeFeedbackStorage:
    async def get_user_feedback_for_run(self, *_args, **_kwargs):
        return None

    async def delete(self, *_args, **_kwargs):
        return None


class _FakeUserStorage:
    def __init__(self, user) -> None:
        self._user = user

    async def get_by_username(self, _sender_id: str):
        return self._user


@pytest.mark.asyncio
async def test_wecom_dislike_maps_first_reason_to_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2/K: WeCom 点踩应把 inaccurate_reasons[0] 映射为结构化 reason 枚举。"""
    captured = _CaptureManager()

    monkeypatch.setattr(
        "src.infra.feedback.manager.FeedbackManager", lambda: captured
    )
    monkeypatch.setattr(
        "src.infra.feedback.storage.FeedbackStorage", lambda: _FakeFeedbackStorage()
    )
    monkeypatch.setattr(
        "src.infra.user.storage.UserStorage",
        lambda: _FakeUserStorage(SimpleNamespace(id="user-1")),
    )

    async def _no_lookup(_run_id: str):
        return None

    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._lookup_session_by_run_id", _no_lookup
    )

    await _handle_wecom_feedback(
        feedback_id="run-1",
        feedback_type=2,
        content="",
        inaccurate_reasons=[3, 1],
        sender_id="sender-1",
        chat_id="chat-1",
        chat_type="group",
        aibotid="bot-1",
    )

    assert len(captured.submitted) == 1
    data = captured.submitted[0]
    assert data.rating == "down"
    assert data.reason == "incorrect"  # 3 → incorrect (首个原因)


@pytest.mark.asyncio
async def test_wecom_like_has_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2: WeCom 点赞 (feedback_type=1) 不携带 reason。"""
    captured = _CaptureManager()

    monkeypatch.setattr(
        "src.infra.feedback.manager.FeedbackManager", lambda: captured
    )
    monkeypatch.setattr(
        "src.infra.feedback.storage.FeedbackStorage", lambda: _FakeFeedbackStorage()
    )
    monkeypatch.setattr(
        "src.infra.user.storage.UserStorage",
        lambda: _FakeUserStorage(SimpleNamespace(id="user-1")),
    )

    async def _no_lookup(_run_id: str):
        return None

    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._lookup_session_by_run_id", _no_lookup
    )

    await _handle_wecom_feedback(
        feedback_id="run-2",
        feedback_type=1,
        content="",
        inaccurate_reasons=[],
        sender_id="sender-1",
        chat_id="chat-1",
        chat_type="group",
        aibotid="bot-1",
    )

    assert len(captured.submitted) == 1
    data = captured.submitted[0]
    assert data.rating == "up"
    assert data.reason is None


@pytest.mark.asyncio
async def test_wecom_dislike_without_reasons_has_none_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2: WeCom 点踩但无 inaccurate_reasons 时 reason 为 None。"""
    captured = _CaptureManager()

    monkeypatch.setattr(
        "src.infra.feedback.manager.FeedbackManager", lambda: captured
    )
    monkeypatch.setattr(
        "src.infra.feedback.storage.FeedbackStorage", lambda: _FakeFeedbackStorage()
    )
    monkeypatch.setattr(
        "src.infra.user.storage.UserStorage",
        lambda: _FakeUserStorage(SimpleNamespace(id="user-1")),
    )

    async def _no_lookup(_run_id: str):
        return None

    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._lookup_session_by_run_id", _no_lookup
    )

    await _handle_wecom_feedback(
        feedback_id="run-3",
        feedback_type=2,
        content="",
        inaccurate_reasons=[],
        sender_id="sender-1",
        chat_id="chat-1",
        chat_type="group",
        aibotid="bot-1",
    )

    assert len(captured.submitted) == 1
    data = captured.submitted[0]
    assert data.rating == "down"
    assert data.reason is None
