"""Bounded background jobs for storage recovery and physical purge."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from src.infra.logging import get_logger
from src.infra.storage.s3.service import get_or_init_storage
from src.infra.storage.user_storage import (
    StorageOperationState,
    UserStorageQuotaService,
    _cursor_documents,
)
from src.infra.utils.datetime import utc_now

logger = get_logger(__name__)


async def reconcile_expired_storage_operations(
    service: UserStorageQuotaService | None = None,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    """Move expired preparing/reserved operations through safe compensation."""
    service = service or UserStorageQuotaService()
    storage = service.storage
    now = now or utc_now()
    limit = max(1, min(int(limit), 500))
    cursor = storage.operation_collection.find(
        {
            "state": {
                "$in": [
                    StorageOperationState.PREPARING.value,
                    StorageOperationState.INTENT.value,
                    StorageOperationState.RESERVED.value,
                    StorageOperationState.OBJECT_WRITTEN.value,
                    StorageOperationState.QUOTA_COMMITTED.value,
                    StorageOperationState.COMPLETING.value,
                ]
            },
            "lease_expires_at": {"$lt": now},
        }
    ).limit(limit)
    operations = await _cursor_documents(cursor)
    repaired = 0
    touched_users: set[str] = set()
    for operation in operations:
        operation_id = str(operation.get("_id") or operation.get("operation_id"))
        user_id = str(operation.get("user_id"))
        touched_users.add(user_id)
        try:
            state = str(operation.get("state"))
            if state in {
                StorageOperationState.OBJECT_WRITTEN.value,
                StorageOperationState.QUOTA_COMMITTED.value,
                StorageOperationState.COMPLETING.value,
            }:
                await service._claim_operation_lease(operation_id, uuid.uuid4().hex)
                await service.recover_operation(user_id, operation_id)
                repaired += 1
                continue
            changed = await storage.update_operation(
                {
                    "_id": operation_id,
                    "version": int(operation.get("version", 1)),
                    "lease_expires_at": {"$lt": now},
                },
                {
                    "$set": {"state": StorageOperationState.COMPENSATING.value, "updated_at": now},
                    "$inc": {"version": 1, "retry_count": 1},
                },
            )
            if not changed:
                continue
            await service.compensate_create(user_id, operation_id)
            repaired += 1
        except Exception as exc:
            logger.warning("Storage operation reconciliation failed for %s: %s", operation_id, exc)
    for user_id in touched_users:
        try:
            reconcile_user = getattr(service, "reconcile_user", None)
            if callable(reconcile_user):
                await reconcile_user(user_id)
        except Exception as exc:
            logger.warning("Storage ledger reconciliation failed for %s: %s", user_id, exc)
    return repaired


async def purge_pending_storage_blobs(
    service: UserStorageQuotaService | None = None,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> dict[str, int]:
    """Purge only owner-safe blobs and record failed attempts for retry."""
    service = service or UserStorageQuotaService()
    storage = service.storage
    now = now or utc_now()
    limit = max(1, min(int(limit), 500))
    # A crash can happen after the logical owner tombstone is durable but before
    # the delete path queues the blob. Recover those owner-safe active blobs first;
    # historical/quarantined blobs remain excluded by ownership_complete.
    orphan_cursor = storage.blob_collection.find(
        {"status": "active", "ownership_complete": True}
    ).limit(limit)
    orphan_blobs = await _cursor_documents(orphan_cursor)
    for orphan in orphan_blobs:
        blob_id = str(orphan.get("_id") or orphan.get("blob_id"))
        if orphan.get("pending_owner_operation_ids") or await storage.owner_count(blob_id) > 0:
            continue
        await storage.update_blob(
            {"_id": blob_id, "status": "active", "ownership_complete": True},
            {"$set": {"status": "purge_pending", "next_purge_at": now, "updated_at": now}},
        )

    cursor = storage.blob_collection.find(
        {"status": "purge_pending", "next_purge_at": {"$lte": now}}
    ).limit(limit)
    blobs = await _cursor_documents(cursor)
    object_storage = await get_or_init_storage()
    result = {"purged": 0, "shared": 0, "quarantined": 0, "failed": 0}
    for blob in blobs:
        status = await service.purge_blob(str(blob.get("_id") or blob.get("blob_id")), object_storage)
        if status in result:
            result[status] += 1
        elif status != "busy":
            result["failed"] += 1
    return result


async def run_storage_maintenance(
    service: UserStorageQuotaService | None = None,
    *,
    operation_limit: int = 100,
    purge_limit: int = 100,
) -> dict[str, Any]:
    """Run both bounded recovery passes; suitable for a scheduler callback."""
    service = service or UserStorageQuotaService()
    repaired = await reconcile_expired_storage_operations(service, limit=operation_limit)
    purged = await purge_pending_storage_blobs(service, limit=purge_limit)
    return {"repaired_operations": repaired, "purge": purged}
