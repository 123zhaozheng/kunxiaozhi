from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import storage as storage_route
from src.kernel.schemas.storage import FileLifecycleStatus, StorageUsage


class _DomainStorage:
    async def get_blob(self, _blob_id: str):
        return {"status": "active", "storage_key": "managed/chat/u1/blob"}


class _Domain:
    def __init__(self, row: dict | None):
        self.row = row
        self.storage = _DomainStorage()

    async def get_content_file(self, _identifier: str):
        return self.row


@pytest.mark.asyncio
async def test_managed_content_returns_stable_410_without_touching_object_storage() -> None:
    service = _Domain(
        {
            "_id": "file-1",
            "status": FileLifecycleStatus.DELETED.value,
            "name": "report.pdf",
            "mime_type": "application/pdf",
            "size": 12,
        }
    )
    with pytest.raises(HTTPException) as error:
        await storage_route.stream_storage_file("file-1", SimpleNamespace(), service=service)
    assert error.value.status_code == 410
    assert error.value.detail["code"] == "file_deleted"


@pytest.mark.asyncio
async def test_unknown_managed_content_is_non_enumerating_404() -> None:
    service = _Domain(None)
    with pytest.raises(HTTPException) as error:
        await storage_route.stream_storage_file("unknown", SimpleNamespace(), service=service)
    assert error.value.status_code == 404
    assert error.value.detail["code"] == "file_not_found"


@pytest.mark.asyncio
async def test_usage_response_uses_domain_authoritative_snapshot() -> None:
    usage = StorageUsage(user_id="u1", used_bytes=80, quota_bytes=100)

    class _UsageService:
        async def get_usage(self, user_id: str, *, roles=None):
            assert user_id == "u1"
            assert roles == ["user"]
            return usage

    result = await storage_route.get_storage_usage(
        current_user=SimpleNamespace(sub="u1", roles=["user"]),
        service=_UsageService(),
    )
    assert result.warning_level.value == "notice"
    assert result.remaining_bytes == 20
