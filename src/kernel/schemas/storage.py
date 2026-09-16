"""Schemas for the user-owned storage domain.

The storage domain deliberately separates logical user files from physical blobs.  The
models in this module are also used for persisted operation manifests, so their bounds
are stricter than the historical upload metadata models.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.infra.utils.datetime import utc_now

MAX_OPERATION_ITEMS = 500
MAX_OPERATION_TEXT_BYTES = 1024
MAX_IDEMPOTENCY_KEY_BYTES = 128
MAX_MIME_TYPE_BYTES = 255
MAX_CATEGORY_BYTES = 64
MAX_SANITIZED_ERROR_BYTES = 2048
MAX_OPERATION_MANIFEST_BYTES = 1024 * 1024
MAX_PENDING_OWNER_OPERATIONS = 32
MAX_STATUS_FILE_IDS = 100
MAX_BATCH_DELETE_ITEMS = 100
DEFAULT_STORAGE_QUOTA_MB = 1024
DEFAULT_STORAGE_WARNING_PERCENT = 80
MAX_STORAGE_QUOTA_BYTES = 1 << 60


def _utf8_length(value: str) -> int:
    return len(value.encode("utf-8"))


def _bounded_text(value: str, *, name: str, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{name} must not be empty")
    if _utf8_length(clean) > limit:
        raise ValueError(f"{name} exceeds {limit} UTF-8 bytes")
    return clean


class StorageSource(StrEnum):
    CHAT = "chat"
    PROFILE_AVATAR = "profile_avatar"
    PERSONA_AVATAR = "persona_avatar"
    TEAM_AVATAR = "team_avatar"
    SKILL = "skill"
    WECOM = "wecom"
    LEGACY = "legacy"


# The personal storage feature covers exactly one thing: files the user uploaded
# through a conversation. Avatars and skill files are out of scope entirely — they
# are neither listed nor billed.
#
# Billable and listable MUST stay identical. A source that is billed but hidden
# recreates the original bug this feature had: a quota filling up with bytes the
# user can neither see nor delete. If you add a source to one set, add it to the
# other or justify the asymmetry here.
STORAGE_LISTABLE_SOURCES = frozenset(
    {
        StorageSource.CHAT,
        StorageSource.WECOM,
        # LEGACY rows are migrated real user uploads (migration.py tags them when
        # the original record had no source). They are shown and billed so the
        # user can actually reclaim that space.
        StorageSource.LEGACY,
    }
)

QUOTA_BILLABLE_SOURCES = STORAGE_LISTABLE_SOURCES


def in_scope_source_clause() -> dict[str, object]:
    """Mongo clause selecting only in-scope personal-storage rows.

    Rows written before `source` was tagged have no field at all; they are real
    user uploads, so they stay in scope rather than silently vanishing from both
    the inventory and the usage total.
    """
    return {
        "$or": [
            {"source": {"$in": sorted(source.value for source in STORAGE_LISTABLE_SOURCES)}},
            {"source": {"$exists": False}},
            {"source": None},
        ]
    }


class BlobStatus(StrEnum):
    STAGED = "staged"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    PURGE_PENDING = "purge_pending"
    PURGED = "purged"
    MISSING = "missing"


class FileLifecycleStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DELETE_PENDING = "delete_pending"
    DELETED = "deleted"
    MIGRATION_REQUIRED = "migration_required"
    FAILED = "failed"


class StorageUsageState(StrEnum):
    INITIALIZING = "initializing"
    READY = "ready"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class StorageOperationKind(StrEnum):
    CREATE = "create"
    GROUP_CREATE = "group_create"
    REPLACE = "replace"
    DELETE = "delete"


class StorageOperationState(StrEnum):
    PREPARING = "preparing"
    INTENT = "intent"
    RESERVED = "reserved"
    OBJECT_WRITTEN = "object_written"
    OWNERS_PENDING = "owners_pending"
    QUOTA_COMMITTED = "quota_committed"
    COMPLETING = "completing"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    FAILED = "failed"


class StorageWarningLevel(StrEnum):
    NORMAL = "normal"
    NOTICE = "notice"
    CRITICAL = "critical"
    FULL = "full"
    OVER_QUOTA = "over_quota"


class StoragePolicy(BaseModel):
    """Effective user quota policy after override/role/default resolution."""

    quota_bytes: int = Field(..., gt=0, le=MAX_STORAGE_QUOTA_BYTES)
    warning_percent: int = Field(default=DEFAULT_STORAGE_WARNING_PERCENT, ge=1, le=99)
    enforcement_enabled: bool = True
    source: str = Field(default="global", max_length=32)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        return _bounded_text(value, name="source", limit=64)


class StorageUsage(BaseModel):
    """Authoritative quota snapshot returned to clients."""

    model_config = ConfigDict(extra="ignore")

    user_id: str
    used_bytes: int = Field(default=0, ge=0)
    pending_bytes: int = Field(default=0, ge=0)
    quota_bytes: int = Field(..., gt=0, le=MAX_STORAGE_QUOTA_BYTES)
    warning_percent: int = Field(default=DEFAULT_STORAGE_WARNING_PERCENT, ge=1, le=99)
    remaining_bytes: int = Field(default=0, ge=0)
    usage_percent: float = Field(default=0.0, ge=0)
    warning_level: StorageWarningLevel = StorageWarningLevel.NORMAL
    active_file_count: int = Field(default=0, ge=0)
    state: StorageUsageState = StorageUsageState.READY
    version: int = Field(default=0, ge=0)
    generation: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    quota_override_bytes: int | None = Field(default=None, gt=0, le=MAX_STORAGE_QUOTA_BYTES)
    in_flight_count: int = Field(default=0, ge=0, le=32)
    operation_markers: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=32)
    reconciled_at: datetime | None = None

    @model_validator(mode="after")
    def calculate_snapshot(self) -> "StorageUsage":
        self.remaining_bytes = max(self.quota_bytes - self.used_bytes - self.pending_bytes, 0)
        accounted_bytes = self.used_bytes + self.pending_bytes
        self.usage_percent = round((accounted_bytes / self.quota_bytes) * 100, 2)
        if accounted_bytes > self.quota_bytes:
            self.warning_level = StorageWarningLevel.OVER_QUOTA
        elif accounted_bytes >= self.quota_bytes:
            self.warning_level = StorageWarningLevel.FULL
        elif self.usage_percent >= max(self.warning_percent, 90):
            self.warning_level = StorageWarningLevel.CRITICAL
        elif self.usage_percent >= self.warning_percent:
            self.warning_level = StorageWarningLevel.NOTICE
        else:
            self.warning_level = StorageWarningLevel.NORMAL
        return self


class FileBlob(BaseModel):
    """Physical immutable object metadata."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    blob_id: str = Field(alias="_id", max_length=MAX_OPERATION_TEXT_BYTES)
    storage_key: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    content_hash: str = Field(..., max_length=128)
    size: int = Field(..., ge=0)
    mime_type: str = Field(default="application/octet-stream", max_length=255)
    category: str = Field(default="unknown", max_length=64)
    status: BlobStatus = BlobStatus.STAGED
    ownership_complete: bool = True
    legacy_key: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    write_operation_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    write_generation: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    pending_owner_operation_ids: list[str] = Field(default_factory=list, max_length=MAX_PENDING_OWNER_OPERATIONS)
    purge_generation: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    purge_lease_owner: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    purge_lease_expires_at: datetime | None = None
    purge_attempts: int = Field(default=0, ge=0)
    next_purge_at: datetime | None = None
    last_purge_error: str | None = Field(default=None, max_length=MAX_SANITIZED_ERROR_BYTES)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    purged_at: datetime | None = None


class UserFile(BaseModel):
    """Logical ownership row.  This is the only row exposed by user APIs."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    file_id: str = Field(alias="_id", max_length=MAX_OPERATION_TEXT_BYTES)
    user_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    blob_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    source: StorageSource
    source_ref: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    name: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    mime_type: str = Field(..., max_length=MAX_MIME_TYPE_BYTES)
    size: int = Field(..., ge=0)
    category: str = Field(..., max_length=MAX_CATEGORY_BYTES)
    content_hash: str = Field(..., max_length=128)
    status: FileLifecycleStatus = FileLifecycleStatus.PENDING
    is_user_deletable: bool = True
    create_operation_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    delete_operation_id: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    quota_committed: bool = False
    quota_released: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None
    deleted_reason: str | None = Field(default=None, max_length=MAX_SANITIZED_ERROR_BYTES)


class OperationManifestItem(BaseModel):
    """Normalized bounded item persisted in ``storage_operation_items``."""

    model_config = ConfigDict(extra="ignore")

    file_id: str
    blob_id: str
    immutable_storage_key: str
    write_generation: str
    source: StorageSource
    source_ref: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    name: str
    content_hash: str
    mime_type: str = "application/octet-stream"
    category: str = "unknown"
    size: int = Field(..., ge=0)
    item_index: int = Field(default=0, ge=0, lt=MAX_OPERATION_ITEMS)
    replaced_file_id: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    replaced_size: int = Field(default=0, ge=0)

    @field_validator("file_id", "blob_id", "immutable_storage_key", "write_generation", "name")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _bounded_text(value, name=info.field_name, limit=MAX_OPERATION_TEXT_BYTES)

    @field_validator("source_ref")
    @classmethod
    def validate_source_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, name="source_ref", limit=MAX_OPERATION_TEXT_BYTES)

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return _bounded_text(value, name="content_hash", limit=128)

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, value: str) -> str:
        return _bounded_text(value, name="mime_type", limit=MAX_MIME_TYPE_BYTES)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return _bounded_text(value, name="category", limit=MAX_CATEGORY_BYTES)


class StorageOperationItem(OperationManifestItem):
    """Persisted operation item with operation/version identity."""

    operation_item_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    operation_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    operation_version: int = Field(..., ge=1)
    state: str = Field(default="planned", max_length=64)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class StorageOperation(BaseModel):
    """Durable idempotent operation header."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    operation_id: str = Field(alias="_id", max_length=MAX_OPERATION_TEXT_BYTES)
    user_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    idempotency_key: str
    kind: StorageOperationKind
    source: StorageSource
    source_ref: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    replaced_file_id: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    replaced_size: int = Field(default=0, ge=0)
    item_count: int = Field(default=0, ge=0, le=MAX_OPERATION_ITEMS)
    manifest_digest: str = ""
    size_bytes: int = Field(default=0, ge=0)
    reserve_bytes: int = Field(default=0, ge=0)
    commit_bytes: int = Field(default=0, ge=0)
    release_bytes: int = Field(default=0, ge=0)
    quota_snapshot_bytes: int = Field(..., gt=0)
    usage_generation: int = Field(default=0, ge=0)
    roles: list[str] = Field(default_factory=list, max_length=32)
    state: StorageOperationState = StorageOperationState.PREPARING
    version: int = Field(default=1, ge=1)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    retry_count: int = Field(default=0, ge=0)
    next_retry_at: datetime | None = None
    sanitized_error: str | None = Field(default=None, max_length=MAX_SANITIZED_ERROR_BYTES)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        return _bounded_text(value, name="idempotency_key", limit=MAX_IDEMPOTENCY_KEY_BYTES)

    @field_validator("manifest_digest")
    @classmethod
    def validate_manifest_digest(cls, value: str) -> str:
        if not value:
            return value
        return _bounded_text(value, name="manifest_digest", limit=128)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, values: list[str]) -> list[str]:
        return [_bounded_text(value, name="role", limit=MAX_OPERATION_TEXT_BYTES) for value in values]

    @field_validator("source_ref")
    @classmethod
    def validate_source_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, name="source_ref", limit=MAX_OPERATION_TEXT_BYTES)

    @field_validator("sanitized_error")
    @classmethod
    def validate_error(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, name="sanitized_error", limit=MAX_SANITIZED_ERROR_BYTES)


class StorageFileListQuery(BaseModel):
    cursor: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    limit: int = Field(default=50, ge=1, le=100)
    source: StorageSource | None = None
    category: str | None = Field(default=None, max_length=MAX_CATEGORY_BYTES)
    search: str | None = Field(default=None, max_length=128)
    status: FileLifecycleStatus | None = None
    sort: str = Field(default="created_at", pattern="^(created_at|size|name)$")
    descending: bool = True


class FileStatusRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list, max_length=MAX_STATUS_FILE_IDS)
    keys: list[str] = Field(default_factory=list, max_length=MAX_STATUS_FILE_IDS)

    @model_validator(mode="after")
    def validate_ids(self) -> "FileStatusRequest":
        if not self.file_ids and not self.keys:
            raise ValueError("file_ids or keys is required")
        if len(self.file_ids) + len(self.keys) > MAX_STATUS_FILE_IDS:
            raise ValueError(f"at most {MAX_STATUS_FILE_IDS} file identifiers are allowed")
        self.file_ids = [
            _bounded_text(value, name="file_id", limit=MAX_OPERATION_TEXT_BYTES) for value in self.file_ids
        ]
        self.keys = [_bounded_text(value, name="key", limit=MAX_OPERATION_TEXT_BYTES) for value in self.keys]
        return self


class BatchDeleteRequest(BaseModel):
    file_ids: list[str] = Field(..., min_length=1, max_length=MAX_BATCH_DELETE_ITEMS)
    idempotency_key: str | None = None

    @field_validator("file_ids")
    @classmethod
    def validate_file_ids(cls, values: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = _bounded_text(value, name="file_id", limit=MAX_OPERATION_TEXT_BYTES)
            if clean not in seen:
                seen.add(clean)
                unique.append(clean)
        return unique

    @field_validator("idempotency_key")
    @classmethod
    def validate_batch_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, name="idempotency_key", limit=MAX_IDEMPOTENCY_KEY_BYTES)

    @model_validator(mode="after")
    def validate_non_empty_ids(self) -> "BatchDeleteRequest":
        if not self.file_ids:
            raise ValueError("file_ids must contain at least one identifier")
        return self


class UserQuotaUpdate(BaseModel):
    # ``null`` explicitly clears a per-user override and restores policy
    # resolution to role/global defaults.
    quota_mb: int | None = Field(default=None, gt=0, le=1024 * 1024)


class StorageErrorDetail(BaseModel):
    code: str
    message: str
    usage: StorageUsage | None = None
    required_bytes: int | None = Field(default=None, ge=0)


class StorageSummaryResponse(StorageUsage):
    pass


class StorageFileResponse(BaseModel):
    """User-facing inventory projection without physical storage identifiers."""

    file_id: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    source: StorageSource
    source_ref: str | None = Field(default=None, max_length=MAX_OPERATION_TEXT_BYTES)
    name: str = Field(..., max_length=MAX_OPERATION_TEXT_BYTES)
    mime_type: str = Field(..., max_length=MAX_MIME_TYPE_BYTES)
    size: int = Field(..., ge=0)
    category: str = Field(..., max_length=MAX_CATEGORY_BYTES)
    status: FileLifecycleStatus
    is_user_deletable: bool = True
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    deleted_reason: str | None = Field(default=None, max_length=MAX_SANITIZED_ERROR_BYTES)


class StorageFileListResponse(BaseModel):
    files: list[StorageFileResponse]
    next_cursor: str | None = None
    has_more: bool = False
    usage: StorageUsage


class StorageDeleteResult(BaseModel):
    file_id: str
    logical_status: str
    released_bytes: int = Field(default=0, ge=0)
    physical_status: str
    status: FileLifecycleStatus | None = None
    message: str | None = None


class StorageBatchDeleteResponse(BaseModel):
    results: list[StorageDeleteResult]
    usage: StorageUsage
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class StorageFileStatus(BaseModel):
    file_id: str
    status: FileLifecycleStatus | None = None
    name: str | None = None
    mime_type: str | None = None
    size: int | None = Field(default=None, ge=0)
    source: StorageSource | None = None
    deleted_reason: str | None = None


def manifest_size_bytes(items: list[OperationManifestItem | dict[str, Any]]) -> int:
    """Return the canonical bounded manifest size before it is persisted."""
    encoded_items: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        model = item if isinstance(item, OperationManifestItem) else OperationManifestItem(**item)
        model.item_index = index
        encoded_items.append(model.model_dump(mode="json"))
    return len(json.dumps(encoded_items, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# Compatibility aliases used by callers that prefer domain terminology.
StorageFile = UserFile
QuotaSummary = StorageUsage
UserStorageUsage = StorageUsage
OperationItem = StorageOperationItem
