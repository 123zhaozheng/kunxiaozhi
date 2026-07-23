"""WeCom preferred_agent_id resolve: no hardcoded search/team on message path."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.kernel.schemas.persona_preset import PersonaPresetSnapshot


def _make_submit_capturer() -> tuple[dict, AsyncMock]:
    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return ("run-1", "trace-1")

    return captured, AsyncMock(side_effect=fake_submit)


def _install_handler_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    preferred_agent_id: str | None = None,
    include_preferred_field: bool = True,
) -> tuple[dict, MagicMock]:
    """Install mocks needed to drive wecom_message_handler.

    When include_preferred_field is False, build a snapshot without setting
    preferred_agent_id so schema default (fast) applies — models missing field.
    """
    captured, submit_mock = _make_submit_capturer()
    fake_task_manager = SimpleNamespace(
        cancel=AsyncMock(return_value={"success": False}),
        submit=submit_mock,
        storage=SimpleNamespace(get_by_session_id=AsyncMock(return_value=None)),
    )

    snapshot_kwargs: dict = {
        "preset_id": "preset-1",
        "name": "Bot",
        "system_prompt": "prompt",
    }
    if include_preferred_field and preferred_agent_id is not None:
        snapshot_kwargs["preferred_agent_id"] = preferred_agent_id
    snapshot = PersonaPresetSnapshot(**snapshot_kwargs)

    async def fake_resolve(agent_request, _user):
        agent_request.persona_snapshot = snapshot
        agent_request.persona_system_prompt = "prompt"
        agent_request.enabled_skills = None

    monkeypatch.setattr("src.api.routes.chat.resolve_persona_request", fake_resolve)
    monkeypatch.setattr("src.infra.task.manager.get_task_manager", lambda: fake_task_manager)
    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._get_wecom_session_id",
        AsyncMock(return_value="wecom_chat_1"),
    )
    monkeypatch.setattr("src.infra.agent.wecom.handler._store_run_session_mapping", AsyncMock())
    monkeypatch.setattr("src.infra.agent.wecom.handler._process_events", AsyncMock())

    manager = MagicMock()
    manager.get_preset_id_for_aibotid.return_value = "preset-1"
    manager.get_config_for_aibotid.return_value = {
        "stream_reply": False,
        "send_thinking_message": False,
        "segmented_reply": False,
        "session_ttl_hours": 24,
    }
    manager.send_message = AsyncMock()
    manager.find_bot.return_value = MagicMock()

    collector_instance = MagicMock()
    collector_instance.send_thinking_placeholder = AsyncMock()
    collector_instance.set_run_id = MagicMock()
    collector_instance.finalize_stream_message = AsyncMock(return_value=False)
    collector_instance.send_message = AsyncMock()
    collector_instance.upload_and_send_files = AsyncMock()
    monkeypatch.setattr(
        "src.infra.agent.wecom.handler.WeComResponseCollector",
        MagicMock(return_value=collector_instance),
    )

    user_obj = SimpleNamespace(id="mongo-user-id")
    monkeypatch.setattr(
        "src.infra.user.storage.UserStorage",
        lambda: SimpleNamespace(get_by_username=AsyncMock(return_value=user_obj)),
    )
    monkeypatch.setattr(
        "src.infra.persona_preset.manager.PersonaPresetManager",
        lambda: SimpleNamespace(get_preset=AsyncMock(return_value=SimpleNamespace(name="WeCom"))),
    )
    monkeypatch.setattr(
        "src.infra.folder.storage.get_project_storage",
        lambda: SimpleNamespace(
            get_or_create_by_name=AsyncMock(return_value=SimpleNamespace(id="proj-1"))
        ),
    )
    return captured, manager


async def _send_text_message(manager: MagicMock) -> None:
    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="hello",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "text",
        },
    )


@pytest.mark.asyncio
async def test_wecom_preferred_team_submits_team_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, manager = _install_handler_env(monkeypatch, preferred_agent_id="team")
    await _send_text_message(manager)

    assert captured["agent_id"] == "team"
    assert captured["persona_preset_id"] == "preset-1"


@pytest.mark.asyncio
async def test_unmapped_wecom_user_gets_visible_error_and_is_not_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, manager = _install_handler_env(monkeypatch)
    monkeypatch.setattr(
        "src.infra.user.storage.UserStorage",
        lambda: SimpleNamespace(get_by_username=AsyncMock(return_value=None)),
    )

    await _send_text_message(manager)

    assert captured == {}
    manager.send_message.assert_awaited_once()
    assert "尚未绑定" in manager.send_message.await_args.args[2]


@pytest.mark.asyncio
async def test_wecom_missing_preferred_falls_back_to_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Snapshot without preferred field → schema default fast → resolve fast
    captured, manager = _install_handler_env(
        monkeypatch,
        preferred_agent_id=None,
        include_preferred_field=False,
    )
    await _send_text_message(manager)

    assert captured["agent_id"] == "fast"
    assert captured["persona_preset_id"] == "preset-1"


@pytest.mark.asyncio
async def test_wecom_preferred_search_submits_search_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, manager = _install_handler_env(monkeypatch, preferred_agent_id="search")
    await _send_text_message(manager)

    assert captured["agent_id"] == "search"
    assert captured["persona_preset_id"] == "preset-1"
