from __future__ import annotations

import pytest

from src.agents.core.node_utils import build_human_message, inline_image_attachments_as_data_urls
from src.infra.agent.attachments import normalize_attachment_events, normalize_attachments
from src.infra.storage.managed_integration import set_managed_storage_service


class _LifecycleService:
    async def resolve_attachment_statuses(self, **_kwargs):
        return [
            {
                "file_id": "file-deleted",
                "status": "deleted",
                "name": "report.pdf",
                "mime_type": "application/pdf",
                "size": 12,
            },
            {
                "file_id": "file-active",
                "status": "active",
                "key": "managed/chat/u1/active",
                "url": "/api/storage/files/file-active/content",
            },
        ]


@pytest.mark.asyncio
async def test_deleted_attachment_is_projected_without_a_usable_key_or_url() -> None:
    set_managed_storage_service(_LifecycleService())
    try:
        attachments = await normalize_attachments(
            [
                {
                    "id": "legacy-1",
                    "file_id": "file-deleted",
                    "key": "old-key",
                    "url": "https://old.example/file",
                    "name": "report.pdf",
                    "type": "document",
                    "mime_type": "application/pdf",
                    "size": 12,
                }
            ],
            user_id="u1",
        )
    finally:
        set_managed_storage_service(None)

    assert attachments == [
        {
            "id": "legacy-1",
            "file_id": "file-deleted",
            "key": "",
            "url": "",
            "name": "report.pdf",
            "type": "document",
            "mime_type": "application/pdf",
            "size": 12,
            "status": "deleted",
            "reupload_required": True,
            "lifecycle_error": "file_deleted",
        }
    ]
    message = build_human_message("read this", attachments)
    assert "file_deleted" in str(message.content)
    assert "重新上传" in str(message.content)


@pytest.mark.asyncio
async def test_unavailable_image_never_attempts_object_storage_download(monkeypatch) -> None:
    called = False

    async def fail_storage():
        nonlocal called
        called = True
        raise AssertionError("deleted images must not be downloaded")

    monkeypatch.setattr(
        "src.infra.storage.s3.service.get_or_init_storage",
        fail_storage,
    )
    result = await inline_image_attachments_as_data_urls(
        [
            {
                "id": "image-1",
                "file_id": "file-deleted",
                "key": "",
                "url": "",
                "name": "image.png",
                "type": "image",
                "mime_type": "image/png",
                "size": 100,
                "status": "deleted",
            }
        ]
    )
    assert called is False
    assert result[0]["reupload_context"]["code"] == "file_deleted"


@pytest.mark.asyncio
async def test_history_projection_does_not_mutate_original_event() -> None:
    class _ActiveService:
        async def resolve_attachment_statuses(self, **_kwargs):
            return [{"file_id": "f1", "status": "active", "url": "/managed/f1"}]

    event = {
        "event_type": "user:message",
        "data": {
            "attachments": [
                {
                    "file_id": "f1",
                    "key": "legacy-key",
                    "url": "legacy-url",
                    "name": "x.txt",
                    "type": "document",
                    "mime_type": "text/plain",
                    "size": 1,
                }
            ]
        },
    }
    set_managed_storage_service(_ActiveService())
    try:
        projected = await normalize_attachment_events([event], user_id="u1")
    finally:
        set_managed_storage_service(None)

    assert event["data"]["attachments"][0]["url"] == "legacy-url"
    assert projected[0]["data"]["attachments"][0]["url"] == "/managed/f1"


@pytest.mark.asyncio
async def test_active_managed_row_without_url_gets_prefixed_content_url(monkeypatch) -> None:
    from src.kernel.config import settings

    class _RowService:
        async def status_for_user(self, _user_id, _file_ids, _keys):
            return [
                {
                    "file_id": "f9",
                    "status": "active",
                    "name": "x.xlsx",
                    "mime_type": "application/vnd.ms-excel",
                    "size": 5,
                }
            ]

    monkeypatch.setattr(settings, "APP_BASE_URL", "http://example.com:3000")
    set_managed_storage_service(_RowService())
    try:
        projected = await normalize_attachments(
            [{"file_id": "f9", "name": "x.xlsx", "mime_type": "application/vnd.ms-excel", "size": 5}],
            user_id="u1",
        )
    finally:
        set_managed_storage_service(None)

    assert projected[0]["url"] == "http://example.com:3000/api/storage/files/f9/content/x.xlsx"


@pytest.mark.asyncio
async def test_content_url_stays_relative_without_app_base_url() -> None:
    from src.kernel.config import settings

    class _RowService:
        async def status_for_user(self, _user_id, _file_ids, _keys):
            return [{"file_id": "f9", "status": "active", "name": "x.xlsx", "size": 5}]

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(settings, "APP_BASE_URL", "")
    set_managed_storage_service(_RowService())
    try:
        projected = await normalize_attachments(
            [{"file_id": "f9", "name": "x.xlsx", "size": 5}],
            user_id="u1",
        )
    finally:
        set_managed_storage_service(None)
        monkeypatch.undo()

    assert projected[0]["url"] == "/api/storage/files/f9/content/x.xlsx"
