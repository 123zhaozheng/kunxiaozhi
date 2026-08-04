"""Tests for persona feedback notifier (Web + WeCom channels)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.notification.feedback_notifier import notify_persona_feedback


class _FakeConnectionManager:
    """Records send_to_user_with_broadcast calls."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.fail_users: set[str] = set()

    async def send_to_user_with_broadcast(self, user_id: str, message: dict) -> int:
        if user_id in self.fail_users:
            raise RuntimeError("boom")
        self.sent.append((user_id, message))
        return 1


class _FakeBotManager:
    """Records WeCom send_message calls."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_message(self, aibotid: str, chat_id: str, content: str) -> bool:
        self.sent.append((aibotid, chat_id, content))
        return True


class _FakeUserStorage:
    def __init__(self, users: dict[str, Any]) -> None:
        self._users = users

    async def get_by_username(self, username: str) -> Any:
        return self._users.get(username)


def _patch_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    targets: list[str] | None,
    bound: set[str],
    users: dict[str, Any],
    conn_manager: _FakeConnectionManager,
    bot_manager: _FakeBotManager,
    run_context: tuple[str | None, str | None] = (None, None),
) -> None:
    config = None
    if targets is not None:
        config = SimpleNamespace(feedback_notify_targets=targets)

    monkeypatch.setattr(
        "src.infra.notification.feedback_notifier.AgentConfigStorage",
        lambda: SimpleNamespace(get_persona_wecom_config=_async_return(config)),
    )
    monkeypatch.setattr(
        "src.infra.notification.feedback_notifier.UserStorage",
        lambda: _FakeUserStorage(users),
    )
    monkeypatch.setattr(
        "src.infra.notification.feedback_notifier.get_connection_manager",
        lambda: conn_manager,
    )
    monkeypatch.setattr(
        "src.infra.notification.feedback_notifier.WeComNotifyBindingStorage",
        lambda: SimpleNamespace(list_bound=_async_return(bound)),
    )
    monkeypatch.setattr(
        "src.infra.notification.feedback_notifier.get_wecom_bot_manager",
        lambda: bot_manager,
    )
    monkeypatch.setattr(
        "src.infra.notification.feedback_notifier._load_run_context",
        _async_return(run_context),
    )


def _async_return(value: Any):
    async def _inner(*_args, **_kwargs) -> Any:
        return value

    return _inner


_COMMON_KWARGS = dict(
    preset_id="p1",
    preset_name="P1",
    operator="op-1",
    aibotid="bot-1",
    session_id="s1",
    run_id="r1",
)


@pytest.mark.asyncio
async def test_no_targets_does_not_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnectionManager()
    bots = _FakeBotManager()
    _patch_deps(
        monkeypatch,
        targets=[],
        bound=set(),
        users={"u1": SimpleNamespace(id="uid-1")},
        conn_manager=conn,
        bot_manager=bots,
    )

    await notify_persona_feedback(
        **_COMMON_KWARGS, rating="up", comment=None,
    )

    assert conn.sent == []
    assert bots.sent == []


@pytest.mark.asyncio
async def test_no_config_does_not_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConnectionManager()
    bots = _FakeBotManager()
    _patch_deps(
        monkeypatch,
        targets=None,  # config 不存在
        bound=set(),
        users={},
        conn_manager=conn,
        bot_manager=bots,
    )

    await notify_persona_feedback(
        **_COMMON_KWARGS, rating="down", comment="原因X",
    )

    assert conn.sent == []
    assert bots.sent == []


@pytest.mark.asyncio
async def test_web_channel_pushes_to_resolved_users_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnectionManager()
    bots = _FakeBotManager()
    _patch_deps(
        monkeypatch,
        targets=["u1", "unknown"],
        bound=set(),
        users={"u1": SimpleNamespace(id="uid-1")},  # unknown 无对应用户
        conn_manager=conn,
        bot_manager=bots,
    )

    await notify_persona_feedback(
        **_COMMON_KWARGS, rating="up", comment=None,
    )

    assert len(conn.sent) == 1
    user_id, message = conn.sent[0]
    assert user_id == "uid-1"
    assert message["type"] == "notification:feedback"
    data = message["data"]
    assert data["preset_id"] == "p1"
    assert data["preset_name"] == "P1"
    assert data["rating"] == "up"
    assert data["operator"] == "op-1"
    assert data["comment"] is None
    assert data["user_question"] is None
    assert data["model_output"] is None
    assert "ts" in data
    # 未知 username 不推送、不进入 WeCom 渠道
    assert bots.sent == []


@pytest.mark.asyncio
async def test_wecom_channel_only_for_bound_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnectionManager()
    bots = _FakeBotManager()
    _patch_deps(
        monkeypatch,
        targets=["u1", "u2"],
        bound={"u1"},  # 仅 u1 已绑定
        users={"u1": SimpleNamespace(id="uid-1"), "u2": SimpleNamespace(id="uid-2")},
        conn_manager=conn,
        bot_manager=bots,
    )

    await notify_persona_feedback(
        **_COMMON_KWARGS, rating="down", comment="答案有误",
    )

    assert len(conn.sent) == 2  # Web 渠道两个目标都推
    assert bots.sent == [("bot-1", "u1", "📢 【P1】收到新的点踩\n操作人：op-1\n反馈：答案有误")]
    # u2 未绑定，不推送
    assert all(call[1] != "u2" for call in bots.sent)


@pytest.mark.asyncio
async def test_run_context_included_in_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户问题与模型输出应出现在 Web 消息与 WeCom 文案中。"""
    conn = _FakeConnectionManager()
    bots = _FakeBotManager()
    _patch_deps(
        monkeypatch,
        targets=["u1"],
        bound={"u1"},
        users={"u1": SimpleNamespace(id="uid-1")},
        conn_manager=conn,
        bot_manager=bots,
        run_context=("你好", "我是数字人助手"),
    )

    await notify_persona_feedback(
        **_COMMON_KWARGS, rating="down", comment="内容不完整",
    )

    # Web 消息携带问题与输出
    assert len(conn.sent) == 1
    data = conn.sent[0][1]["data"]
    assert data["user_question"] == "你好"
    assert data["model_output"] == "我是数字人助手"
    # WeCom 文案包含问题与输出
    assert bots.sent[0][2] == (
        "📢 【P1】收到新的点踩\n操作人：op-1\n反馈：内容不完整\n"
        "用户问题：你好\n模型回复：我是数字人助手"
    )


@pytest.mark.asyncio
async def test_exception_isolation_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnectionManager()
    conn.fail_users = {"uid-1"}  # 第一个目标 Web 推送失败
    bots = _FakeBotManager()

    class _RaisingBotManager:
        async def send_message(self, *_args, **_kwargs) -> bool:
            raise RuntimeError("wecom boom")

    raising_bots = _RaisingBotManager()
    _patch_deps(
        monkeypatch,
        targets=["u1", "u2"],
        bound={"u1", "u2"},
        users={"u1": SimpleNamespace(id="uid-1"), "u2": SimpleNamespace(id="uid-2")},
        conn_manager=conn,
        bot_manager=raising_bots,
    )

    # 任一渠道/目标失败不应向外抛出
    await notify_persona_feedback(
        **_COMMON_KWARGS, rating="up", comment=None,
    )

    # u1 失败被隔离，u2 仍送达
    assert [uid for uid, _ in conn.sent] == ["uid-2"]
