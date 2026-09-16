"""Quota must only bill sources the user can actually delete.

Avatars and skill files are surfaced to users as protected, so charging for them
would let a quota fill up with bytes the user has no way to free.
"""

from __future__ import annotations

import pytest

from src.infra.storage.user_storage import (
    UserStorageQuotaService,
    UserStorageQuotaStorage,
    encode_storage_cursor,
)
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.storage import (
    QUOTA_BILLABLE_SOURCES,
    STORAGE_LISTABLE_SOURCES,
    FileLifecycleStatus,
    StorageFileListQuery,
    StorageSource,
)
from tests.infra.test_user_storage_quota import _Database

MIB = 1024 * 1024


def _file(user_id: str, file_id: str, source: StorageSource | None, size: int) -> dict:
    document = {
        "_id": file_id,
        "file_id": file_id,
        "user_id": user_id,
        "size": size,
        "status": FileLifecycleStatus.ACTIVE.value,
        "is_user_deletable": source in {StorageSource.CHAT, StorageSource.WECOM},
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    if source is not None:
        document["source"] = source.value
    return document


def test_billable_set_excludes_protected_sources() -> None:
    """Protected sources must stay out of the billable set."""
    for source in (
        StorageSource.SKILL,
        StorageSource.PROFILE_AVATAR,
        StorageSource.PERSONA_AVATAR,
        StorageSource.TEAM_AVATAR,
    ):
        assert source not in QUOTA_BILLABLE_SOURCES

    assert StorageSource.CHAT in QUOTA_BILLABLE_SOURCES
    assert StorageSource.WECOM in QUOTA_BILLABLE_SOURCES


def test_every_source_is_a_conscious_billing_decision() -> None:
    """A newly added StorageSource must not silently become billable."""
    known = {
        StorageSource.CHAT,
        StorageSource.WECOM,
        StorageSource.LEGACY,
        StorageSource.SKILL,
        StorageSource.PROFILE_AVATAR,
        StorageSource.PERSONA_AVATAR,
        StorageSource.TEAM_AVATAR,
    }
    assert set(StorageSource) == known, (
        "StorageSource gained a member; decide explicitly whether it consumes "
        "quota and update QUOTA_BILLABLE_SOURCES plus this test."
    )


@pytest.mark.asyncio
async def test_protected_files_are_excluded_from_usage() -> None:
    storage = UserStorageQuotaStorage(database=_Database())
    service = UserStorageQuotaService(storage)
    user_id = "user-protected"

    # 3 MiB the user can delete.
    await storage.insert_file(_file(user_id, "f-chat", StorageSource.CHAT, 2 * MIB))
    await storage.insert_file(_file(user_id, "f-wecom", StorageSource.WECOM, 1 * MIB))
    # 40 MiB the user cannot delete — must not be billed.
    await storage.insert_file(_file(user_id, "f-skill", StorageSource.SKILL, 10 * MIB))
    await storage.insert_file(_file(user_id, "f-av1", StorageSource.PROFILE_AVATAR, 10 * MIB))
    await storage.insert_file(_file(user_id, "f-av2", StorageSource.PERSONA_AVATAR, 10 * MIB))
    await storage.insert_file(_file(user_id, "f-av3", StorageSource.TEAM_AVATAR, 10 * MIB))

    rows = await service._active_files_for_reconciliation(user_id)

    assert sum(int(row["size"]) for row in rows) == 3 * MIB
    assert {row["file_id"] for row in rows} == {"f-chat", "f-wecom"}


@pytest.mark.asyncio
async def test_user_with_only_protected_files_reports_zero_usage() -> None:
    """The regression that motivated this fix: nothing deletable, yet billed."""
    storage = UserStorageQuotaStorage(database=_Database())
    service = UserStorageQuotaService(storage)
    user_id = "user-only-protected"

    await storage.insert_file(_file(user_id, "f-skill", StorageSource.SKILL, 500 * MIB))
    await storage.insert_file(_file(user_id, "f-av", StorageSource.PROFILE_AVATAR, 20 * MIB))

    rows = await service._active_files_for_reconciliation(user_id)

    assert rows == []


@pytest.mark.asyncio
async def test_legacy_and_untagged_rows_stay_billable() -> None:
    """Fail closed on billing: never silently under-report migrated usage."""
    storage = UserStorageQuotaStorage(database=_Database())
    service = UserStorageQuotaService(storage)
    user_id = "user-legacy"

    await storage.insert_file(_file(user_id, "f-legacy", StorageSource.LEGACY, 4 * MIB))
    await storage.insert_file(_file(user_id, "f-untagged", None, 1 * MIB))
    await storage.insert_file(_file(user_id, "f-skill", StorageSource.SKILL, 9 * MIB))

    rows = await service._active_files_for_reconciliation(user_id)

    assert sum(int(row["size"]) for row in rows) == 5 * MIB
    assert {row["file_id"] for row in rows} == {"f-legacy", "f-untagged"}


@pytest.mark.asyncio
async def test_other_users_files_are_not_billed() -> None:
    storage = UserStorageQuotaStorage(database=_Database())
    service = UserStorageQuotaService(storage)

    await storage.insert_file(_file("user-a", "f-a", StorageSource.CHAT, 3 * MIB))
    await storage.insert_file(_file("user-b", "f-b", StorageSource.CHAT, 7 * MIB))

    rows = await service._active_files_for_reconciliation("user-a")

    assert {row["file_id"] for row in rows} == {"f-a"}


@pytest.mark.asyncio
async def test_listing_only_returns_chat_and_wecom_files() -> None:
    storage = UserStorageQuotaStorage(database=_Database())
    user_id = "user-list"
    await storage.insert_file(_file(user_id, "f-chat", StorageSource.CHAT, 1))
    await storage.insert_file(_file(user_id, "f-wecom", StorageSource.WECOM, 2))
    for index, source in enumerate(
        (
            StorageSource.SKILL,
            StorageSource.PROFILE_AVATAR,
            StorageSource.PERSONA_AVATAR,
            StorageSource.TEAM_AVATAR,
        )
    ):
        await storage.insert_file(_file(user_id, f"f-protected-{index}", source, 10))

    rows, has_more = await storage.list_files(user_id, StorageFileListQuery(limit=100))

    # What is listed and what is billed must be the same set. A source that is
    # billed but hidden recreates the original bug: quota consumed by bytes the
    # user can neither see nor delete.
    assert STORAGE_LISTABLE_SOURCES == QUOTA_BILLABLE_SOURCES
    assert StorageSource.SKILL not in STORAGE_LISTABLE_SOURCES
    assert {row["file_id"] for row in rows} == {"f-chat", "f-wecom"}
    assert has_more is False


@pytest.mark.asyncio
async def test_explicit_protected_source_filter_returns_an_empty_page() -> None:
    storage = UserStorageQuotaStorage(database=_Database())
    user_id = "user-explicit-protected"
    await storage.insert_file(_file(user_id, "f-skill", StorageSource.SKILL, 10))

    rows, has_more = await storage.list_files(
        user_id,
        StorageFileListQuery(source=StorageSource.SKILL, cursor="not-a-valid-cursor"),
    )

    assert rows == []
    assert has_more is False


@pytest.mark.asyncio
async def test_listing_cursor_paginates_without_protected_rows() -> None:
    storage = UserStorageQuotaStorage(database=_Database())
    user_id = "user-paginated-list"
    for index, source in enumerate(
        (
            StorageSource.CHAT,
            StorageSource.SKILL,
            StorageSource.WECOM,
            StorageSource.PROFILE_AVATAR,
            StorageSource.CHAT,
            StorageSource.TEAM_AVATAR,
            StorageSource.WECOM,
        ),
        start=1,
    ):
        await storage.insert_file(_file(user_id, f"f-{index}", source, index))

    query = StorageFileListQuery(limit=2, sort="size", descending=False)
    page_one, has_more = await storage.list_files(user_id, query)
    all_rows = list(page_one)
    while has_more:
        cursor = encode_storage_cursor(all_rows[-1], query)
        page, has_more = await storage.list_files(user_id, query.model_copy(update={"cursor": cursor}))
        all_rows.extend(page)

    assert [row["file_id"] for row in all_rows] == ["f-1", "f-3", "f-5", "f-7"]
    assert all(row["source"] in {StorageSource.CHAT.value, StorageSource.WECOM.value} for row in all_rows)
