"""Compatibility exports for the personal storage quota service."""

from src.infra.storage.user_storage import (
    QuotaService,
    StorageDomainError,
    StorageDomainService,
    StorageFileDeletedError,
    StorageInvalidCursorError,
    StorageManagedBySourceError,
    StorageOperationBusyError,
    StorageOperationLeaseError,
    StorageOperationTooLargeError,
    StorageOwnershipNotFoundError,
    StorageQuotaExceededError,
    StorageReconciliationRequiredError,
    UserStorageQuotaService,
    UserStorageQuotaStorage,
)

__all__ = [
    "QuotaService",
    "StorageDomainError",
    "StorageDomainService",
    "StorageFileDeletedError",
    "StorageManagedBySourceError",
    "StorageOperationBusyError",
    "StorageInvalidCursorError",
    "StorageOperationLeaseError",
    "StorageOperationTooLargeError",
    "StorageOwnershipNotFoundError",
    "StorageQuotaExceededError",
    "StorageReconciliationRequiredError",
    "UserStorageQuotaService",
    "UserStorageQuotaStorage",
]
