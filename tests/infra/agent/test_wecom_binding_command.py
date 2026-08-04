"""Tests for the WeCom "绑定通知" binding command handler."""

from __future__ import annotations

import pytest

from src.infra.agent.wecom.handler import create_wecom_message_handler


class _FakeBindingStorage:
    def __init__(self) -> None:
        self.upsert_calls: list[tuple[str, str]] = []
        self.upsert_result = True

    async def upsert(self, aibotid: str, username: str) -> bool:
        self.upsert_calls.append((aibotid, username))
        return self.upsert_result


class _FakeManager:
    """Fake WeComBotManager: routing raises if reached for binding messages."""

    def __init__(self, preset_id: str | None) -> None:
        self.preset_id = preset_id
        self.route_calls: list[str] = []
        self.sent: list[tuple[str, str, str]] = []
        self.send_ok = True

    def get_preset_id_for_aibotid(self, aibotid: str) -> str | None:
        self.route_calls.append(aibotid)
        return self.preset_id

    async def send_message(self, aibotid: str, chat_id: str, content: str) -> bool:
        self.sent.append((aibotid, chat_id, content))
        return self.send_ok


def _patch_binding(monkeypatch: pytest.MonkeyPatch, storage: _FakeBindingStorage) -> None:
    monkeypatch.setattr(
        "src.infra.agent.wecom.binding.WeComNotifyBindingStorage",
        lambda: storage,
    )


@pytest.mark.asyncio
async def test_binding_command_does_not_enter_persona_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """绑定指令在 persona 路由之前处理：upsert + 确认回复，不进入 AI 会话。"""
    storage = _FakeBindingStorage()
    manager = _FakeManager(preset_id="preset-1")
    _patch_binding(monkeypatch, storage)

    handler = create_wecom_message_handler(manager)  # type: ignore[arg-type]
    await handler(
        sender_id="sender-1",
        chat_id="chat-1",
        content="绑定通知",
        metadata={"aibotid": "bot-1", "message_id": "m-1"},
    )

    assert storage.upsert_calls == [("bot-1", "sender-1")]
    assert manager.sent == [
        (
            "bot-1",
            "chat-1",
            "绑定成功！之后该机器人的点赞/点踩通知会推送到这里。",
        )
    ]
    # 关键：未进入 aibotid → preset_id 路由
    assert manager.route_calls == []


@pytest.mark.asyncio
async def test_binding_command_trims_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeBindingStorage()
    manager = _FakeManager(preset_id="preset-1")
    _patch_binding(monkeypatch, storage)

    handler = create_wecom_message_handler(manager)  # type: ignore[arg-type]
    await handler(
        sender_id="sender-1",
        chat_id="chat-1",
        content="  绑定通知  ",
        metadata={"aibotid": "bot-1", "message_id": "m-1"},
    )

    assert storage.upsert_calls == [("bot-1", "sender-1")]
    assert manager.route_calls == []


@pytest.mark.asyncio
async def test_binding_command_confirmation_send_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeBindingStorage()
    manager = _FakeManager(preset_id="preset-1")
    manager.send_ok = False
    _patch_binding(monkeypatch, storage)

    handler = create_wecom_message_handler(manager)  # type: ignore[arg-type]
    await handler(
        sender_id="sender-1",
        chat_id="chat-1",
        content="绑定通知",
        metadata={"aibotid": "bot-1", "message_id": "m-1"},
    )

    assert storage.upsert_calls == [("bot-1", "sender-1")]
    assert manager.route_calls == []


@pytest.mark.asyncio
async def test_normal_message_still_routes_to_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeBindingStorage()
    manager = _FakeManager(preset_id=None)  # 无映射 → 路由阶段直接 return
    _patch_binding(monkeypatch, storage)

    handler = create_wecom_message_handler(manager)  # type: ignore[arg-type]
    await handler(
        sender_id="sender-1",
        chat_id="chat-1",
        content="你好",
        metadata={"aibotid": "bot-1", "message_id": "m-1"},
    )

    assert manager.route_calls == ["bot-1"]  # 正常消息进入路由
    assert storage.upsert_calls == []
    assert manager.sent == []
