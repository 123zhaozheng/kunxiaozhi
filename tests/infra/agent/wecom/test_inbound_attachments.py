"""WeCom inbound attachments: download → S3 → attachment passthrough.

Covers image / file / voice (with and without transcription) / mixed message
attachment construction in the handler layer, plus failure degradation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.kernel.schemas.persona_preset import PersonaPresetSnapshot


def _make_submit_capturer() -> tuple[dict, AsyncMock]:
    """Return (captured kwargs dict, fake submit AsyncMock)."""
    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return ("run-1", "trace-1")

    return captured, AsyncMock(side_effect=fake_submit)


def _install_handler_env(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, MagicMock]:
    """Install the standard mocks needed to drive wecom_message_handler.

    Returns (captured-submit-dict, manager) so tests can configure find_bot on
    the same manager the handler will receive.
    """
    captured, submit_mock = _make_submit_capturer()
    fake_task_manager = SimpleNamespace(
        cancel=AsyncMock(return_value={"success": False}),
        submit=submit_mock,
        storage=SimpleNamespace(get_by_session_id=AsyncMock(return_value=None)),
    )

    snapshot = PersonaPresetSnapshot(
        preset_id="preset-1",
        name="Bot",
        system_prompt="prompt",
    )

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


def _install_storage_mocks(monkeypatch, *, storage_key: str, size: int) -> MagicMock:
    """Mock get_or_init_storage + upload_bytes + file_record.create."""
    upload_result = SimpleNamespace(key=storage_key, size=size)

    storage = MagicMock()
    storage.upload_bytes = AsyncMock(return_value=upload_result)

    async def fake_get_or_init_storage():
        return storage

    monkeypatch.setattr(
        "src.infra.agent.wecom.handler.get_or_init_storage", fake_get_or_init_storage
    )

    file_record_storage = MagicMock()
    file_record_storage.create = AsyncMock(return_value={"id": "fr-1"})
    monkeypatch.setattr("src.infra.agent.wecom.handler._file_record_storage", file_record_storage)
    return storage, file_record_storage


@pytest.mark.asyncio
async def test_image_message_builds_image_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image message: download → S3 → image attachment passed to submit."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock(return_value=(b"image-bytes", "md5hash"))
    manager.find_bot.return_value = bot

    storage, file_record_storage = _install_storage_mocks(
        monkeypatch, storage_key="image/mongo-user-id/abc.jpg", size=11
    )

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="[image]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "image",
            "pic_url": "https://wecom.example/pic",
            "aes_key": "key123",
        },
    )

    bot.download_media_file.assert_awaited_once_with("https://wecom.example/pic", "key123")
    storage.upload_bytes.assert_awaited_once()
    file_record_storage.create.assert_awaited_once()

    attachments = captured.get("attachments")
    assert attachments is not None and len(attachments) == 1
    att = attachments[0]
    assert att["type"] == "image"
    assert att["key"] == "image/mongo-user-id/abc.jpg"
    assert att["size"] == 11
    assert att["mime_type"] == "image/jpeg"
    # APP_BASE_URL is unset (placeholder) → url is empty so the agent chain
    # rebuilds it from the live request base_url or inlines a data_url.
    assert att["url"] == ""
    assert att["id"]


@pytest.mark.asyncio
async def test_file_message_builds_document_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File message: download → S3 → document attachment with original filename."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock(return_value=(b"pdf-bytes", "md5hash"))
    manager.find_bot.return_value = bot

    storage, file_record_storage = _install_storage_mocks(
        monkeypatch, storage_key="document/mongo-user-id/report.pdf", size=9
    )

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="[file: report.pdf]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "file",
            "file_url": "https://wecom.example/file",
            "aes_key": "filekey",
            "file_name": "report.pdf",
        },
    )

    bot.download_media_file.assert_awaited_once_with("https://wecom.example/file", "filekey")
    storage.upload_bytes.assert_awaited_once()
    file_record_storage.create.assert_awaited_once()

    attachments = captured.get("attachments")
    assert attachments is not None and len(attachments) == 1
    att = attachments[0]
    assert att["type"] == "document"
    assert att["name"] == "report.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["key"] == "document/mongo-user-id/report.pdf"


@pytest.mark.asyncio
async def test_voice_message_without_transcription_builds_audio_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice without transcription: download → S3 → audio attachment."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock(return_value=(b"amr-bytes", "md5hash"))
    manager.find_bot.return_value = bot

    storage, file_record_storage = _install_storage_mocks(
        monkeypatch, storage_key="audio/mongo-user-id/voice.amr", size=9
    )

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="[voice]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "voice",
            "voice_url": "https://wecom.example/voice",
            "aes_key": "voicekey",
            "voice_transcribed": False,
        },
    )

    bot.download_media_file.assert_awaited_once_with("https://wecom.example/voice", "voicekey")
    attachments = captured.get("attachments")
    assert attachments is not None and len(attachments) == 1
    att = attachments[0]
    assert att["type"] == "audio"
    assert att["mime_type"] == "audio/amr"


@pytest.mark.asyncio
async def test_voice_message_with_transcription_skips_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Voice with transcription: no download, no attachment, transcription is content."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock()
    manager.find_bot.return_value = bot

    _install_storage_mocks(monkeypatch, storage_key="audio/x", size=0)

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="转写文本内容",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "voice",
            "voice_url": "https://wecom.example/voice",
            "aes_key": "voicekey",
            "voice_transcribed": True,
        },
    )

    bot.download_media_file.assert_not_awaited()
    # No attachments when transcription is available
    assert captured.get("attachments") is None
    # Content is the transcription
    assert captured.get("message") == "转写文本内容"


@pytest.mark.asyncio
async def test_mixed_message_builds_image_and_document_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed message: text in content; image + file sub-items → 2 attachments."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock(
        side_effect=[
            (b"img-bytes", "md5-1"),
            (b"doc-bytes", "md5-2"),
        ]
    )
    manager.find_bot.return_value = bot

    storage, file_record_storage = _install_storage_mocks(
        monkeypatch, storage_key="image/mongo-user-id/x.jpg", size=9
    )

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="看这张图\n[file: notes.txt]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "mixed",
            "mixed_media_items": [
                {
                    "type": "image",
                    "url": "https://wecom.example/pic1",
                    "aes_key": "k1",
                    "file_name": "",
                },
                {
                    "type": "file",
                    "url": "https://wecom.example/file1",
                    "aes_key": "k2",
                    "file_name": "notes.txt",
                },
            ],
        },
    )

    assert bot.download_media_file.await_count == 2
    assert storage.upload_bytes.await_count == 2
    assert file_record_storage.create.await_count == 2

    attachments = captured.get("attachments")
    assert attachments is not None and len(attachments) == 2
    types = [a["type"] for a in attachments]
    assert "image" in types and "document" in types
    doc_att = next(a for a in attachments if a["type"] == "document")
    assert doc_att["name"] == "notes.txt"
    assert doc_att["mime_type"] == "text/plain"


@pytest.mark.asyncio
async def test_download_failure_degrades_to_no_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download returns empty bytes → no attachment, message still submitted."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock(return_value=(b"", None))
    manager.find_bot.return_value = bot

    storage, file_record_storage = _install_storage_mocks(
        monkeypatch, storage_key="image/x", size=0
    )

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="[image]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "image",
            "pic_url": "https://wecom.example/pic",
            "aes_key": "key123",
        },
    )

    bot.download_media_file.assert_awaited_once()
    storage.upload_bytes.assert_not_awaited()
    file_record_storage.create.assert_not_awaited()
    # Degrade: no attachments, but message still submitted with placeholder content
    assert captured.get("attachments") is None
    assert captured.get("message") == "[image]"


@pytest.mark.asyncio
async def test_s3_upload_failure_degrades_to_no_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3 upload raises → no attachment, message still submitted."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock(return_value=(b"img-bytes", "md5"))
    manager.find_bot.return_value = bot

    storage = MagicMock()
    storage.upload_bytes = AsyncMock(side_effect=RuntimeError("S3 down"))

    async def fake_get_or_init_storage():
        return storage

    monkeypatch.setattr(
        "src.infra.agent.wecom.handler.get_or_init_storage", fake_get_or_init_storage
    )
    file_record_storage = MagicMock()
    file_record_storage.create = AsyncMock()
    monkeypatch.setattr("src.infra.agent.wecom.handler._file_record_storage", file_record_storage)

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="[image]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "image",
            "pic_url": "https://wecom.example/pic",
            "aes_key": "key123",
        },
    )

    bot.download_media_file.assert_awaited_once()
    storage.upload_bytes.assert_awaited_once()
    file_record_storage.create.assert_not_awaited()
    # Degrade: no attachments, message still submitted
    assert captured.get("attachments") is None
    assert captured.get("message") == "[image]"


@pytest.mark.asyncio
async def test_no_bot_for_aibotid_degrades_to_no_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """find_bot returns None → no attachment, message still submitted."""
    captured, manager = _install_handler_env(monkeypatch)
    manager.find_bot.return_value = None

    _install_storage_mocks(monkeypatch, storage_key="image/x", size=0)

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="[image]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "image",
            "pic_url": "https://wecom.example/pic",
            "aes_key": "key123",
        },
    )

    assert captured.get("attachments") is None
    assert captured.get("message") == "[image]"


@pytest.mark.asyncio
async def test_video_message_no_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Video messages: no attachment construction (placeholder behavior maintained)."""
    captured, manager = _install_handler_env(monkeypatch)

    bot = MagicMock()
    bot.download_media_file = AsyncMock()
    manager.find_bot.return_value = bot

    _install_storage_mocks(monkeypatch, storage_key="video/x", size=0)

    from src.infra.agent.wecom.handler import create_wecom_message_handler

    handler = create_wecom_message_handler(manager)
    await handler(
        sender_id="10325",
        chat_id="chat-1",
        content="[video]",
        metadata={
            "aibotid": "bot-1",
            "message_id": "msg-1",
            "msg_type": "video",
        },
    )

    bot.download_media_file.assert_not_awaited()
    assert captured.get("attachments") is None
    assert captured.get("message") == "[video]"
