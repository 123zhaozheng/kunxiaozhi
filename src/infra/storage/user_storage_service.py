"""Compatibility module for the personal storage domain service."""

from src.infra.storage.user_storage import (
    QuotaService,
    StorageDomainService,
    UserStorageQuotaService,
)

__all__ = ["QuotaService", "StorageDomainService", "UserStorageQuotaService"]
