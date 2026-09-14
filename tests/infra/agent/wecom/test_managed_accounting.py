from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infra.agent.wecom import handler
from src.infra.storage.managed_integration import set_managed_storage_service


class _Service:
    def __init__(self, calls: list[str], *, reject: bool = False) -> None:
        self.calls = calls
        self.reject = reject

    async def reserve_files(self, **_kwargs):
        self.calls.append("reserve")
        if self.reject:
            raise RuntimeError("storage_quota_exceeded")
        return {
            "file_id": "managed-file-1",
            "storage_key": "managed/wecom/user-1/generation-1",
            "status": "pending",
        }

    async def commit_files(self, **_kwargs):
        self.calls.append("commit")
        return {
            "file_id": "managed-file-1",
            "storage_key": "managed/wecom/user-1/generation-1",
            "status": "active",
        }

    async def compensate_files(self, **_kwargs):
        self.calls.append("compensate")


class _Bot:
    async def download_media_file(self, _url: str, _aes_key: str):
        return b"media", None


@pytest.mark.asyncio
async def test_wecom_reserves_before_write_and_commits_authoritative_file(monkeypatch) -> None:
    calls: list[str] = []
    set_managed_storage_service(_Service(calls))
    try:
        storage = SimpleNamespace(
            upload_to_key=AsyncMock(
                side_effect=lambda **kwargs: (
                    calls.append("write")
                    or SimpleNamespace(key=kwargs["key"], size=5)
                )
            ),
            delete_file=AsyncMock(),
        )
        monkeypatch.setattr(handler, "get_or_init_storage", AsyncMock(return_value=storage))
        monkeypatch.setattr(
            handler,
            "_file_record_storage",
            SimpleNamespace(create=AsyncMock(return_value={"id": "legacy"})),
        )

        result = await handler._build_single_attachment(
            _Bot(),
            url="https://wecom.example/file",
            aes_key="key",
            file_name="report.pdf",
            attachment_type="document",
            owner_id="user-1",
        )
    finally:
        set_managed_storage_service(None)

    assert result["file_id"] == "managed-file-1"
    assert result["status"] == "active"
    assert result["url"] == "/api/storage/files/managed-file-1/content"
    assert calls == ["reserve", "write", "commit"]


@pytest.mark.asyncio
async def test_wecom_quota_rejection_does_not_write_an_object(monkeypatch) -> None:
    calls: list[str] = []
    set_managed_storage_service(_Service(calls, reject=True))
    try:
        upload = AsyncMock()
        monkeypatch.setattr(
            handler,
            "get_or_init_storage",
            AsyncMock(return_value=SimpleNamespace(upload_to_key=upload, upload_bytes=upload)),
        )
        monkeypatch.setattr(
            handler,
            "_file_record_storage",
            SimpleNamespace(create=AsyncMock()),
        )
        with pytest.raises(handler.WeComAttachmentAccountingError, match="storage_quota_exceeded"):
            await handler._build_single_attachment(
                _Bot(),
                url="https://wecom.example/file",
                aes_key="key",
                file_name="report.pdf",
                attachment_type="document",
                owner_id="user-1",
            )
    finally:
        set_managed_storage_service(None)

    upload.assert_not_awaited()
    assert calls == ["reserve"]
