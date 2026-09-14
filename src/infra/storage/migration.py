"""Dry-run-first migration and reconciliation for legacy file records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from src.infra.storage.user_storage import (
    UserStorageQuotaService,
    UserStorageQuotaStorage,
    _cursor_documents,
)
from src.infra.upload.file_record import FileRecordStorage
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.storage import BlobStatus, FileLifecycleStatus, StorageSource


@dataclass(slots=True)
class StorageMigrationReport:
    dry_run: bool = True
    complete: bool = True
    truncated: bool = False
    next_cursor: str | None = None
    scanned_records: int = 0
    created_blobs: int = 0
    created_files: int = 0
    users: int = 0
    orphan_records: int = 0
    missing_objects: int = 0
    unverified_objects: int = 0
    quarantined_records: int = 0
    inconsistent_records: int = 0
    warnings: list[str] = field(default_factory=list)

    def add_warning(self, message: str) -> None:
        if len(self.warnings) < 100:
            self.warnings.append(str(message)[:2048])

    def model_dump(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "complete": self.complete,
            "truncated": self.truncated,
            "next_cursor": self.next_cursor,
            "scanned_records": self.scanned_records,
            "created_blobs": self.created_blobs,
            "created_files": self.created_files,
            "users": self.users,
            "orphan_records": self.orphan_records,
            "missing_objects": self.missing_objects,
            "unverified_objects": self.unverified_objects,
            "quarantined_records": self.quarantined_records,
            "inconsistent_records": self.inconsistent_records,
            "warnings": list(self.warnings),
        }


class StorageMigrationService:
    """Build modern ownership rows without deleting or guessing legacy data."""

    def __init__(
        self,
        *,
        storage: UserStorageQuotaStorage | None = None,
        legacy_storage: FileRecordStorage | None = None,
        object_storage: Any | None = None,
    ) -> None:
        self.storage = storage or UserStorageQuotaStorage()
        self.legacy_storage = legacy_storage or FileRecordStorage()
        self.object_storage = object_storage

    async def dry_run(
        self,
        *,
        apply: bool = False,
        limit: int = 1000,
        cursor: str | None = None,
    ) -> StorageMigrationReport:
        report = StorageMigrationReport(dry_run=not apply)
        await self.storage.ensure_indexes()
        bounded_limit = max(1, min(int(limit), 5000))
        query: dict[str, Any] = {}
        if cursor:
            query["_id"] = {"$gt": cursor}
        legacy_cursor = self.legacy_storage.collection.find(query)
        try:
            legacy_cursor = legacy_cursor.sort("_id", 1)
        except TypeError:
            legacy_cursor = legacy_cursor.sort([("_id", 1)])
        records = await _cursor_documents(legacy_cursor.limit(bounded_limit + 1))
        if len(records) > bounded_limit:
            report.complete = False
            report.truncated = True
            records = records[:bounded_limit]
            report.next_cursor = str(records[-1].get("_id")) if records else None
            report.add_warning("legacy scan was truncated; ownership is not complete")
        users: set[str] = set()
        for record in records:
            report.scanned_records += 1
            user_id = str(record.get("user_id") or record.get("uploaded_by") or "").strip()
            key = str(record.get("key") or "").strip()
            if not user_id or not key:
                report.orphan_records += 1
                report.add_warning("legacy record has no provable owner or key")
                continue
            try:
                record_size = int(record.get("size", -1))
            except (TypeError, ValueError):
                record_size = -1
            if not str(record.get("hash") or "").strip() or record_size < 0:
                report.inconsistent_records += 1
                report.add_warning(f"legacy record {key} has incomplete hash or size metadata")
            users.add(user_id)
            legacy_id = str(record.get("_id", record.get("hash", key)))
            # A legacy hash/key may have been shared by several users.  Keep one
            # physical blob row for that key and separate logical owner rows.
            blob_id = f"legacy-blob:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
            file_id = f"legacy:{user_id}:{legacy_id}"
            object_exists = False
            object_checked = False
            if self.object_storage is not None:
                try:
                    object_checked = True
                    object_exists = bool(await self.object_storage.file_exists(key))
                except Exception as exc:
                    object_checked = False
                    object_exists = False
                    report.unverified_objects += 1
                    report.add_warning(f"object existence check failed for {key}: {exc}")
            else:
                report.unverified_objects += 1
                report.add_warning(f"physical object was not checked for {key}; row remains quarantined")
            report.quarantined_records += 1
            if not object_exists:
                if object_checked:
                    report.missing_objects += 1
            if await self.storage.get_blob(blob_id) is None:
                report.created_blobs += 1
            if await self.storage.get_file(file_id) is None:
                report.created_files += 1
            if not apply:
                continue
            now = utc_now()
            await self.storage.insert_blob(
                {
                    "_id": blob_id,
                    "blob_id": blob_id,
                    "storage_key": key,
                    "legacy_key": key,
                    "content_hash": str(record.get("hash") or ""),
                    "size": max(0, record_size),
                    "mime_type": str(record.get("mime_type") or "application/octet-stream"),
                    "category": str(record.get("category") or "unknown"),
                    "status": BlobStatus.QUARANTINED.value,
                    "ownership_complete": False,
                    "write_operation_id": "legacy-migration",
                    "write_generation": "legacy",
                    "pending_owner_operation_ids": [],
                    "created_at": record.get("created_at") or now,
                    "updated_at": now,
                }
            )
            await self.storage.insert_file(
                {
                    "_id": file_id,
                    "file_id": file_id,
                    "user_id": user_id,
                    "blob_id": blob_id,
                    "storage_key": key,
                    "source": StorageSource.LEGACY.value,
                    "name": str(record.get("name") or key.rsplit("/", 1)[-1])[:1024],
                    "mime_type": str(record.get("mime_type") or "application/octet-stream")[:255],
                    "size": max(0, record_size),
                    "category": str(record.get("category") or "unknown")[:64],
                    "content_hash": str(record.get("hash") or ""),
                    "status": FileLifecycleStatus.MIGRATION_REQUIRED.value,
                    "is_user_deletable": False,
                    "create_operation_id": "legacy-migration",
                    "quota_committed": False,
                    "quota_released": False,
                    "created_at": record.get("created_at") or now,
                    "updated_at": now,
                }
            )
        report.users = len(users)
        if apply:
            # Rebuild ledgers from the additive rows, but leave uncertain users
            # in reconciliation_required until a complete ownership scan proves
            # that their legacy set is safe to manage.
            quota_service = UserStorageQuotaService(self.storage)
            for user_id in users:
                await quota_service.initialize_user(user_id)
        return report


async def run_storage_migration(
    *,
    apply: bool = False,
    limit: int = 1000,
    cursor: str | None = None,
) -> dict[str, Any]:
    object_storage = None
    try:
        from src.infra.storage.s3.service import get_or_init_storage

        object_storage = await get_or_init_storage()
    except Exception:
        # A provider outage must make the report conservative, not turn into a
        # destructive fallback or an unverified apply.
        object_storage = None
    report = await StorageMigrationService(object_storage=object_storage).dry_run(
        apply=apply,
        limit=limit,
        cursor=cursor,
    )
    return report.model_dump()
