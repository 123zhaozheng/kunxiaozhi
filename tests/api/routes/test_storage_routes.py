from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException

from src.api.routes import storage as storage_route
from src.infra.storage.user_storage import UserStorageQuotaService, UserStorageQuotaStorage
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.storage import FileLifecycleStatus, StorageSource, StorageUsage
from tests.infra.test_user_storage_quota import _Database


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


@pytest.mark.asyncio
async def test_admin_reconcile_removes_protected_bytes_from_existing_ledger() -> None:
    storage = UserStorageQuotaStorage(database=_Database())
    user_id = "user-reconcile"
    now = utc_now()
    await storage.insert_usage(
        {
            "_id": user_id,
            "user_id": user_id,
            "state": "ready",
            "generation": "generation-1",
            "version": 4,
            "used_bytes": 101,
            "pending_bytes": 0,
            "active_file_count": 4,
            "quota_bytes": 1024 * 1024,
            "quota_override_bytes": None,
            "operation_markers": {},
            "in_flight_count": 0,
            "updated_at": now,
        }
    )
    await storage.insert_file(
        {
            "_id": "chat-file",
            "user_id": user_id,
            "source": StorageSource.CHAT.value,
            "size": 1,
            "status": FileLifecycleStatus.ACTIVE.value,
        }
    )
    await storage.insert_file(
        {
            "_id": "skill-file",
            "user_id": user_id,
            "source": StorageSource.SKILL.value,
            "size": 100,
            "status": FileLifecycleStatus.ACTIVE.value,
        }
    )
    service = UserStorageQuotaService(storage)

    # The route resolves the TARGET user's roles (not the caller's) so that
    # reconciliation cannot overwrite a role-derived quota with the admin's.
    class _Users:
        async def get_by_id(self, requested_id: str):
            assert requested_id == user_id
            return SimpleNamespace(roles=["staff"])

    # Passing real roles makes resolve_policy consult RoleStorage; stub it so the
    # test exercises the route rather than needing Mongo.
    class _Roles:
        async def get_by_name(self, _name: str):
            return SimpleNamespace(limits=SimpleNamespace(storage_quota_mb=None))

    with (
        mock.patch.object(storage_route, "UserStorage", _Users),
        mock.patch("src.infra.storage.user_storage.RoleStorage", _Roles),
    ):
        result = await storage_route.reconcile_storage_user(
            user_id,
            current_user=SimpleNamespace(roles=["admin"]),
            service=service,
        )

    assert result.used_bytes == 1
    assert result.active_file_count == 1


@pytest.mark.asyncio
async def test_admin_reconcile_rejects_unknown_user() -> None:
    storage = UserStorageQuotaStorage(database=_Database())
    service = UserStorageQuotaService(storage)

    class _Users:
        async def get_by_id(self, _requested_id: str):
            return None

    with mock.patch.object(storage_route, "UserStorage", _Users):
        with pytest.raises(HTTPException) as excinfo:
            await storage_route.reconcile_storage_user(
                "missing-user",
                current_user=SimpleNamespace(roles=["admin"]),
                service=service,
            )

    assert excinfo.value.status_code == 404
