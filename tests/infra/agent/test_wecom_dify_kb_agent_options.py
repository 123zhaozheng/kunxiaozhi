"""WeCom handler passes Dify KB ids into task submit agent_options."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.kernel.schemas.persona_preset import PersonaPresetSnapshot


@pytest.mark.asyncio
async def test_wecom_submit_includes_dify_kb_dataset_ids_from_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After persona resolve, submit must receive agent_options with KB ids."""
    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return ("run-1", "trace-1")

    fake_task_manager = SimpleNamespace(
        cancel=AsyncMock(return_value={"success": False}),
        submit=AsyncMock(side_effect=fake_submit),
        storage=SimpleNamespace(get_by_session_id=AsyncMock(return_value=None)),
    )

    snapshot = PersonaPresetSnapshot(
        preset_id="preset-1",
        name="Bot",
        system_prompt="prompt",
        dify_kb_dataset_ids=["dataset-wecom-1"],
    )

    async def fake_resolve(agent_request, _user):
        agent_request.persona_snapshot = snapshot
        agent_request.persona_system_prompt = "prompt"
        agent_request.enabled_skills = None

    monkeypatch.setattr(
        "src.api.routes.chat.resolve_persona_request",
        fake_resolve,
    )
    monkeypatch.setattr(
        "src.infra.task.manager.get_task_manager",
        lambda: fake_task_manager,
    )
    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._get_wecom_session_id",
        AsyncMock(return_value="wecom_chat_1"),
    )
    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._store_run_session_mapping",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._process_events",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.infra.agent.wecom.handler._persist_wecom_session_config",
        AsyncMock(return_value=True),
    )

    manager = MagicMock()
    manager.get_preset_id_for_aibotid.return_value = "preset-1"
    manager.get_config_for_aibotid.return_value = {
        "stream_reply": False,
        "send_thinking_message": False,
        "segmented_reply": False,
        "session_ttl_hours": 24,
    }
    manager.send_message = AsyncMock()

    collector_instance = MagicMock()
    collector_instance.send_thinking_placeholder = AsyncMock()
    collector_instance.set_run_id = MagicMock()
    collector_instance.finalize_stream_message = AsyncMock(return_value=False)
    collector_instance.send_message = AsyncMock()
    collector_instance.upload_and_send_files = AsyncMock()

    collector_cls = MagicMock(return_value=collector_instance)
    monkeypatch.setattr(
        "src.infra.agent.wecom.handler.WeComResponseCollector",
        collector_cls,
    )

    user_obj = SimpleNamespace(id="mongo-user-id")
    monkeypatch.setattr(
        "src.infra.user.storage.UserStorage",
        lambda: SimpleNamespace(get_by_username=AsyncMock(return_value=user_obj)),
    )

    preset = SimpleNamespace(name="WeCom Preset")
    monkeypatch.setattr(
        "src.infra.persona_preset.manager.PersonaPresetManager",
        lambda: SimpleNamespace(
            get_preset=AsyncMock(return_value=preset),
        ),
    )
    monkeypatch.setattr(
        "src.infra.folder.storage.get_project_storage",
        lambda: SimpleNamespace(
            get_or_create_by_name=AsyncMock(return_value=SimpleNamespace(id="proj-1")),
        ),
    )

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="hello",
        metadata={"aibotid": "bot-1", "message_id": "msg-1"},
    )

    fake_task_manager.submit.assert_awaited_once()
    assert captured.get("agent_options") == {
        "dify_kb_dataset_ids": ["dataset-wecom-1"],
    }
