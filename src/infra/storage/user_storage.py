"""Mongo-backed personal storage domain.

This module owns the six additive storage collections and the standalone-Mongo
reservation protocol.  MongoDB is the source of truth; Redis is intentionally not
used for quota arithmetic.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from pymongo.errors import DuplicateKeyError

from src.infra.logging import get_logger
from src.infra.role.storage import RoleStorage
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.storage import (
    MAX_IDEMPOTENCY_KEY_BYTES,
    MAX_OPERATION_ITEMS,
    MAX_OPERATION_MANIFEST_BYTES,
    MAX_OPERATION_TEXT_BYTES,
    MAX_STORAGE_QUOTA_BYTES,
    STORAGE_LISTABLE_SOURCES,
    BlobStatus,
    FileLifecycleStatus,
    OperationManifestItem,
    StorageFileListQuery,
    StorageOperationKind,
    StorageOperationState,
    StoragePolicy,
    StorageSource,
    StorageUsage,
    StorageUsageState,
    UserFile,
    in_scope_source_clause,
    manifest_size_bytes,
)

logger = get_logger(__name__)

USAGE_COLLECTION = "user_storage_usage"
BLOBS_COLLECTION = "file_blobs"
FILES_COLLECTION = "user_files"
OPERATIONS_COLLECTION = "storage_operations"
OPERATION_ITEMS_COLLECTION = "storage_operation_items"
MESSAGE_REFS_COLLECTION = "file_message_refs"
STORAGE_COLLECTIONS = (
    BLOBS_COLLECTION,
    FILES_COLLECTION,
    USAGE_COLLECTION,
    OPERATIONS_COLLECTION,
    OPERATION_ITEMS_COLLECTION,
    MESSAGE_REFS_COLLECTION,
)
MAX_IN_FLIGHT_OPERATIONS = 32
DEFAULT_OPERATION_LEASE_SECONDS = 120
DEFAULT_PURGE_LEASE_SECONDS = 120


class StorageDomainError(Exception):
    """Base class for safe storage-domain failures."""

    code = "storage_error"
    status_code = 500


class StorageQuotaExceededError(StorageDomainError):
    code = "storage_quota_exceeded"
    status_code = 413

    def __init__(self, usage: StorageUsage, required_bytes: int) -> None:
        self.usage = usage
        self.required_bytes = required_bytes
        super().__init__("存储空间不足")


class StorageOperationTooLargeError(StorageDomainError):
    code = "storage_operation_too_large"
    status_code = 413


class StorageOperationBusyError(StorageDomainError):
    code = "storage_operation_busy"
    status_code = 429


class StorageReconciliationRequiredError(StorageDomainError):
    code = "storage_reconciliation_required"
    status_code = 503


class StorageOwnershipNotFoundError(StorageDomainError):
    code = "file_not_found"
    status_code = 404


class StorageFileDeletedError(StorageDomainError):
    code = "file_deleted"
    status_code = 410


class StorageManagedBySourceError(StorageDomainError):
    code = "managed_by_source"
    status_code = 409


class StorageOperationLeaseError(StorageDomainError):
    code = "storage_operation_busy"
    status_code = 409


class StorageInvalidCursorError(StorageDomainError):
    code = "invalid_storage_cursor"
    status_code = 400


def _now_plus(seconds: int) -> Any:
    return utc_now() + timedelta(seconds=seconds)


def _modified(result: Any) -> bool:
    return bool(getattr(result, "modified_count", 0) or getattr(result, "matched_count", 0))


def _inserted_id(result: Any, fallback: str) -> str:
    return str(getattr(result, "inserted_id", fallback))


def _clean_idempotency_key(value: str) -> str:
    value = str(value or "").strip()
    if not value or len(value.encode("utf-8")) > MAX_IDEMPOTENCY_KEY_BYTES:
        raise StorageOperationTooLargeError("idempotency key exceeds its UTF-8 byte limit")
    return value


def _clean_operation_id(value: str) -> str:
    clean = str(value or "").strip()
    if (
        not clean
        or len(clean.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES
        or "." in clean
        or clean.startswith("$")
    ):
        raise StorageOperationTooLargeError("operation identifier is invalid or too long")
    return clean


async def _cursor_documents(cursor: Any) -> list[dict[str, Any]]:
    """Consume both Motor cursors and the tiny cursors used by unit tests."""
    if cursor is None:
        return []
    if hasattr(cursor, "to_list"):
        try:
            return list(await cursor.to_list(length=None))
        except TypeError:
            return list(await cursor.to_list(None))
    if hasattr(cursor, "__aiter__"):
        return [item async for item in cursor]
    return list(cursor)


def _safe_document(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = dict(document)
    if "_id" in result:
        result.setdefault("id", str(result["_id"]))
    return result


def encode_storage_cursor(row: UserFile | dict[str, Any], query: StorageFileListQuery) -> str:
    """Encode the last sorted row for stable keyset pagination."""
    if isinstance(row, UserFile):
        file_id = row.file_id
        value = getattr(row, query.sort)
    else:
        file_id = str(row.get("_id") or row.get("file_id") or "")
        value = row.get(query.sort)
    if not file_id or value is None:
        raise StorageInvalidCursorError("storage page cursor cannot be created")
    if isinstance(value, datetime):
        encoded_value: Any = {"kind": "datetime", "value": value.isoformat()}
    elif isinstance(value, bool):
        encoded_value = {"kind": "int", "value": int(value)}
    elif isinstance(value, int):
        encoded_value = {"kind": "int", "value": value}
    else:
        encoded_value = {"kind": "text", "value": str(value)}
    payload = {
        "v": 1,
        "sort": query.sort,
        "descending": query.descending,
        "source": query.source.value if query.source else None,
        "category": query.category,
        "status": query.status.value if query.status else None,
        "search": query.search,
        "id": file_id,
        "value": encoded_value,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "v1." + urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_storage_cursor(cursor: str, query: StorageFileListQuery) -> tuple[Any, str] | None:
    """Decode a managed cursor; plain IDs remain compatible with old clients."""
    if not cursor.startswith("v1."):
        return None
    try:
        encoded = cursor[3:]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
        if (
            payload.get("v") != 1
            or payload.get("sort") != query.sort
            or bool(payload.get("descending")) != query.descending
            or payload.get("source") != (query.source.value if query.source else None)
            or payload.get("category") != query.category
            or payload.get("status") != (query.status.value if query.status else None)
            or payload.get("search") != query.search
        ):
            raise ValueError("cursor query does not match")
        file_id = str(payload["id"])
        if not file_id or len(file_id.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
            raise ValueError("cursor file id is invalid")
        encoded_value = payload["value"]
        kind = encoded_value.get("kind")
        value = encoded_value.get("value")
        if kind == "datetime":
            value = datetime.fromisoformat(str(value))
        elif kind == "int":
            value = int(value)
        elif kind == "text":
            value = str(value)
        else:
            raise ValueError("cursor value is invalid")
        return value, file_id
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageInvalidCursorError("storage page cursor is invalid") from exc


class UserStorageQuotaStorage:
    """Persistence adapter for storage-domain collections."""

    def __init__(self, *, database: Any | None = None) -> None:
        self._database = database
        self._collection = None
        self._collections: dict[str, Any] = {}
        self._indexes_ensured = False

    @property
    def database(self) -> Any:
        if self._database is None:
            self._database = get_mongo_client()[settings.MONGODB_DB]
        return self._database

    def _collection_for(self, name: str) -> Any:
        if name == USAGE_COLLECTION and self._collection is not None:
            return self._collection
        if name not in self._collections:
            self._collections[name] = self.database[name]
        return self._collections[name]

    @property
    def collection(self) -> Any:
        """Compatibility alias for the authoritative usage collection."""
        return self._collection_for(USAGE_COLLECTION)

    @property
    def usage_collection(self) -> Any:
        return self._collection_for(USAGE_COLLECTION)

    @property
    def blob_collection(self) -> Any:
        return self._collection_for(BLOBS_COLLECTION)

    @property
    def file_collection(self) -> Any:
        return self._collection_for(FILES_COLLECTION)

    @property
    def operation_collection(self) -> Any:
        return self._collection_for(OPERATIONS_COLLECTION)

    @property
    def operation_item_collection(self) -> Any:
        return self._collection_for(OPERATION_ITEMS_COLLECTION)

    @property
    def message_ref_collection(self) -> Any:
        return self._collection_for(MESSAGE_REFS_COLLECTION)

    async def ensure_indexes(self) -> None:
        if self._indexes_ensured:
            return
        all_succeeded = True
        indexes = {
            USAGE_COLLECTION: [
                ([('user_id', 1)], {"unique": True, "background": True}),
                ([('state', 1), ('updated_at', 1)], {"background": True}),
            ],
            BLOBS_COLLECTION: [
                ([('storage_key', 1)], {"unique": True, "background": True}),
                ([('status', 1), ('next_purge_at', 1)], {"background": True}),
                ([('content_hash', 1)], {"background": True}),
            ],
            FILES_COLLECTION: [
                ([('user_id', 1), ('status', 1), ('created_at', -1)], {"background": True}),
                (
                    [('user_id', 1), ('source', 1), ('source_ref', 1)],
                    {
                        "unique": True,
                        "background": True,
                        "partialFilterExpression": {
                            "status": FileLifecycleStatus.ACTIVE.value,
                            "source": {"$in": [
                                StorageSource.PROFILE_AVATAR.value,
                                StorageSource.PERSONA_AVATAR.value,
                                StorageSource.TEAM_AVATAR.value,
                                StorageSource.SKILL.value,
                            ]},
                            "source_ref": {"$type": "string"},
                        },
                    },
                ),
                (
                    [('user_id', 1), ('source', 1), ('content_hash', 1)],
                    {
                        "unique": True,
                        "background": True,
                        "partialFilterExpression": {
                            "status": {"$in": ["pending", "active"]},
                            "source": {"$in": [StorageSource.CHAT.value, StorageSource.WECOM.value]},
                        },
                    },
                ),
            ],
            OPERATIONS_COLLECTION: [
                ([('user_id', 1), ('idempotency_key', 1)], {"unique": True, "background": True}),
                ([('state', 1), ('lease_expires_at', 1)], {"background": True}),
            ],
            OPERATION_ITEMS_COLLECTION: [
                ([('operation_id', 1), ('item_index', 1)], {"unique": True, "background": True}),
                ([('operation_id', 1), ('file_id', 1)], {"unique": True, "background": True}),
            ],
            MESSAGE_REFS_COLLECTION: [
                ([('event_id', 1), ('file_id', 1)], {"unique": True, "background": True}),
                ([('user_id', 1), ('file_id', 1)], {"background": True}),
            ],
        }
        for name, definitions in indexes.items():
            collection = self._collection_for(name)
            for keys, options in definitions:
                try:
                    await collection.create_index(keys, **options)
                except Exception as exc:
                    logger.warning("Failed to create storage index %s on %s: %s", keys, name, exc)
                    all_succeeded = False
        self._indexes_ensured = all_succeeded
        if not all_succeeded:
            # Quota writes depend on the uniqueness/CAS indexes.  Continuing after
            # an index failure would turn a provider outage into an unbounded,
            # duplicate-accounting write path.
            raise StorageReconciliationRequiredError("storage indexes are not ready")

    async def get_usage(self, user_id: str) -> dict[str, Any] | None:
        return _safe_document(await self.usage_collection.find_one({"_id": user_id}))

    async def insert_usage(self, document: dict[str, Any]) -> bool:
        try:
            await self.usage_collection.insert_one(document)
            return True
        except DuplicateKeyError:
            return False

    async def update_usage(self, query: dict[str, Any], update: dict[str, Any]) -> bool:
        return _modified(await self.usage_collection.update_one(query, update))

    async def get_file(self, file_id: str, *, include_deleted: bool = True) -> dict[str, Any] | None:
        if len(str(file_id or "").encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
            return None
        query: dict[str, Any] = {"_id": file_id}
        if not include_deleted:
            query["status"] = {"$nin": [FileLifecycleStatus.DELETED.value]}
        return _safe_document(await self.file_collection.find_one(query))

    async def get_owned_file(
        self,
        user_id: str,
        identifier: str,
        *,
        include_deleted: bool = True,
        allow_storage_key: bool = True,
    ) -> dict[str, Any] | None:
        clean = str(identifier or "").strip()
        if not clean or len(clean.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
            return None
        query: dict[str, Any] = {"user_id": user_id}
        if allow_storage_key:
            query["$or"] = [{"_id": clean}, {"storage_key": clean}]
        else:
            query["_id"] = clean
        if not include_deleted:
            query["status"] = {"$nin": [FileLifecycleStatus.DELETED.value]}
        return _safe_document(await self.file_collection.find_one(query))

    async def find_active_by_hash(
        self, user_id: str, source: StorageSource | str, content_hash: str
    ) -> dict[str, Any] | None:
        return _safe_document(
            await self.file_collection.find_one(
                {
                    "user_id": user_id,
                    "source": str(source),
                    "content_hash": content_hash,
                    "status": {"$in": [FileLifecycleStatus.PENDING.value, FileLifecycleStatus.ACTIVE.value]},
                }
            )
        )

    async def find_active_by_source_ref(
        self, user_id: str, source: StorageSource | str, source_ref: str
    ) -> dict[str, Any] | None:
        return _safe_document(
            await self.file_collection.find_one(
                {
                    "user_id": user_id,
                    "source": str(source),
                    "source_ref": source_ref,
                    "status": {"$in": [FileLifecycleStatus.PENDING.value, FileLifecycleStatus.ACTIVE.value]},
                }
            )
        )

    async def list_files(self, user_id: str, query: StorageFileListQuery) -> tuple[list[dict[str, Any]], bool]:
        mongo_query: dict[str, Any] = {"user_id": user_id}
        empty_source_filter = False
        if query.source is None:
            # Keep the inventory scope authoritative in Mongo so pagination never
            # counts out-of-scope rows before they are filtered out. Shares one
            # clause with usage reconciliation so the list and the total can
            # never disagree about what is in scope.
            mongo_query.update(in_scope_source_clause())
        elif query.source in STORAGE_LISTABLE_SOURCES:
            mongo_query["source"] = query.source.value
        else:
            # An explicit out-of-scope source is a valid filter with no rows here.
            # `$in: []` avoids disclosing whether such files exist.
            mongo_query["source"] = {"$in": []}
            empty_source_filter = True
        if query.category:
            mongo_query["category"] = query.category
        if query.search:
            # Search is an optional UI filter, never a raw Mongo expression.
            mongo_query["name"] = {"$regex": re.escape(query.search), "$options": "i"}
        if query.status:
            mongo_query["status"] = query.status.value
        else:
            mongo_query["status"] = {"$in": [
                FileLifecycleStatus.PENDING.value,
                FileLifecycleStatus.ACTIVE.value,
                FileLifecycleStatus.DELETE_PENDING.value,
                FileLifecycleStatus.DELETED.value,
                FileLifecycleStatus.MIGRATION_REQUIRED.value,
            ]}
        if query.cursor and not empty_source_filter:
            decoded_cursor = _decode_storage_cursor(query.cursor, query)
            if decoded_cursor is None:
                mongo_query["_id"] = {"$lt" if query.descending else "$gt": query.cursor}
            else:
                cursor_value, cursor_id = decoded_cursor
                comparison = "$lt" if query.descending else "$gt"
                cursor_clause = [
                    {query.sort: {comparison: cursor_value}},
                    {query.sort: cursor_value, "_id": {comparison: cursor_id}},
                ]
                # The scope clause already owns the top-level `$or`; assigning
                # another one here would silently drop it and leak out-of-scope
                # rows on every page after the first. Combine under `$and`.
                existing_or = mongo_query.pop("$or", None)
                if existing_or is None:
                    mongo_query["$or"] = cursor_clause
                else:
                    mongo_query["$and"] = [
                        {"$or": existing_or},
                        {"$or": cursor_clause},
                    ]
        direction = -1 if query.descending else 1
        cursor = self.file_collection.find(mongo_query)
        try:
            cursor = cursor.sort([(query.sort, direction), ("_id", direction)])
        except TypeError:
            cursor = cursor.sort(query.sort, direction)
        cursor = cursor.limit(query.limit + 1)
        rows = [_safe_document(item) for item in await _cursor_documents(cursor)]
        rows = [item for item in rows if item is not None]
        has_more = len(rows) > query.limit
        return rows[: query.limit], has_more

    async def insert_blob(self, document: dict[str, Any]) -> bool:
        try:
            await self.blob_collection.insert_one(document)
            return True
        except DuplicateKeyError:
            return False

    async def get_blob(self, blob_id: str) -> dict[str, Any] | None:
        return _safe_document(await self.blob_collection.find_one({"_id": blob_id}))

    async def get_blob_by_storage_key(self, storage_key: str) -> dict[str, Any] | None:
        """Resolve one physical key without exposing it through user APIs."""
        return _safe_document(await self.blob_collection.find_one({"storage_key": storage_key}))

    async def insert_file(self, document: dict[str, Any]) -> bool:
        try:
            await self.file_collection.insert_one(document)
            return True
        except DuplicateKeyError:
            return False

    async def update_file(self, query: dict[str, Any], update: dict[str, Any]) -> bool:
        return _modified(await self.file_collection.update_one(query, update))

    async def update_blob(self, query: dict[str, Any], update: dict[str, Any]) -> bool:
        return _modified(await self.blob_collection.update_one(query, update))

    async def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        return _safe_document(await self.operation_collection.find_one({"_id": operation_id}))

    async def get_operation_by_key(self, user_id: str, idempotency_key: str) -> dict[str, Any] | None:
        return _safe_document(
            await self.operation_collection.find_one(
                {"user_id": user_id, "idempotency_key": idempotency_key}
            )
        )

    async def insert_operation(self, document: dict[str, Any]) -> bool:
        try:
            await self.operation_collection.insert_one(document)
            return True
        except DuplicateKeyError:
            return False

    async def update_operation(self, query: dict[str, Any], update: dict[str, Any]) -> bool:
        return _modified(await self.operation_collection.update_one(query, update))

    async def insert_operation_items(self, documents: list[dict[str, Any]]) -> None:
        if not documents:
            return
        if hasattr(self.operation_item_collection, "insert_many"):
            await self.operation_item_collection.insert_many(documents, ordered=True)
            return
        for document in documents:
            await self.operation_item_collection.insert_one(document)

    async def get_operation_items(self, operation_id: str) -> list[dict[str, Any]]:
        cursor = self.operation_item_collection.find({"operation_id": operation_id}).sort("item_index", 1)
        return [item for item in await _cursor_documents(cursor)]

    async def owner_count(self, blob_id: str) -> int:
        return int(
            await self.file_collection.count_documents(
                {
                    "blob_id": blob_id,
                    "status": {"$in": [
                        FileLifecycleStatus.PENDING.value,
                        FileLifecycleStatus.ACTIVE.value,
                        FileLifecycleStatus.DELETE_PENDING.value,
                    ]},
                }
            )
        )


@dataclass(slots=True)
class PreparedStorageOperation:
    operation_id: str
    file_id: str
    blob_id: str
    storage_key: str
    size: int
    reused: bool = False
    commit_bytes: int | None = None
    release_bytes: int = 0
    replaced_file_id: str | None = None
    replaced_size: int = 0
    roles: tuple[str, ...] = ()
    lease_owner: str | None = None


class UserStorageQuotaService:
    """Effective policy, quota CAS and logical file lifecycle service."""

    def __init__(self, storage: UserStorageQuotaStorage | None = None) -> None:
        self.storage = storage or UserStorageQuotaStorage()

    @staticmethod
    def _global_quota_bytes() -> int:
        mb = int(getattr(settings, "USER_STORAGE_DEFAULT_QUOTA_MB", 1024) or 1024)
        if mb <= 0:
            mb = 1
        return min(mb * 1024 * 1024, MAX_STORAGE_QUOTA_BYTES)

    @staticmethod
    def _warning_percent() -> int:
        value = int(getattr(settings, "USER_STORAGE_WARNING_PERCENT", 80) or 80)
        return max(1, min(99, value))

    @staticmethod
    def _ledger_corrupt(document: dict[str, Any]) -> bool:
        """Detect unsafe ledger shapes before a write path can trust them."""
        try:
            state = str(document.get("state", ""))
            if state not in {item.value for item in StorageUsageState}:
                return True
            used = int(document.get("used_bytes", 0))
            pending = int(document.get("pending_bytes", 0))
            active_count = int(document.get("active_file_count", 0))
            quota = int(document.get("quota_bytes", 0))
            in_flight = int(document.get("in_flight_count", 0))
        except (TypeError, ValueError):
            return True
        if (
            used < 0
            or pending < 0
            or active_count < 0
            or not 0 < quota <= MAX_STORAGE_QUOTA_BYTES
            or not 0 <= in_flight <= MAX_IN_FLIGHT_OPERATIONS
        ):
            return True
        override = document.get("quota_override_bytes")
        if override is not None:
            try:
                if not 0 < int(override) <= MAX_STORAGE_QUOTA_BYTES:
                    return True
            except (TypeError, ValueError):
                return True
        markers = document.get("operation_markers") or {}
        if not isinstance(markers, dict) or len(markers) > MAX_IN_FLIGHT_OPERATIONS:
            return True
        reserved_bytes = 0
        in_flight_marker_count = 0
        valid_marker_states = {"reserved", "committed", "released", "compensated"}
        for marker_key, marker in markers.items():
            try:
                _clean_operation_id(str(marker_key))
            except StorageDomainError:
                return True
            if not isinstance(marker, dict) or marker.get("state") not in valid_marker_states:
                return True
            try:
                reserve_bytes = int(marker.get("reserve_bytes", 0))
                commit_bytes = int(marker.get("commit_bytes", 0))
                release_bytes = int(marker.get("release_bytes", 0))
            except (TypeError, ValueError):
                return True
            if (
                reserve_bytes < 0
                or commit_bytes < 0
                or release_bytes < 0
                or reserve_bytes > MAX_STORAGE_QUOTA_BYTES
                or commit_bytes > MAX_STORAGE_QUOTA_BYTES
                or release_bytes > MAX_STORAGE_QUOTA_BYTES
                or commit_bytes > reserve_bytes
            ):
                return True
            if marker.get("state") == "reserved":
                reserved_bytes += reserve_bytes
            if marker.get("state") in {"reserved", "committed"}:
                in_flight_marker_count += 1
        return (
            in_flight_marker_count > MAX_IN_FLIGHT_OPERATIONS
            or in_flight_marker_count != in_flight
            or reserved_bytes != pending
        )

    async def resolve_policy(self, user_id: str, roles: Iterable[str] | None = None) -> StoragePolicy:
        """Resolve user override > most permissive role > global default."""
        usage = await self.storage.get_usage(user_id)
        override = usage.get("quota_override_bytes") if usage else None
        if override is not None:
            try:
                override = int(override)
            except (TypeError, ValueError) as exc:
                raise StorageReconciliationRequiredError("storage quota override is invalid") from exc
            if not 0 < override <= MAX_STORAGE_QUOTA_BYTES:
                raise StorageReconciliationRequiredError("storage quota override is outside the safe range")
            return StoragePolicy(
                quota_bytes=override,
                warning_percent=self._warning_percent(),
                enforcement_enabled=bool(getattr(settings, "USER_STORAGE_ENFORCEMENT_ENABLED", True)),
                source="user",
            )

        role_quota_mb = 0
        if roles:
            role_storage = RoleStorage()
            for role_name in list(roles)[:32]:
                try:
                    role = await role_storage.get_by_name(str(role_name))
                except Exception as exc:
                    logger.warning("Failed to resolve storage role limit for %s: %s", role_name, exc)
                    raise StorageReconciliationRequiredError(
                        "storage policy could not be resolved"
                    ) from exc
                value = getattr(getattr(role, "limits", None), "storage_quota_mb", None) if role else None
                if value is not None:
                    try:
                        candidate = int(value)
                    except (TypeError, ValueError):
                        logger.warning("Ignoring invalid storage quota for role %s", role_name)
                        continue
                    if 0 < candidate <= MAX_STORAGE_QUOTA_BYTES // (1024 * 1024):
                        role_quota_mb = max(role_quota_mb, candidate)
                    else:
                        logger.warning("Ignoring out-of-range storage quota for role %s", role_name)
        if role_quota_mb:
            return StoragePolicy(
                quota_bytes=role_quota_mb * 1024 * 1024,
                warning_percent=self._warning_percent(),
                enforcement_enabled=bool(getattr(settings, "USER_STORAGE_ENFORCEMENT_ENABLED", True)),
                source="role",
            )
        return StoragePolicy(
            quota_bytes=self._global_quota_bytes(),
            warning_percent=self._warning_percent(),
            enforcement_enabled=bool(getattr(settings, "USER_STORAGE_ENFORCEMENT_ENABLED", True)),
            source="global",
        )

    async def initialize_user(
        self,
        user_id: str,
        *,
        roles: Iterable[str] | None = None,
        lease_owner: str | None = None,
        lease_seconds: int = DEFAULT_OPERATION_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Initialize or reconcile one ledger with a versioned lease."""
        await self.storage.ensure_indexes()
        lease_owner = lease_owner or uuid.uuid4().hex
        existing = await self.storage.get_usage(user_id)
        policy = await self.resolve_policy(user_id, roles)
        if existing and existing.get("state") == StorageUsageState.READY.value:
            if self._ledger_corrupt(existing):
                await self.storage.update_usage(
                    {
                        "_id": user_id,
                        "state": StorageUsageState.READY.value,
                        "version": existing.get("version", 0),
                    },
                    {
                        "$set": {
                            "state": StorageUsageState.RECONCILIATION_REQUIRED.value,
                            "updated_at": utc_now(),
                        },
                        "$inc": {"version": 1},
                    },
                )
                return (await self.storage.get_usage(user_id)) or existing
            current_quota = int(existing.get("quota_bytes") or policy.quota_bytes)
            if current_quota != policy.quota_bytes:
                await self.storage.update_usage(
                    {"_id": user_id, "state": StorageUsageState.READY.value, "version": existing.get("version", 0)},
                    {"$set": {"quota_bytes": policy.quota_bytes, "updated_at": utc_now()}, "$inc": {"version": 1}},
                )
            refreshed = await self.storage.get_usage(user_id)
            return refreshed or existing

        if existing and existing.get("state") == StorageUsageState.RECONCILIATION_REQUIRED.value:
            return existing

        if existing and existing.get("state") == StorageUsageState.INITIALIZING.value:
            lease_expires = existing.get("reconcile_lease_expires_at")
            if lease_expires is not None and lease_expires > utc_now():
                # Do not fence a live initializer.  The reservation path fails
                # closed until that worker promotes the ledger to ready.
                return existing

        now = utc_now()
        initial = {
            "_id": user_id,
            "user_id": user_id,
            "state": StorageUsageState.INITIALIZING.value,
            "generation": uuid.uuid4().hex,
            "version": int(existing.get("version", 0)) if existing else 0,
            "used_bytes": int(existing.get("used_bytes", 0)) if existing else 0,
            "pending_bytes": int(existing.get("pending_bytes", 0)) if existing else 0,
            "active_file_count": int(existing.get("active_file_count", 0)) if existing else 0,
            "quota_bytes": policy.quota_bytes,
            "quota_override_bytes": existing.get("quota_override_bytes") if existing else None,
            "operation_markers": existing.get("operation_markers", {}) if existing else {},
            "in_flight_count": int(existing.get("in_flight_count", 0)) if existing else 0,
            "reconcile_lease_owner": lease_owner,
            "reconcile_lease_expires_at": _now_plus(lease_seconds),
            "updated_at": now,
        }
        if existing:
            acquired = await self.storage.update_usage(
                {
                    "_id": user_id,
                    "state": existing.get("state", StorageUsageState.INITIALIZING.value),
                    "version": existing.get("version", 0),
                },
                {"$set": initial, "$inc": {"version": 1}},
            )
            if not acquired:
                latest = await self.storage.get_usage(user_id)
                if latest:
                    return latest
        else:
            if not await self.storage.insert_usage(initial):
                latest = await self.storage.get_usage(user_id)
                if latest:
                    return latest

        active_rows = await self._active_files_for_reconciliation(user_id)
        used_bytes = sum(int(row.get("size", 0)) for row in active_rows)
        active_count = self._listable_file_count(active_rows)
        uncertain = (
            any(row.get("status") == FileLifecycleStatus.MIGRATION_REQUIRED.value for row in active_rows)
            or int(initial.get("pending_bytes", 0)) > 0
            or bool(initial.get("operation_markers"))
            or int(initial.get("in_flight_count", 0)) > 0
        )
        state = (
            StorageUsageState.RECONCILIATION_REQUIRED.value
            if uncertain
            else StorageUsageState.READY.value
        )
        current = await self.storage.get_usage(user_id)
        if current and current.get("reconcile_lease_owner") not in (None, lease_owner):
            return current
        await self.storage.update_usage(
            {
                "_id": user_id,
                "state": StorageUsageState.INITIALIZING.value,
                "reconcile_lease_owner": lease_owner,
                "version": current.get("version") if current else initial.get("version", 0),
            },
            {
                "$set": {
                    "state": state,
                    "used_bytes": used_bytes,
                    "active_file_count": active_count,
                    "pending_bytes": int(current.get("pending_bytes", 0)) if current else 0,
                    "quota_bytes": policy.quota_bytes,
                    "reconciled_at": utc_now(),
                    "updated_at": utc_now(),
                    "reconcile_lease_owner": None,
                    "reconcile_lease_expires_at": None,
                },
                "$inc": {"version": 1},
            },
        )
        return (await self.storage.get_usage(user_id)) or initial

    async def _active_files_for_reconciliation(self, user_id: str) -> list[dict[str, Any]]:
        # Filter in the query, not in Python, so a user with many out-of-scope
        # files does not pull their whole file set into memory.
        query = {
            "user_id": user_id,
            "status": {"$in": [
                FileLifecycleStatus.ACTIVE.value,
                FileLifecycleStatus.PENDING.value,
                FileLifecycleStatus.DELETE_PENDING.value,
                FileLifecycleStatus.MIGRATION_REQUIRED.value,
            ]},
            **in_scope_source_clause(),
        }
        cursor = self.storage.file_collection.find(query)
        return await _cursor_documents(cursor)

    @staticmethod
    def _is_listable_source(source: Any) -> bool:
        value = getattr(source, "value", source)
        return value in {item.value for item in STORAGE_LISTABLE_SOURCES}

    @classmethod
    def _listable_file_count(cls, rows: Iterable[dict[str, Any]]) -> int:
        return sum(cls._is_listable_source(row.get("source")) for row in rows)

    def _usage_model(self, document: dict[str, Any], policy: StoragePolicy | None = None) -> StorageUsage:
        try:
            quota = int(document.get("quota_bytes") or (policy.quota_bytes if policy else self._global_quota_bytes()))
            used = int(document.get("used_bytes", 0))
            pending = int(document.get("pending_bytes", 0))
            active_count = int(document.get("active_file_count", 0))
        except (TypeError, ValueError) as exc:
            raise StorageReconciliationRequiredError("storage ledger contains invalid counters") from exc
        if (
            not 0 < quota <= MAX_STORAGE_QUOTA_BYTES
            or used < 0
            or pending < 0
            or active_count < 0
        ):
            raise StorageReconciliationRequiredError("storage ledger contains invalid counters")
        return StorageUsage(
            user_id=str(document.get("user_id") or document.get("_id")),
            used_bytes=used,
            pending_bytes=pending,
            quota_bytes=quota,
            warning_percent=policy.warning_percent if policy else self._warning_percent(),
            active_file_count=active_count,
            state=document.get("state", StorageUsageState.READY.value),
            version=max(0, int(document.get("version", 0))),
            generation=document.get("generation"),
            quota_override_bytes=document.get("quota_override_bytes"),
            in_flight_count=max(0, int(document.get("in_flight_count", 0))),
            operation_markers=document.get("operation_markers") or {},
            reconciled_at=document.get("reconciled_at"),
        )

    async def get_usage(self, user_id: str, *, roles: Iterable[str] | None = None) -> StorageUsage:
        effective_roles = tuple(roles or ())
        document = await self.initialize_user(user_id, roles=effective_roles)
        # A worker can crash after marking an operation terminal but before
        # pruning its committed marker. Reap only markers whose durable
        # operation is already terminal; in-flight/reserved operations remain
        # fail-closed and visible to reconciliation.
        for operation_id in list((document.get("operation_markers") or {}).keys())[:MAX_IN_FLIGHT_OPERATIONS]:
            operation = await self.storage.get_operation(str(operation_id))
            if operation and operation.get("state") in {
                StorageOperationState.COMPLETED.value,
                StorageOperationState.COMPENSATED.value,
            }:
                await self._prune_committed_marker(user_id, str(operation_id))
        document = (await self.storage.get_usage(user_id)) or document
        policy = await self.resolve_policy(user_id, effective_roles)
        return self._usage_model(document, policy)

    async def reconcile_user(
        self,
        user_id: str,
        *,
        roles: Iterable[str] | None = None,
    ) -> StorageUsage:
        """Promote a fail-closed ledger only after all durable work is settled."""
        effective_roles = tuple(roles or ())
        current = await self.storage.get_usage(user_id)
        if current is None:
            return await self.get_usage(user_id, roles=effective_roles)
        if self._ledger_corrupt(current):
            return self._usage_model(current, await self.resolve_policy(user_id, effective_roles))
        markers = current.get("operation_markers") or {}
        if int(current.get("pending_bytes", 0)) > 0 or int(current.get("in_flight_count", 0)) > 0 or markers:
            return self._usage_model(current, await self.resolve_policy(user_id, effective_roles))
        rows = await self._active_files_for_reconciliation(user_id)
        used_bytes = sum(max(0, int(row.get("size", 0))) for row in rows)
        uncertain = any(row.get("status") == FileLifecycleStatus.MIGRATION_REQUIRED.value for row in rows)
        policy = await self.resolve_policy(user_id, effective_roles)
        target_state = (
            StorageUsageState.RECONCILIATION_REQUIRED.value
            if uncertain
            else StorageUsageState.READY.value
        )
        await self.storage.update_usage(
            {
                "_id": user_id,
                "version": int(current.get("version", 0)),
                "state": current.get("state"),
                "pending_bytes": 0,
                "in_flight_count": 0,
            },
            {
                "$set": {
                    "state": target_state,
                    "used_bytes": used_bytes,
                    "active_file_count": self._listable_file_count(rows),
                    "quota_bytes": policy.quota_bytes,
                    "reconciled_at": utc_now(),
                    "updated_at": utc_now(),
                },
                "$inc": {"version": 1},
            },
        )
        return await self.get_usage(user_id, roles=effective_roles)

    async def set_user_quota(self, user_id: str, quota_bytes: int) -> StorageUsage:
        try:
            quota_bytes = int(quota_bytes)
        except (TypeError, ValueError) as exc:
            raise StorageOperationTooLargeError("quota must be a bounded integer") from exc
        if not 0 < quota_bytes <= MAX_STORAGE_QUOTA_BYTES:
            raise StorageOperationTooLargeError("quota is outside the safe byte range")
        await self.initialize_user(user_id)
        await self.storage.update_usage(
            {"_id": user_id},
            {
                "$set": {
                    "quota_override_bytes": quota_bytes,
                    "quota_bytes": quota_bytes,
                    "updated_at": utc_now(),
                },
                "$inc": {"version": 1},
            },
        )
        return await self.get_usage(user_id)

    async def clear_user_quota(
        self,
        user_id: str,
        *,
        roles: Iterable[str] | None = None,
    ) -> StorageUsage:
        """Remove a user override and restore role/global policy resolution."""

        await self.initialize_user(user_id)
        current = await self.storage.get_usage(user_id)
        if not current:
            raise StorageReconciliationRequiredError("storage ledger is missing")
        changed = await self.storage.update_usage(
            {"_id": user_id, "version": int(current.get("version", 0))},
            {
                "$unset": {"quota_override_bytes": ""},
                "$inc": {"version": 1},
                "$set": {"updated_at": utc_now()},
            },
        )
        if not changed:
            raise StorageOperationBusyError("storage quota update conflicted with another operation")
        policy = await self.resolve_policy(user_id, roles)
        await self.storage.update_usage(
            {"_id": user_id},
            {"$set": {"quota_bytes": policy.quota_bytes, "updated_at": utc_now()}, "$inc": {"version": 1}},
        )
        return await self.get_usage(user_id)

    async def _reserve_document(
        self,
        user_id: str,
        operation_id: str,
        reserve_bytes: int,
        *,
        quota_snapshot: int | None = None,
        commit_bytes: int | None = None,
        release_bytes: int = 0,
        roles: Iterable[str] | None = None,
    ) -> StorageUsage:
        operation_id = _clean_operation_id(operation_id)
        reserve_bytes = max(0, int(reserve_bytes))
        document = await self.initialize_user(user_id, roles=roles)
        policy = await self.resolve_policy(user_id, roles)
        if document.get("state") == StorageUsageState.RECONCILIATION_REQUIRED.value:
            raise StorageReconciliationRequiredError("storage ledger requires reconciliation")
        for _ in range(8):
            current = await self.storage.get_usage(user_id)
            if current is None:
                document = await self.initialize_user(user_id)
                current = document
            markers = current.get("operation_markers") or {}
            marker = markers.get(operation_id)
            if marker and marker.get("state") in {"reserved", "committed", "released", "compensated"}:
                expected_commit = int(commit_bytes if commit_bytes is not None else reserve_bytes)
                if (
                    int(marker.get("reserve_bytes", 0)) != reserve_bytes
                    or int(marker.get("commit_bytes", 0)) != expected_commit
                ):
                    raise StorageOperationBusyError("storage operation payload conflicts with its idempotency marker")
                return self._usage_model(current, policy)
            if int(current.get("in_flight_count", 0)) >= MAX_IN_FLIGHT_OPERATIONS:
                raise StorageOperationBusyError("too many storage operations are in flight")
            used = int(current.get("used_bytes", 0))
            pending = int(current.get("pending_bytes", 0))
            persisted_quota = int(current.get("quota_bytes") or policy.quota_bytes)
            requested_quota = int(quota_snapshot) if quota_snapshot else policy.quota_bytes
            # A caller-supplied snapshot may preserve a pre-flight policy, but it
            # must never increase the current authoritative quota.
            quota = min(persisted_quota, requested_quota)
            if policy.enforcement_enabled and used + pending + reserve_bytes > quota:
                raise StorageQuotaExceededError(self._usage_model(current, policy), reserve_bytes)
            marker_document = {
                "state": "reserved",
                "reserve_bytes": reserve_bytes,
                "commit_bytes": int(commit_bytes if commit_bytes is not None else reserve_bytes),
                "release_bytes": int(release_bytes),
                "created_at": utc_now(),
            }
            query = {
                "_id": user_id,
                "state": StorageUsageState.READY.value,
                "version": int(current.get("version", 0)),
                "used_bytes": used,
                "pending_bytes": pending,
                "in_flight_count": {"$lt": MAX_IN_FLIGHT_OPERATIONS},
                f"operation_markers.{operation_id}": {"$exists": False},
            }
            changed = await self.storage.update_usage(
                query,
                {
                    "$inc": {"pending_bytes": reserve_bytes, "in_flight_count": 1, "version": 1},
                    "$set": {f"operation_markers.{operation_id}": marker_document, "updated_at": utc_now()},
                },
            )
            if changed:
                latest = await self.storage.get_usage(user_id)
                return self._usage_model(latest or current, policy)
        raise StorageOperationBusyError("storage reservation conflicted with another operation")

    async def reserve(
        self,
        user_id: str,
        operation_id: str,
        reserve_bytes: int,
        *,
        quota_snapshot: int | None = None,
        commit_bytes: int | None = None,
        release_bytes: int = 0,
        roles: Iterable[str] | None = None,
    ) -> StorageUsage:
        """Reserve capacity with a version/CAS guarded marker."""
        if reserve_bytes < 0:
            raise ValueError("reserve_bytes must not be negative")
        if commit_bytes is not None and not 0 <= int(commit_bytes) <= reserve_bytes:
            raise ValueError("commit_bytes must be between zero and reserve_bytes")
        if release_bytes < 0:
            raise ValueError("release_bytes must not be negative")
        return await self._reserve_document(
            user_id,
            operation_id,
            reserve_bytes,
            quota_snapshot=quota_snapshot,
            commit_bytes=commit_bytes,
            release_bytes=release_bytes,
            roles=roles,
        )

    async def commit_reservation(
        self,
        user_id: str,
        operation_id: str,
        *,
        commit_bytes: int | None = None,
        active_file_delta: int = 1,
        roles: Iterable[str] | None = None,
    ) -> StorageUsage:
        operation_id = _clean_operation_id(operation_id)
        policy = await self.resolve_policy(user_id, roles)
        current = await self.storage.get_usage(user_id)
        if not current:
            raise StorageReconciliationRequiredError("storage ledger is missing")
        marker = (current.get("operation_markers") or {}).get(operation_id)
        if marker and marker.get("state") == "committed":
            if commit_bytes is not None and int(marker.get("commit_bytes", 0)) != int(commit_bytes):
                raise StorageOperationBusyError("storage commit payload conflicts with its idempotency marker")
            return self._usage_model(current, policy)
        if marker is None:
            raise StorageReconciliationRequiredError("storage reservation marker is missing")
        if marker.get("state") != "reserved":
            raise StorageOperationBusyError("storage operation is not reservable")
        reserve_bytes = int(marker.get("reserve_bytes", 0)) if marker else 0
        commit = int(commit_bytes if commit_bytes is not None else marker.get("commit_bytes", reserve_bytes))
        if commit < 0 or commit > reserve_bytes:
            raise StorageOperationBusyError("storage commit exceeds its reservation")
        if int(current.get("pending_bytes", 0)) < reserve_bytes:
            raise StorageReconciliationRequiredError("storage pending ledger is inconsistent")
        if int(current.get("active_file_count", 0)) + active_file_delta < 0:
            raise StorageReconciliationRequiredError("storage active file count would become negative")
        changed = await self.storage.update_usage(
            {
                "_id": user_id,
                "version": int(current.get("version", 0)),
                f"operation_markers.{operation_id}.state": "reserved",
            },
            {
                "$inc": {
                "pending_bytes": -reserve_bytes,
                    "used_bytes": commit,
                    "active_file_count": active_file_delta,
                    "version": 1,
                },
                "$set": {f"operation_markers.{operation_id}.state": "committed", "updated_at": utc_now()},
            },
        )
        if not changed:
            latest = await self.storage.get_usage(user_id)
            marker = (latest or {}).get("operation_markers", {}).get(operation_id)
            if marker and marker.get("state") == "committed":
                return self._usage_model(latest, policy)
            raise StorageOperationBusyError("storage commit conflicted with another operation")
        latest = await self.storage.get_usage(user_id)
        return self._usage_model(latest or current, policy)

    async def compensate_reservation(self, user_id: str, operation_id: str) -> StorageUsage:
        operation_id = _clean_operation_id(operation_id)
        policy = await self.resolve_policy(user_id)
        current = await self.storage.get_usage(user_id)
        if not current:
            raise StorageReconciliationRequiredError("storage ledger is missing")
        marker = (current.get("operation_markers") or {}).get(operation_id)
        if not marker or marker.get("state") == "compensated":
            return self._usage_model(current, policy)
        if marker.get("state") != "reserved":
            return self._usage_model(current, policy)
        reserve_bytes = int(marker.get("reserve_bytes", 0))
        changed = await self.storage.update_usage(
            {
                "_id": user_id,
                "version": int(current.get("version", 0)),
                f"operation_markers.{operation_id}.state": "reserved",
                "pending_bytes": {"$gte": reserve_bytes},
                "in_flight_count": {"$gt": 0},
            },
            {
                "$inc": {"pending_bytes": -reserve_bytes, "in_flight_count": -1, "version": 1},
                "$set": {f"operation_markers.{operation_id}.state": "compensated", "updated_at": utc_now()},
            },
        )
        if not changed:
            latest = await self.storage.get_usage(user_id)
            if latest and (latest.get("operation_markers") or {}).get(operation_id, {}).get("state") == "compensated":
                return self._usage_model(latest, policy)
            raise StorageOperationBusyError("storage compensation conflicted with another operation")
        latest = await self.storage.get_usage(user_id)
        return self._usage_model(latest or current, policy)

    async def release(
        self,
        user_id: str,
        operation_id: str,
        release_bytes: int,
        *,
        active_file_delta: int = -1,
    ) -> StorageUsage:
        """Release charged bytes once, guarded against negative usage."""
        operation_id = _clean_operation_id(operation_id)
        if release_bytes < 0:
            raise ValueError("release_bytes must not be negative")
        policy = await self.resolve_policy(user_id)
        current = await self.storage.get_usage(user_id)
        if not current:
            raise StorageReconciliationRequiredError("storage ledger is missing")
        marker = (current.get("operation_markers") or {}).get(operation_id)
        if marker and marker.get("state") == "released":
            if int(marker.get("release_bytes", 0)) != int(release_bytes):
                raise StorageOperationBusyError("storage release payload conflicts with its idempotency marker")
            return self._usage_model(current, policy)
        if marker and marker.get("state") not in {None, "reserved"}:
            return self._usage_model(current, policy)
        used = int(current.get("used_bytes", 0))
        if used < release_bytes:
            raise StorageReconciliationRequiredError("storage ledger would become negative")
        if int(current.get("active_file_count", 0)) + active_file_delta < 0:
            raise StorageReconciliationRequiredError("storage active file count would become negative")
        marker_document = {
            "state": "released",
            "reserve_bytes": 0,
            "commit_bytes": 0,
            "release_bytes": int(release_bytes),
            "created_at": utc_now(),
        }
        changed = await self.storage.update_usage(
            {
                "_id": user_id,
                "version": int(current.get("version", 0)),
                "used_bytes": used,
                "active_file_count": {"$gte": max(0, -active_file_delta)},
                f"operation_markers.{operation_id}": {"$exists": False},
            },
            {
                "$inc": {
                    "used_bytes": -int(release_bytes),
                    "active_file_count": active_file_delta,
                    "version": 1,
                },
                "$set": {f"operation_markers.{operation_id}": marker_document, "updated_at": utc_now()},
            },
        )
        if not changed:
            latest = await self.storage.get_usage(user_id)
            marker = (latest or {}).get("operation_markers", {}).get(operation_id)
            if marker and marker.get("state") == "released":
                return self._usage_model(latest, policy)
            raise StorageOperationBusyError("storage release conflicted with another operation")
        latest = await self.storage.get_usage(user_id)
        return self._usage_model(latest or current, policy)

    async def begin_operation(
        self,
        user_id: str,
        *,
        idempotency_key: str,
        kind: StorageOperationKind,
        source: StorageSource,
        items: list[OperationManifestItem | dict[str, Any]],
        size_bytes: int,
        source_ref: str | None = None,
        reserve_bytes: int | None = None,
        commit_bytes: int | None = None,
        release_bytes: int = 0,
        lease_owner: str | None = None,
        operation_id: str | None = None,
        roles: Iterable[str] | None = None,
        replaced_file_id: str | None = None,
        replaced_size: int = 0,
    ) -> dict[str, Any]:
        """Persist a bounded operation header and normalized item rows first."""
        key = _clean_idempotency_key(idempotency_key)
        if len(items) > MAX_OPERATION_ITEMS:
            raise StorageOperationTooLargeError(f"at most {MAX_OPERATION_ITEMS} operation items are allowed")
        normalized: list[OperationManifestItem] = []
        for index, item in enumerate(items):
            model = item if isinstance(item, OperationManifestItem) else OperationManifestItem(**item)
            normalized.append(model.model_copy(update={"item_index": index}))
        manifest_bytes = manifest_size_bytes(normalized)
        if manifest_bytes > MAX_OPERATION_MANIFEST_BYTES:
            raise StorageOperationTooLargeError("operation manifest exceeds 1 MiB")
        if source_ref is not None and len(str(source_ref).encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
            raise StorageOperationTooLargeError(
                f"source_ref exceeds {MAX_OPERATION_TEXT_BYTES} UTF-8 bytes"
            )
        if int(size_bytes) < 0 or int(reserve_bytes if reserve_bytes is not None else size_bytes) < 0:
            raise ValueError("operation byte counts must not be negative")
        if int(release_bytes) < 0:
            raise ValueError("operation release bytes must not be negative")
        if sum(item.size for item in normalized) != int(size_bytes):
            raise StorageOperationTooLargeError("operation item sizes do not match the manifest total")
        if int(commit_bytes if commit_bytes is not None else reserve_bytes if reserve_bytes is not None else size_bytes) < 0:
            raise ValueError("operation commit bytes must not be negative")
        if int(commit_bytes if commit_bytes is not None else reserve_bytes if reserve_bytes is not None else size_bytes) > int(
            reserve_bytes if reserve_bytes is not None else size_bytes
        ):
            raise ValueError("operation commit bytes must not exceed its reservation")
        if replaced_size < 0:
            raise ValueError("replaced_size must not be negative")
        normalized_roles: list[str] = []
        for role in list(roles or ())[:32]:
            clean_role = str(role).strip()
            if not clean_role or len(clean_role.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
                raise StorageOperationTooLargeError("operation role metadata is invalid or too long")
            normalized_roles.append(clean_role)
        digest = hashlib.sha256(
            json.dumps([item.model_dump(mode="json") for item in normalized], sort_keys=True).encode()
        ).hexdigest()
        await self.storage.ensure_indexes()
        existing = await self.storage.get_operation_by_key(user_id, key)
        if existing:
            if existing.get("state") in {
                StorageOperationState.FAILED.value,
                StorageOperationState.COMPENSATED.value,
            }:
                # A failed intent has no active charge.  Keep its audit row and
                # use a bounded retry key so a hash-derived upload idempotency
                # key can be retried after compensation.
                suffix = f":retry:{uuid.uuid4().hex}"
                prefix_bytes = max(1, MAX_IDEMPOTENCY_KEY_BYTES - len(suffix.encode("utf-8")))
                prefix = key.encode("utf-8")[:prefix_bytes].decode("utf-8", "ignore")
                key = f"{prefix}{suffix}"
                existing = None
            else:
                if existing.get("manifest_digest") not in (None, "", digest):
                    raise StorageOperationBusyError("idempotency key is already bound to another operation")
                return existing
        usage = await self.initialize_user(user_id, roles=normalized_roles)
        operation_id = _clean_operation_id(operation_id or uuid.uuid4().hex)
        now = utc_now()
        document = {
            "_id": operation_id,
            "operation_id": operation_id,
            "user_id": user_id,
            "idempotency_key": key,
            "kind": str(kind),
            "source": str(source),
            "source_ref": source_ref,
            "replaced_file_id": replaced_file_id,
            "replaced_size": int(replaced_size),
            "item_count": len(normalized),
            "manifest_digest": digest,
            "manifest_bytes": manifest_bytes,
            "size_bytes": int(size_bytes),
            "reserve_bytes": int(max(0, reserve_bytes if reserve_bytes is not None else size_bytes)),
            "commit_bytes": int(max(0, commit_bytes if commit_bytes is not None else size_bytes)),
            "release_bytes": int(max(0, release_bytes)),
            "quota_snapshot_bytes": int(usage.get("quota_bytes") or self._global_quota_bytes()),
            "usage_generation": int(usage.get("version", 0)),
            "roles": normalized_roles,
            "state": StorageOperationState.PREPARING.value,
            "version": 1,
            "lease_owner": lease_owner or uuid.uuid4().hex,
            "lease_expires_at": _now_plus(DEFAULT_OPERATION_LEASE_SECONDS),
            "retry_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        operation_items = []
        for model in normalized:
            item_id = uuid.uuid4().hex
            row = model.model_dump(mode="python")
            row.update(
                {
                    "_id": item_id,
                    "operation_item_id": item_id,
                    "operation_id": operation_id,
                    "operation_version": 1,
                    "state": "planned",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            operation_items.append(row)
        if not await self.storage.insert_operation(document):
            return (await self.storage.get_operation_by_key(user_id, key)) or document
        try:
            await self.storage.insert_operation_items(operation_items)
            await self.storage.update_operation(
                {"_id": operation_id, "version": 1, "state": StorageOperationState.PREPARING.value},
                {
                    "$set": {"state": StorageOperationState.INTENT.value, "updated_at": utc_now()},
                    "$inc": {"version": 1},
                },
            )
        except Exception:
            await self.storage.update_operation(
                {"_id": operation_id},
                {"$set": {"state": StorageOperationState.FAILED.value, "sanitized_error": "manifest persistence failed", "updated_at": utc_now()}},
            )
            raise
        return (await self.storage.get_operation(operation_id)) or document

    async def _set_operation_state(
        self,
        operation_id: str,
        expected: set[str],
        target: str,
        *,
        extra: dict[str, Any] | None = None,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        current = await self.storage.get_operation(operation_id)
        if not current:
            raise StorageOwnershipNotFoundError("operation not found")
        if current.get("state") == target:
            return current
        if current.get("state") not in expected:
            raise StorageOperationLeaseError("operation lease is no longer valid")
        lease_expires_at = current.get("lease_expires_at")
        if lease_expires_at is not None and lease_expires_at <= utc_now():
            raise StorageOperationLeaseError("operation lease has expired")
        if lease_owner is not None and current.get("lease_owner") != lease_owner:
            raise StorageOperationLeaseError("operation lease belongs to another worker")
        update_set = {"state": target, "updated_at": utc_now()}
        if extra:
            update_set.update(extra)
        changed = await self.storage.update_operation(
            {"_id": operation_id, "version": int(current.get("version", 1)), "state": {"$in": list(expected)}},
            {"$set": update_set, "$inc": {"version": 1}},
        )
        if not changed:
            latest = await self.storage.get_operation(operation_id)
            if latest and latest.get("state") == target:
                return latest
            raise StorageOperationLeaseError("operation lease was fenced")
        return (await self.storage.get_operation(operation_id)) or current

    async def _claim_operation_lease(self, operation_id: str, lease_owner: str) -> dict[str, Any]:
        """Acquire or renew a preparing lease with a versioned CAS."""
        current = await self.storage.get_operation(operation_id)
        if not current:
            raise StorageOwnershipNotFoundError("operation not found")
        if current.get("state") in {
            StorageOperationState.COMPLETED.value,
            StorageOperationState.COMPENSATED.value,
            StorageOperationState.FAILED.value,
        }:
            return current
        now = utc_now()
        current_owner = current.get("lease_owner")
        expires_at = current.get("lease_expires_at")
        if current_owner not in (None, lease_owner) and expires_at is not None and expires_at > now:
            raise StorageOperationBusyError("storage operation is owned by another worker")
        changed = await self.storage.update_operation(
            {
                "_id": operation_id,
                "version": int(current.get("version", 1)),
            },
            {
                "$set": {
                    "lease_owner": lease_owner,
                    "lease_expires_at": _now_plus(DEFAULT_OPERATION_LEASE_SECONDS),
                    "updated_at": now,
                },
                "$inc": {"version": 1},
            },
        )
        if not changed:
            latest = await self.storage.get_operation(operation_id)
            if latest and latest.get("lease_owner") == lease_owner:
                return latest
            raise StorageOperationLeaseError("operation lease was fenced")
        return (await self.storage.get_operation(operation_id)) or current

    async def _claim_replacement_lock(self, user_id: str, file_id: str, operation_id: str) -> bool:
        """Fence concurrent protected replacements on the old active owner."""
        changed = await self.storage.update_file(
            {
                "_id": file_id,
                "user_id": user_id,
                "status": FileLifecycleStatus.ACTIVE.value,
                "$or": [
                    {"replacement_operation_id": {"$exists": False}},
                    {"replacement_operation_id": operation_id},
                ],
            },
            {"$set": {"replacement_operation_id": operation_id, "updated_at": utc_now()}},
        )
        if changed:
            return True
        current = await self.storage.get_file(file_id)
        return bool(current and current.get("replacement_operation_id") == operation_id)

    async def _prune_committed_marker(self, user_id: str, operation_id: str) -> None:
        """Remove a completed marker without making a retry charge twice."""
        current = await self.storage.get_usage(user_id)
        if not current:
            raise StorageReconciliationRequiredError("storage ledger is missing")
        marker = (current.get("operation_markers") or {}).get(operation_id)
        if not marker:
            return
        if marker.get("state") not in {"committed", "released", "compensated"}:
            return
        marker_state = str(marker.get("state"))
        if marker_state in {"released", "compensated"}:
            changed = await self.storage.update_usage(
                {
                    "_id": user_id,
                    "version": int(current.get("version", 0)),
                    f"operation_markers.{operation_id}.state": marker_state,
                },
                {
                    "$unset": {f"operation_markers.{operation_id}": ""},
                    "$inc": {"version": 1},
                    "$set": {"updated_at": utc_now()},
                },
            )
            if not changed:
                latest = await self.storage.get_usage(user_id)
                if latest and operation_id not in (latest.get("operation_markers") or {}):
                    return
                raise StorageOperationBusyError("storage completion marker conflicted with another worker")
            return
        changed = await self.storage.update_usage(
            {
                "_id": user_id,
                "version": int(current.get("version", 0)),
                f"operation_markers.{operation_id}.state": "committed",
                "in_flight_count": {"$gt": 0},
            },
            {
                "$unset": {f"operation_markers.{operation_id}": ""},
                "$inc": {"in_flight_count": -1, "version": 1},
                "$set": {"updated_at": utc_now()},
            },
        )
        if not changed:
            latest = await self.storage.get_usage(user_id)
            if latest and operation_id not in (latest.get("operation_markers") or {}):
                return
            raise StorageOperationBusyError("storage completion marker conflicted with another worker")

    async def prepare_create(
        self,
        user_id: str,
        *,
        source: StorageSource,
        name: str,
        mime_type: str,
        category: str,
        size: int,
        content_hash: str,
        storage_key: str,
        idempotency_key: str,
        source_ref: str | None = None,
        file_id: str | None = None,
        blob_id: str | None = None,
        roles: Iterable[str] | None = None,
    ) -> PreparedStorageOperation:
        """Create a staged logical owner and reserve bytes before object write."""
        source = StorageSource(source)
        operation_key = _clean_idempotency_key(idempotency_key)
        existing_operation = await self.storage.get_operation_by_key(user_id, operation_key)
        resume_operation = existing_operation and existing_operation.get("state") not in {
            StorageOperationState.FAILED.value,
            StorageOperationState.COMPENSATED.value,
        }
        persisted_retry_item: dict[str, Any] | None = None
        if resume_operation and existing_operation:
            retry_items = await self.storage.get_operation_items(str(existing_operation.get("_id")))
            persisted_retry_item = retry_items[0] if retry_items else None
        existing = (
            await self.storage.find_active_by_hash(user_id, source, content_hash)
            if not resume_operation and (source in {StorageSource.CHAT, StorageSource.WECOM} or not source_ref)
            else None
        )
        if existing:
            if existing.get("status") != FileLifecycleStatus.ACTIVE.value:
                raise StorageOperationBusyError("an identical file is still being finalized")
            existing_blob = await self.storage.get_blob(str(existing.get("blob_id")))
            if not existing_blob or existing_blob.get("status") != BlobStatus.ACTIVE.value:
                raise StorageOperationBusyError("an identical file is still being finalized")
            return PreparedStorageOperation(
                operation_id=str(existing.get("create_operation_id", "")),
                file_id=str(existing.get("_id") or existing.get("file_id")),
                blob_id=str(existing.get("blob_id")),
                storage_key=str(existing.get("storage_key") or ""),
                size=int(existing.get("size", size)),
                reused=True,
                roles=tuple(str(role) for role in (roles or ()))[:32],
            )
        replaced = (
            await self.storage.find_active_by_source_ref(user_id, source, source_ref)
            if source_ref and source in {
                StorageSource.PROFILE_AVATAR,
                StorageSource.PERSONA_AVATAR,
                StorageSource.TEAM_AVATAR,
                StorageSource.SKILL,
            }
            else None
        )
        replaced_size = int(replaced.get("size", 0)) if replaced else 0
        if replaced and replaced.get("status") == FileLifecycleStatus.PENDING.value:
            raise StorageOperationBusyError("a replacement for this protected source is already pending")
        reserve_delta = max(size - replaced_size, 0)
        release_delta = max(replaced_size - size, 0)
        file_id = str((persisted_retry_item or {}).get("file_id") or file_id or uuid.uuid4().hex)
        blob_id = str((persisted_retry_item or {}).get("blob_id") or blob_id or uuid.uuid4().hex)
        storage_key = str((persisted_retry_item or {}).get("immutable_storage_key") or storage_key)
        write_generation = str((persisted_retry_item or {}).get("write_generation") or uuid.uuid4().hex)
        operation_lease_owner = uuid.uuid4().hex
        normalized_roles = tuple(str(role) for role in (roles or ()))[:32]
        operation = await self.begin_operation(
            user_id,
            idempotency_key=idempotency_key,
            kind=StorageOperationKind.CREATE,
            source=source,
            items=[
                OperationManifestItem(
                    file_id=file_id,
                    blob_id=blob_id,
                    immutable_storage_key=storage_key,
                    write_generation=write_generation,
                    source=source,
                    source_ref=source_ref,
                    name=name,
                    content_hash=content_hash,
                    mime_type=mime_type,
                    category=category,
                    size=size,
                )
            ],
            size_bytes=size,
            source_ref=source_ref,
            reserve_bytes=reserve_delta,
            commit_bytes=reserve_delta,
            release_bytes=release_delta,
            lease_owner=operation_lease_owner,
            roles=normalized_roles,
            replaced_file_id=str(replaced.get("_id") or replaced.get("file_id")) if replaced else None,
            replaced_size=replaced_size,
        )
        operation_id = str(operation.get("_id") or operation.get("operation_id"))
        operation_items = await self.storage.get_operation_items(operation_id)
        if not operation_items:
            raise StorageReconciliationRequiredError("storage operation manifest is missing")
        persisted_item = operation_items[0]
        # A retry may arrive after the original worker has acquired the lease;
        # always resume the persisted generation instead of creating an orphan.
        file_id = str(persisted_item.get("file_id") or file_id)
        blob_id = str(persisted_item.get("blob_id") or blob_id)
        storage_key = str(persisted_item.get("immutable_storage_key") or storage_key)
        size = int(persisted_item.get("size", size))
        reserve_delta = int(operation.get("reserve_bytes", reserve_delta))
        release_delta = int(operation.get("release_bytes", release_delta))
        replaced_file_id = operation.get("replaced_file_id") or (
            str(replaced.get("_id") or replaced.get("file_id")) if replaced else None
        )
        replaced_size = int(operation.get("replaced_size", replaced_size))
        if operation.get("state") == StorageOperationState.COMPLETED.value:
            return PreparedStorageOperation(
                operation_id,
                file_id,
                blob_id,
                storage_key,
                size,
                True,
                roles=tuple(str(role) for role in (operation.get("roles") or normalized_roles))[:32],
                lease_owner=str(operation.get("lease_owner") or operation_lease_owner),
            )
        if operation.get("state") in {
            StorageOperationState.FAILED.value,
            StorageOperationState.COMPENSATED.value,
        }:
            raise StorageOperationBusyError("idempotency key refers to a terminal failed operation")
        if replaced_file_id and not await self._claim_replacement_lock(user_id, replaced_file_id, operation_id):
            await self.compensate_create(user_id, operation_id)
            raise StorageOperationBusyError("a protected replacement is owned by another operation")
        operation = await self._claim_operation_lease(operation_id, operation_lease_owner)
        if operation.get("state") == StorageOperationState.INTENT.value:
            await self._set_operation_state(
                operation_id,
                {StorageOperationState.INTENT.value},
                StorageOperationState.INTENT.value,
                lease_owner=operation_lease_owner,
            )
        usage = await self.reserve(
            user_id,
            operation_id,
            reserve_delta,
            quota_snapshot=int(operation.get("quota_snapshot_bytes") or 0) or None,
            commit_bytes=reserve_delta,
            release_bytes=release_delta,
            roles=normalized_roles,
        )
        _ = usage
        if operation.get("state") == StorageOperationState.INTENT.value:
            await self._set_operation_state(
                operation_id,
                {StorageOperationState.INTENT.value},
                StorageOperationState.RESERVED.value,
                lease_owner=operation_lease_owner,
            )
        items = await self.storage.get_operation_items(operation_id)
        item = items[0]
        now = utc_now()
        generation = str(item.get("write_generation") or uuid.uuid4().hex)
        blob = {
            "_id": item["blob_id"],
            "blob_id": item["blob_id"],
            "storage_key": item["immutable_storage_key"],
            "content_hash": content_hash,
            "size": size,
            "mime_type": mime_type,
            "category": category,
            "status": BlobStatus.STAGED.value,
            "ownership_complete": True,
            "write_operation_id": operation_id,
            "write_generation": generation,
            "pending_owner_operation_ids": [operation_id],
            "created_at": now,
            "updated_at": now,
        }
        owner = {
            "_id": item["file_id"],
            "file_id": item["file_id"],
            "user_id": user_id,
            "blob_id": item["blob_id"],
            "storage_key": item["immutable_storage_key"],
            "source": str(source),
            "source_ref": source_ref,
            "name": name,
            "mime_type": mime_type,
            "size": size,
            "category": category,
            "content_hash": content_hash,
            "status": FileLifecycleStatus.PENDING.value,
            "is_user_deletable": source in {StorageSource.CHAT, StorageSource.WECOM},
            "create_operation_id": operation_id,
            "quota_committed": False,
            "quota_released": False,
            "created_at": now,
            "updated_at": now,
        }
        if not await self.storage.insert_blob(blob):
            existing_blob = await self.storage.get_blob(item["blob_id"])
            if not existing_blob or existing_blob.get("write_operation_id") != operation_id:
                await self.compensate_create(user_id, operation_id)
                raise StorageOperationBusyError("blob generation is already owned by another operation")
        if not await self.storage.insert_file(owner):
            existing_owner = await self.storage.get_file(item["file_id"])
            if not existing_owner or existing_owner.get("create_operation_id") != operation_id:
                await self.compensate_create(user_id, operation_id)
                raise StorageOperationBusyError("file generation is already owned by another operation")
        return PreparedStorageOperation(
            operation_id,
            item["file_id"],
            item["blob_id"],
            item["immutable_storage_key"],
            size,
            commit_bytes=reserve_delta,
            release_bytes=release_delta,
            replaced_file_id=replaced_file_id,
            replaced_size=replaced_size,
            roles=normalized_roles,
            lease_owner=operation_lease_owner,
        )

    async def prepare_group_create(
        self,
        user_id: str,
        *,
        source: StorageSource,
        items: list[dict[str, Any]],
        idempotency_key: str,
        roles: Iterable[str] | None = None,
    ) -> list[PreparedStorageOperation]:
        """Stage a bounded group under one intent and one quota reservation.

        Skill ZIP/copy callers must not reserve each member independently: doing so
        can expose a partially-installed Skill and makes compensation depend on
        how many members happened to finish.  This method owns the aggregate
        manifest, reservation, staged owners and operation lease.
        """

        source = StorageSource(source)
        if not items or len(items) > MAX_OPERATION_ITEMS:
            raise StorageOperationTooLargeError("group storage operation has an invalid item count")

        manifest: list[OperationManifestItem] = []
        for item in items:
            size = int(item.get("size", 0))
            if size < 0:
                raise StorageOperationTooLargeError("group item size must not be negative")
            manifest.append(
                OperationManifestItem(
                    file_id=str(item.get("file_id") or uuid.uuid4().hex),
                    blob_id=str(item.get("blob_id") or uuid.uuid4().hex),
                    immutable_storage_key=str(
                        item.get("storage_key")
                        or f"managed/{source.value}/{user_id}/{uuid.uuid4().hex}"
                    ),
                    write_generation=str(item.get("write_generation") or uuid.uuid4().hex),
                    source=source,
                    source_ref=item.get("source_ref"),
                    name=str(item.get("name") or "attachment"),
                    content_hash=str(item.get("content_hash") or ""),
                    mime_type=str(item.get("mime_type") or "application/octet-stream"),
                    category=str(item.get("category") or source.value),
                    size=size,
                    replaced_file_id=item.get("replaced_file_id"),
                    replaced_size=int(item.get("replaced_size", 0) or 0),
                )
            )
        if source in {
            StorageSource.PROFILE_AVATAR,
            StorageSource.PERSONA_AVATAR,
            StorageSource.TEAM_AVATAR,
            StorageSource.SKILL,
        }:
            for index, item in enumerate(manifest):
                if not item.source_ref:
                    continue
                existing = await self.storage.find_active_by_source_ref(
                    user_id,
                    source,
                    item.source_ref,
                )
                if existing and existing.get("status") == FileLifecycleStatus.PENDING.value:
                    raise StorageOperationBusyError("a protected replacement is already pending")
                if existing:
                    manifest[index] = item.model_copy(
                        update={
                            "replaced_file_id": str(existing.get("_id") or existing.get("file_id")),
                            "replaced_size": int(existing.get("size", 0)),
                        }
                    )
        total_size = sum(item.size for item in manifest)
        total_reserve = sum(max(item.size - item.replaced_size, 0) for item in manifest)
        total_release = sum(max(item.replaced_size - item.size, 0) for item in manifest)
        operation = await self.begin_operation(
            user_id,
            idempotency_key=idempotency_key,
            kind=StorageOperationKind.GROUP_CREATE,
            source=source,
            items=manifest,
            size_bytes=total_size,
            reserve_bytes=total_reserve,
            commit_bytes=total_reserve,
            release_bytes=total_release,
            roles=roles,
        )
        operation_id = str(operation.get("_id") or operation.get("operation_id"))
        persisted = await self.storage.get_operation_items(operation_id)
        if len(persisted) != len(manifest):
            raise StorageReconciliationRequiredError("group storage operation manifest is incomplete")
        operation = (await self.storage.get_operation(operation_id)) or operation
        operation_state = str(operation.get("state"))
        operation_lease_owner = str(operation.get("lease_owner") or uuid.uuid4().hex)

        if operation_state in {
            StorageOperationState.FAILED.value,
            StorageOperationState.COMPENSATED.value,
        }:
            raise StorageOperationBusyError("group storage operation is terminal and cannot be resumed")
        if operation_state == StorageOperationState.COMPLETED.value:
            return [
                PreparedStorageOperation(
                    operation_id=operation_id,
                    file_id=str(row["file_id"]),
                    blob_id=str(row["blob_id"]),
                    storage_key=str(row["immutable_storage_key"]),
                    size=int(row.get("size", 0)),
                    reused=True,
                    roles=tuple(str(role) for role in (roles or operation.get("roles") or ()))[:32],
                    lease_owner=operation_lease_owner,
                )
                for row in persisted
            ]

        operation = await self._claim_operation_lease(operation_id, operation_lease_owner)
        replacement_ids = [
            str(row.get("replaced_file_id"))
            for row in persisted
            if row.get("replaced_file_id")
        ]
        for replacement_id in replacement_ids:
            if not await self._claim_replacement_lock(user_id, replacement_id, operation_id):
                await self.compensate_create(user_id, operation_id)
                raise StorageOperationBusyError("a protected replacement is owned by another operation")
        if operation.get("state") == StorageOperationState.INTENT.value:
            await self.reserve(
                user_id,
                operation_id,
                total_reserve,
                quota_snapshot=int(operation.get("quota_snapshot_bytes") or 0) or None,
                commit_bytes=total_reserve,
                release_bytes=total_release,
                roles=roles,
            )
            await self._set_operation_state(
                operation_id,
                {StorageOperationState.INTENT.value},
                StorageOperationState.RESERVED.value,
                lease_owner=operation_lease_owner,
            )

        now = utc_now()
        prepared: list[PreparedStorageOperation] = []
        try:
            for row in persisted:
                blob = {
                    "_id": row["blob_id"],
                    "blob_id": row["blob_id"],
                    "storage_key": row["immutable_storage_key"],
                    "content_hash": row["content_hash"],
                    "size": int(row["size"]),
                    "mime_type": row["mime_type"],
                    "category": row["category"],
                    "status": BlobStatus.STAGED.value,
                    "ownership_complete": True,
                    "write_operation_id": operation_id,
                    "write_generation": row["write_generation"],
                    "pending_owner_operation_ids": [operation_id],
                    "created_at": now,
                    "updated_at": now,
                }
                owner = {
                    "_id": row["file_id"],
                    "file_id": row["file_id"],
                    "user_id": user_id,
                    "blob_id": row["blob_id"],
                    "storage_key": row["immutable_storage_key"],
                    "source": source.value,
                    "source_ref": row.get("source_ref"),
                    "name": row["name"],
                    "mime_type": row["mime_type"],
                    "size": int(row["size"]),
                    "category": row["category"],
                    "content_hash": row["content_hash"],
                    "status": FileLifecycleStatus.PENDING.value,
                    "is_user_deletable": source in {StorageSource.CHAT, StorageSource.WECOM},
                    "create_operation_id": operation_id,
                    "quota_committed": False,
                    "quota_released": False,
                    "created_at": now,
                    "updated_at": now,
                }
                if not await self.storage.insert_blob(blob):
                    existing_blob = await self.storage.get_blob(str(row["blob_id"]))
                    if not existing_blob or existing_blob.get("write_operation_id") != operation_id:
                        raise StorageOperationBusyError("group blob generation is already owned")
                if not await self.storage.insert_file(owner):
                    existing_owner = await self.storage.get_file(str(row["file_id"]))
                    if not existing_owner or existing_owner.get("create_operation_id") != operation_id:
                        raise StorageOperationBusyError("group file generation is already owned")
                prepared.append(
                    PreparedStorageOperation(
                        operation_id=operation_id,
                        file_id=str(row["file_id"]),
                        blob_id=str(row["blob_id"]),
                        storage_key=str(row["immutable_storage_key"]),
                        size=int(row["size"]),
                        commit_bytes=total_reserve,
                        release_bytes=total_release,
                        roles=tuple(str(role) for role in (roles or operation.get("roles") or ()))[:32],
                        lease_owner=operation_lease_owner,
                    )
                )
        except Exception:
            # The operation is still pre-commit, so the normal compensation path
            # can remove every staged row and the single aggregate reservation.
            await self.compensate_create(user_id, operation_id)
            raise
        return prepared

    async def complete_group_create(
        self,
        prepared: list[PreparedStorageOperation],
        *,
        roles: Iterable[str] | None = None,
    ) -> tuple[UserFile, StorageUsage] | None:
        """Commit one staged group after all physical members have been written."""

        if not prepared:
            return None
        first = prepared[0]
        operation = await self.storage.get_operation(first.operation_id)
        if not operation:
            raise StorageOwnershipNotFoundError("group storage operation not found")
        owner = await self.storage.get_file(first.file_id)
        if not owner:
            raise StorageOwnershipNotFoundError("group staged file not found")
        user_id = str(owner["user_id"])
        effective_roles = tuple(str(role) for role in (roles or first.roles))[:32]
        if operation.get("state") == StorageOperationState.COMPLETED.value:
            return UserFile.model_validate(owner), await self.get_usage(user_id, roles=effective_roles)
        lease_owner = first.lease_owner or str(operation.get("lease_owner") or uuid.uuid4().hex)
        operation = await self._claim_operation_lease(first.operation_id, lease_owner)
        operation_items = await self.storage.get_operation_items(first.operation_id)
        replacement_ids = [
            str(item.get("replaced_file_id"))
            for item in operation_items
            if item.get("replaced_file_id")
        ]
        listable_item_count = sum(self._is_listable_source(item.get("source")) for item in operation_items)
        listable_replacement_count = sum(
            self._is_listable_source(item.get("source")) for item in operation_items if item.get("replaced_file_id")
        )
        state = str(operation.get("state"))
        if state == StorageOperationState.RESERVED.value:
            await self._set_operation_state(
                first.operation_id,
                {StorageOperationState.RESERVED.value},
                StorageOperationState.OBJECT_WRITTEN.value,
                lease_owner=lease_owner,
            )
            state = StorageOperationState.OBJECT_WRITTEN.value
        if state == StorageOperationState.OBJECT_WRITTEN.value:
            usage = await self.commit_reservation(
                user_id,
                first.operation_id,
                commit_bytes=int(operation.get("commit_bytes", 0)),
                active_file_delta=listable_item_count,
                roles=effective_roles,
            )
            await self._set_operation_state(
                first.operation_id,
                {StorageOperationState.OBJECT_WRITTEN.value},
                StorageOperationState.QUOTA_COMMITTED.value,
                lease_owner=lease_owner,
            )
        elif state in {StorageOperationState.QUOTA_COMMITTED.value, StorageOperationState.COMPLETING.value}:
            usage = await self.get_usage(user_id, roles=effective_roles)
        else:
            raise StorageOperationLeaseError("group storage operation is not ready for completion")

        for item in prepared:
            await self.storage.update_file(
                {"_id": item.file_id, "user_id": user_id, "status": FileLifecycleStatus.PENDING.value},
                {"$set": {"status": FileLifecycleStatus.ACTIVE.value, "quota_committed": True, "updated_at": utc_now()}},
            )
            await self.storage.update_blob(
                {"_id": item.blob_id, "write_operation_id": first.operation_id},
                {"$set": {"status": BlobStatus.ACTIVE.value, "pending_owner_operation_ids": [], "updated_at": utc_now()}},
            )
        for replacement_id in replacement_ids:
            old_owner = await self.storage.get_file(replacement_id)
            if not old_owner:
                continue
            await self.storage.update_file(
                {
                    "_id": replacement_id,
                    "user_id": user_id,
                    "status": {"$in": [FileLifecycleStatus.ACTIVE.value, FileLifecycleStatus.DELETE_PENDING.value]},
                    "replacement_operation_id": first.operation_id,
                },
                {
                    "$set": {
                        "status": FileLifecycleStatus.DELETED.value,
                        "quota_released": True,
                        "deleted_at": utc_now(),
                        "deleted_reason": "replaced",
                        "updated_at": utc_now(),
                    },
                    "$unset": {"replacement_operation_id": ""},
                },
            )
            await self.storage.update_blob(
                {"_id": old_owner.get("blob_id"), "status": {"$in": [BlobStatus.ACTIVE.value, BlobStatus.STAGED.value]}},
                {"$set": {"status": BlobStatus.PURGE_PENDING.value, "next_purge_at": utc_now(), "updated_at": utc_now()}},
            )
        if replacement_ids:
            release_operation_id = f"group-release:{first.operation_id}"
            usage = await self.release(
                user_id,
                release_operation_id,
                int(operation.get("release_bytes", 0)),
                active_file_delta=-listable_replacement_count,
            )
            await self._prune_committed_marker(user_id, release_operation_id)
        await self._set_operation_state(
            first.operation_id,
            {StorageOperationState.QUOTA_COMMITTED.value, StorageOperationState.COMPLETING.value},
            StorageOperationState.COMPLETED.value,
            extra={"completed_at": utc_now()},
            lease_owner=lease_owner,
        )
        await self._prune_committed_marker(user_id, first.operation_id)
        owner = await self.storage.get_file(first.file_id)
        return UserFile.model_validate(owner or {}), usage

    async def complete_create(
        self,
        prepared: PreparedStorageOperation,
        *,
        roles: Iterable[str] | None = None,
    ) -> tuple[UserFile, StorageUsage]:
        """Commit charge before activation and resume safely after a crash."""
        operation = await self.storage.get_operation(prepared.operation_id)
        owner = await self.storage.get_file(prepared.file_id)
        if not owner:
            raise StorageOwnershipNotFoundError("staged file not found")
        user_id = str(owner["user_id"])
        effective_roles = tuple(str(role) for role in (roles or prepared.roles))[:32]
        if operation and operation.get("state") == StorageOperationState.COMPLETED.value:
            await self._prune_committed_marker(user_id, prepared.operation_id)
            return UserFile.model_validate(owner), await self.get_usage(user_id, roles=effective_roles)
        if operation and operation.get("state") in {
            StorageOperationState.FAILED.value,
            StorageOperationState.COMPENSATED.value,
        }:
            raise StorageOperationLeaseError("operation is terminal and cannot be completed")
        if not operation:
            raise StorageOwnershipNotFoundError("operation not found")
        listable_source = self._is_listable_source(owner.get("source"))

        lease_owner = prepared.lease_owner or str(operation.get("lease_owner") or uuid.uuid4().hex)
        operation = await self._claim_operation_lease(prepared.operation_id, lease_owner)
        if operation.get("state") == StorageOperationState.COMPLETED.value:
            await self._prune_committed_marker(user_id, prepared.operation_id)
            owner = await self.storage.get_file(prepared.file_id)
            return UserFile.model_validate(owner or {}), await self.get_usage(user_id, roles=effective_roles)
        if operation.get("state") in {
            StorageOperationState.FAILED.value,
            StorageOperationState.COMPENSATED.value,
        }:
            raise StorageOperationLeaseError("operation is terminal and cannot be completed")
        state = str(operation.get("state"))
        if state == StorageOperationState.RESERVED.value:
            operation = await self._set_operation_state(
                prepared.operation_id,
                {StorageOperationState.RESERVED.value},
                StorageOperationState.OBJECT_WRITTEN.value,
                lease_owner=lease_owner,
            )
            state = str(operation.get("state"))
        elif state not in {
            StorageOperationState.OBJECT_WRITTEN.value,
            StorageOperationState.QUOTA_COMMITTED.value,
            StorageOperationState.COMPLETING.value,
        }:
            raise StorageOperationLeaseError("operation is not ready for completion")

        marker = (await self.storage.get_usage(user_id) or {}).get("operation_markers", {}).get(prepared.operation_id)
        if marker and marker.get("state") == "reserved":
            usage = await self.commit_reservation(
                user_id,
                prepared.operation_id,
                commit_bytes=(prepared.commit_bytes if prepared.commit_bytes is not None else prepared.size),
                active_file_delta=int(listable_source),
                roles=effective_roles,
            )
            operation = await self._set_operation_state(
                prepared.operation_id,
                {StorageOperationState.OBJECT_WRITTEN.value},
                StorageOperationState.QUOTA_COMMITTED.value,
                lease_owner=lease_owner,
            )
        elif marker and marker.get("state") == "committed":
            usage = await self.get_usage(user_id, roles=effective_roles)
            if state == StorageOperationState.OBJECT_WRITTEN.value:
                operation = await self._set_operation_state(
                    prepared.operation_id,
                    {StorageOperationState.OBJECT_WRITTEN.value},
                    StorageOperationState.QUOTA_COMMITTED.value,
                    lease_owner=lease_owner,
                )
        else:
            raise StorageReconciliationRequiredError("storage commit marker is missing")

        if prepared.replaced_file_id:
            # Keep the replacement pending until its owning domain has swapped
            # the pointer; the active-only source-ref index still permits one
            # pending generation beside the old active generation.
            await self.storage.update_file(
                {"_id": prepared.file_id, "status": FileLifecycleStatus.PENDING.value},
                {"$set": {"quota_committed": True, "updated_at": utc_now()}},
            )
        else:
            await self.storage.update_file(
                {
                    "_id": prepared.file_id,
                    "status": {"$in": [FileLifecycleStatus.PENDING.value, FileLifecycleStatus.ACTIVE.value]},
                },
                {"$set": {"status": FileLifecycleStatus.ACTIVE.value, "quota_committed": True, "updated_at": utc_now()}},
            )
            await self.storage.update_blob(
                {"_id": prepared.blob_id, "write_operation_id": prepared.operation_id},
                {"$set": {"status": BlobStatus.ACTIVE.value, "pending_owner_operation_ids": [], "updated_at": utc_now()}},
            )
        completion_state = (
            StorageOperationState.COMPLETING.value
            if prepared.replaced_file_id
            else StorageOperationState.COMPLETED.value
        )
        if operation.get("state") != completion_state:
            await self._set_operation_state(
                prepared.operation_id,
                {StorageOperationState.QUOTA_COMMITTED.value, StorageOperationState.COMPLETING.value},
                completion_state,
                extra={"completed_at": None if prepared.replaced_file_id else utc_now()},
                lease_owner=lease_owner,
            )
        if completion_state == StorageOperationState.COMPLETED.value:
            await self._prune_committed_marker(user_id, prepared.operation_id)
        owner = await self.storage.get_file(prepared.file_id)
        return UserFile.model_validate(owner or {}), usage

    async def finalize_replace(self, prepared: PreparedStorageOperation) -> StorageUsage:
        """Retire the old protected generation after its domain pointer CAS."""
        if not prepared.replaced_file_id:
            owner = await self.storage.get_file(prepared.file_id)
            if not owner:
                raise StorageOwnershipNotFoundError("replacement owner not found")
            return await self.get_usage(str(owner.get("user_id")))
        owner = await self.storage.get_file(prepared.file_id)
        if not owner:
            raise StorageOwnershipNotFoundError("replacement owner not found")
        user_id = str(owner["user_id"])
        operation = await self.storage.get_operation(prepared.operation_id)
        if operation and operation.get("state") == StorageOperationState.COMPLETED.value:
            return await self.get_usage(user_id, roles=prepared.roles)
        if operation:
            lease_owner = prepared.lease_owner or str(operation.get("lease_owner") or uuid.uuid4().hex)
            operation = await self._claim_operation_lease(prepared.operation_id, lease_owner)
            if operation.get("state") == StorageOperationState.COMPLETED.value:
                await self._prune_committed_marker(user_id, prepared.operation_id)
                return await self.get_usage(user_id, roles=prepared.roles)
        await self.storage.update_file(
            {
                "_id": prepared.replaced_file_id,
                "user_id": user_id,
                "status": {"$in": [FileLifecycleStatus.ACTIVE.value, FileLifecycleStatus.DELETE_PENDING.value]},
            },
            {
                "$set": {
                    "status": FileLifecycleStatus.DELETED.value,
                    "quota_released": True,
                    "deleted_at": utc_now(),
                    "deleted_reason": "replaced",
                    "updated_at": utc_now(),
                },
                "$unset": {"replacement_operation_id": ""},
            },
        )
        await self.storage.update_file(
            {
                "_id": prepared.file_id,
                "user_id": user_id,
                "status": {"$in": [FileLifecycleStatus.PENDING.value, FileLifecycleStatus.ACTIVE.value]},
            },
            {"$set": {"status": FileLifecycleStatus.ACTIVE.value, "quota_committed": True, "updated_at": utc_now()}},
        )
        await self.storage.update_blob(
            {"_id": prepared.blob_id, "write_operation_id": prepared.operation_id},
            {"$set": {"status": BlobStatus.ACTIVE.value, "pending_owner_operation_ids": [], "updated_at": utc_now()}},
        )
        release_operation_id = f"replace-release:{prepared.operation_id}"
        usage = await self.release(
            user_id,
            release_operation_id,
            prepared.release_bytes,
            active_file_delta=-int(self._is_listable_source(owner.get("source"))),
        )
        await self._prune_committed_marker(user_id, release_operation_id)
        if operation:
            await self._set_operation_state(
                prepared.operation_id,
                {StorageOperationState.COMPLETING.value, StorageOperationState.QUOTA_COMMITTED.value},
                StorageOperationState.COMPLETED.value,
                extra={"completed_at": utc_now()},
                lease_owner=lease_owner,
            )
            await self._prune_committed_marker(user_id, prepared.operation_id)
        return usage

    async def compensate_create(self, user_id: str, operation_id: str) -> StorageUsage:
        operation = await self.storage.get_operation(operation_id)
        if operation and operation.get("state") == StorageOperationState.COMPLETED.value:
            return await self.get_usage(user_id)
        if operation and operation.get("state") in {
            StorageOperationState.QUOTA_COMMITTED.value,
            StorageOperationState.COMPLETING.value,
        }:
            raise StorageReconciliationRequiredError("committed storage operation must be completed forward")
        if operation and operation.get("replaced_file_id"):
            await self.storage.update_file(
                {
                    "_id": operation["replaced_file_id"],
                    "user_id": user_id,
                    "replacement_operation_id": operation_id,
                },
                {"$unset": {"replacement_operation_id": ""}, "$set": {"updated_at": utc_now()}},
            )
        rows = await self.storage.get_operation_items(operation_id)
        for row in rows:
            await self.storage.update_file(
                {"_id": row.get("file_id"), "user_id": user_id, "status": FileLifecycleStatus.PENDING.value},
                {"$set": {"status": FileLifecycleStatus.FAILED.value, "updated_at": utc_now()}},
            )
            await self.storage.update_blob(
                {"_id": row.get("blob_id"), "write_operation_id": operation_id},
                {
                    "$set": {
                        "status": BlobStatus.PURGE_PENDING.value,
                        "next_purge_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                },
            )
        usage = await self.compensate_reservation(user_id, operation_id)
        await self.storage.update_operation(
            {"_id": operation_id},
            {"$set": {"state": StorageOperationState.COMPENSATED.value, "completed_at": utc_now(), "updated_at": utc_now()}},
        )
        await self._prune_committed_marker(user_id, operation_id)
        return usage

    async def recover_operation(
        self,
        user_id: str,
        operation_id: str,
        *,
        object_storage: Any | None = None,
    ) -> StorageUsage | None:
        """Forward an object-written operation after a worker crash."""
        operation = await self.storage.get_operation(operation_id)
        if not operation or operation.get("user_id") != user_id:
            return None
        if operation.get("state") not in {
            StorageOperationState.OBJECT_WRITTEN.value,
            StorageOperationState.QUOTA_COMMITTED.value,
            StorageOperationState.COMPLETING.value,
        }:
            return None
        rows = await self.storage.get_operation_items(operation_id)
        if not rows:
            raise StorageReconciliationRequiredError("storage operation has no normalized item")
        item = rows[0]
        blob = await self.storage.get_blob(str(item.get("blob_id")))
        if not blob:
            raise StorageReconciliationRequiredError("storage operation blob is missing")
        if object_storage is not None and operation.get("state") == StorageOperationState.OBJECT_WRITTEN.value:
            if not await object_storage.file_exists(str(item.get("immutable_storage_key"))):
                return await self.compensate_create(user_id, operation_id)
        prepared = PreparedStorageOperation(
            operation_id=operation_id,
            file_id=str(item.get("file_id")),
            blob_id=str(item.get("blob_id")),
            storage_key=str(item.get("immutable_storage_key")),
            size=int(item.get("size", 0)),
            commit_bytes=int(operation.get("commit_bytes", 0)),
            release_bytes=int(operation.get("release_bytes", 0)),
            replaced_file_id=operation.get("replaced_file_id"),
            replaced_size=int(operation.get("replaced_size", 0)),
            lease_owner=str(operation.get("lease_owner") or uuid.uuid4().hex),
        )
        if str(operation.get("kind")) == StorageOperationKind.GROUP_CREATE.value:
            group_prepared = [
                PreparedStorageOperation(
                    operation_id=operation_id,
                    file_id=str(group_item.get("file_id")),
                    blob_id=str(group_item.get("blob_id")),
                    storage_key=str(group_item.get("immutable_storage_key")),
                    size=int(group_item.get("size", 0)),
                    commit_bytes=int(operation.get("commit_bytes", 0)),
                    release_bytes=int(operation.get("release_bytes", 0)),
                    roles=tuple(str(role) for role in (operation.get("roles") or ()))[:32],
                    lease_owner=str(operation.get("lease_owner") or uuid.uuid4().hex),
                )
                for group_item in rows
            ]
            completed = await self.complete_group_create(group_prepared)
            return completed[1] if completed else None
        _owner, usage = await self.complete_create(prepared)
        return usage

    async def delete_file(
        self,
        user_id: str,
        file_id: str,
        *,
        idempotency_key: str | None = None,
        reason: str = "user_requested",
        allow_protected: bool = False,
    ) -> dict[str, Any]:
        owner = await self.storage.get_owned_file(user_id, file_id, include_deleted=True, allow_storage_key=False)
        if not owner:
            return {"file_id": file_id, "logical_status": "not_found", "released_bytes": 0, "physical_status": "unknown"}
        status = str(owner.get("status"))
        if not bool(owner.get("is_user_deletable", True)) and not allow_protected:
            return {"file_id": file_id, "logical_status": "managed_by_source", "released_bytes": 0, "physical_status": "protected"}
        if status == FileLifecycleStatus.DELETED.value:
            blob = await self.storage.get_blob(str(owner.get("blob_id")))
            physical_status = "queued"
            if not blob:
                physical_status = "missing"
            elif blob.get("status") == BlobStatus.PURGED.value:
                physical_status = "purged"
            elif not bool(blob.get("ownership_complete", True)):
                physical_status = "quarantined"
            elif await self.storage.owner_count(str(owner.get("blob_id"))) > 0:
                physical_status = "shared"
            return {"file_id": file_id, "logical_status": "already_deleted", "released_bytes": 0, "physical_status": physical_status, "status": status}
        if status == FileLifecycleStatus.PENDING.value and not bool(owner.get("quota_committed")):
            return {"file_id": file_id, "logical_status": "busy", "released_bytes": 0, "physical_status": "queued"}
        operation_id = f"delete:{user_id}:{file_id}"
        durable_operation_id: str | None = None
        if status == FileLifecycleStatus.DELETE_PENDING.value:
            operation_id = str(owner.get("delete_operation_id") or operation_id)
            durable_operation_id = operation_id
        else:
            try:
                delete_operation = await self.begin_operation(
                    user_id,
                    idempotency_key=idempotency_key or operation_id,
                    kind=StorageOperationKind.DELETE,
                    source=StorageSource(str(owner.get("source") or StorageSource.CHAT.value)),
                    source_ref=owner.get("source_ref"),
                    items=[],
                    size_bytes=0,
                    release_bytes=int(owner.get("size", 0)),
                    operation_id=operation_id,
                )
                durable_operation_id = str(delete_operation.get("_id") or delete_operation.get("operation_id"))
                if durable_operation_id != operation_id:
                    raise StorageOperationBusyError("delete idempotency key is already in use")
            except ValueError:
                # A quarantined/legacy source may not map to a modern enum.  The
                # delete intent still remains represented by its deterministic key.
                pass
            changed = await self.storage.update_file(
                {
                    "_id": file_id,
                    "user_id": user_id,
                    "status": {"$in": [FileLifecycleStatus.ACTIVE.value, FileLifecycleStatus.PENDING.value]},
                },
                {
                    "$set": {
                        "status": FileLifecycleStatus.DELETE_PENDING.value,
                        "delete_operation_id": operation_id,
                        "updated_at": utc_now(),
                        "deleted_reason": reason,
                    }
                },
            )
            if not changed:
                latest = await self.storage.get_owned_file(user_id, file_id, include_deleted=True, allow_storage_key=False)
                if latest and latest.get("status") == FileLifecycleStatus.DELETED.value:
                    return {"file_id": file_id, "logical_status": "already_deleted", "released_bytes": 0, "physical_status": "queued", "status": latest.get("status")}
                if latest and latest.get("status") == FileLifecycleStatus.DELETE_PENDING.value:
                    owner = latest
                    operation_id = str(latest.get("delete_operation_id") or operation_id)
                    durable_operation_id = operation_id
                else:
                    return {"file_id": file_id, "logical_status": "busy", "released_bytes": 0, "physical_status": "queued"}
        await self.release(
            user_id,
            operation_id,
            int(owner.get("size", 0)),
            active_file_delta=-int(self._is_listable_source(owner.get("source"))),
        )
        await self.storage.update_file(
            {"_id": file_id, "user_id": user_id, "status": FileLifecycleStatus.DELETE_PENDING.value},
            {"$set": {"status": FileLifecycleStatus.DELETED.value, "quota_released": True, "deleted_at": utc_now(), "updated_at": utc_now()}},
        )
        if durable_operation_id:
            await self.storage.update_operation(
                {"_id": durable_operation_id},
                {"$set": {"state": StorageOperationState.COMPLETED.value, "completed_at": utc_now(), "updated_at": utc_now()}},
            )
        await self._prune_committed_marker(user_id, operation_id)
        blob = await self.storage.get_blob(str(owner.get("blob_id")))
        if not blob:
            physical_status = "missing"
        elif blob.get("status") == BlobStatus.PURGED.value:
            physical_status = "purged"
        elif not bool(blob.get("ownership_complete", True)):
            physical_status = "quarantined"
        elif await self.storage.owner_count(str(owner.get("blob_id"))) > 0:
            physical_status = "shared"
        else:
            await self.storage.update_blob(
                {"_id": owner.get("blob_id"), "status": {"$in": [BlobStatus.ACTIVE.value, BlobStatus.STAGED.value]}},
                {"$set": {"status": BlobStatus.PURGE_PENDING.value, "next_purge_at": utc_now(), "updated_at": utc_now()}},
            )
            physical_status = "queued"
        return {
            "file_id": file_id,
            "logical_status": "deleted",
            "released_bytes": int(owner.get("size", 0)),
            "physical_status": physical_status,
            "status": FileLifecycleStatus.DELETED.value,
        }

    async def status_for_user(self, user_id: str, file_ids: list[str], keys: list[str]) -> list[dict[str, Any]]:
        identifiers = list(dict.fromkeys([*file_ids, *keys]))[:100]
        results: list[dict[str, Any]] = []
        for identifier in identifiers:
            row = await self.storage.get_owned_file(user_id, identifier, include_deleted=True, allow_storage_key=True)
            if row:
                results.append(row)
        return results

    async def delete_protected_file(
        self, user_id: str, file_id: str, *, reason: str = "owner_requested"
    ) -> dict[str, Any]:
        """Delete a protected generation only from its owning domain."""
        return await self.delete_file(user_id, file_id, reason=reason, allow_protected=True)

    async def bind_protected_file(
        self,
        user_id: str,
        file_id: str,
        source_ref: str,
    ) -> bool:
        """Bind a draft protected upload to its persisted owning entity."""

        source_ref = str(source_ref or "").strip()
        if not source_ref or len(source_ref.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
            raise StorageOperationTooLargeError("protected source reference is invalid")
        owner = await self.storage.get_owned_file(user_id, file_id, include_deleted=True, allow_storage_key=False)
        if not owner or owner.get("source") not in {
            StorageSource.PROFILE_AVATAR.value,
            StorageSource.PERSONA_AVATAR.value,
            StorageSource.TEAM_AVATAR.value,
            StorageSource.SKILL.value,
        }:
            raise StorageOwnershipNotFoundError("protected file not found")
        return await self.storage.update_file(
            {
                "_id": file_id,
                "user_id": user_id,
                "status": {"$in": [FileLifecycleStatus.PENDING.value, FileLifecycleStatus.ACTIVE.value]},
            },
            {"$set": {"source_ref": source_ref, "updated_at": utc_now()}},
        )

    async def finalize_replacement_for_file(self, user_id: str, file_id: str) -> StorageUsage:
        """Finish a protected pointer swap after its owning document is durable."""

        owner = await self.storage.get_owned_file(user_id, file_id, include_deleted=True, allow_storage_key=False)
        if not owner:
            raise StorageOwnershipNotFoundError("protected replacement owner not found")
        operation = await self.storage.get_operation(str(owner.get("create_operation_id")))
        replaced_file_id = operation.get("replaced_file_id") if operation else None
        if not replaced_file_id:
            return await self.get_usage(user_id)
        rows = await self.storage.get_operation_items(str(operation.get("_id")))
        row = next((candidate for candidate in rows if str(candidate.get("file_id")) == file_id), None)
        if row is None:
            raise StorageReconciliationRequiredError("protected replacement manifest is missing")
        prepared = PreparedStorageOperation(
            operation_id=str(operation.get("_id")),
            file_id=file_id,
            blob_id=str(row.get("blob_id")),
            storage_key=str(row.get("immutable_storage_key")),
            size=int(row.get("size", owner.get("size", 0))),
            commit_bytes=int(operation.get("commit_bytes", 0)),
            release_bytes=int(operation.get("release_bytes", 0)),
            replaced_file_id=str(replaced_file_id),
            replaced_size=int(operation.get("replaced_size", 0)),
            roles=tuple(str(role) for role in (operation.get("roles") or ()))[:32],
            lease_owner=str(operation.get("lease_owner") or uuid.uuid4().hex),
        )
        return await self.finalize_replace(prepared)

    async def register_message_refs(
        self,
        *,
        event_id: str,
        session_id: str,
        user_id: str,
        file_ids: list[str],
    ) -> int:
        """Persist bounded idempotent history references without affecting quota."""
        event_id = str(event_id or "").strip()
        session_id = str(session_id or "").strip()
        user_id = str(user_id or "").strip()
        if (
            not event_id
            or not session_id
            or not user_id
            or len(event_id.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES
            or len(session_id.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES
            or len(user_id.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES
        ):
            return 0
        count = 0
        for file_id in list(dict.fromkeys(file_ids))[:100]:
            if not file_id:
                continue
            file_id = str(file_id).strip()
            if not file_id or len(file_id.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
                continue
            # Never allow a client/event to manufacture a reference to another
            # user's file. Deleted rows remain valid tombstone references.
            if await self.storage.get_owned_file(
                user_id,
                file_id,
                include_deleted=True,
                allow_storage_key=False,
            ) is None:
                continue
            try:
                await self.storage.message_ref_collection.insert_one(
                    {
                        "_id": f"{event_id}:{file_id}",
                        "event_id": event_id,
                        "session_id": session_id,
                        "user_id": user_id,
                        "file_id": file_id,
                        "created_at": utc_now(),
                    }
                )
                count += 1
            except DuplicateKeyError:
                continue
        return count

    async def remove_session_message_refs(self, *, session_id: str, user_id: str) -> int:
        if (
            not session_id
            or not user_id
            or len(str(session_id).encode("utf-8")) > MAX_OPERATION_TEXT_BYTES
            or len(str(user_id).encode("utf-8")) > MAX_OPERATION_TEXT_BYTES
        ):
            return 0
        collection = self.storage.message_ref_collection
        if hasattr(collection, "delete_many"):
            result = await collection.delete_many({"session_id": session_id, "user_id": user_id})
            return int(getattr(result, "deleted_count", 0))
        return 0

    async def list_files(
        self,
        user_id: str,
        query: StorageFileListQuery,
        *,
        roles: Iterable[str] | None = None,
    ) -> tuple[list[UserFile], bool, StorageUsage]:
        rows, has_more = await self.storage.list_files(user_id, query)
        usage = await self.get_usage(user_id, roles=roles)
        return [UserFile.model_validate(row) for row in rows], has_more, usage

    async def get_content_file(self, identifier: str, *, allow_storage_key: bool = False) -> dict[str, Any] | None:
        clean = str(identifier or "").strip()
        if not clean or len(clean.encode("utf-8")) > MAX_OPERATION_TEXT_BYTES:
            return None
        row = await self.storage.get_file(clean, include_deleted=True)
        if row is None and allow_storage_key:
            cursor = self.storage.file_collection.find({"storage_key": clean}).limit(2)
            rows = await _cursor_documents(cursor)
            row = rows[0] if len(rows) == 1 else None
        return row

    async def purge_blob(self, blob_id: str, storage: Any, *, worker_id: str | None = None) -> str:
        """Generation/lease guarded physical cleanup; never revives a tombstone."""
        blob = await self.storage.get_blob(blob_id)
        if not blob:
            return "missing"
        if not blob.get("ownership_complete", True):
            return "quarantined"
        if blob.get("pending_owner_operation_ids"):
            return "shared"
        if await self.storage.owner_count(blob_id) > 0:
            return "shared"
        worker_id = worker_id or uuid.uuid4().hex
        now = utc_now()
        generation = str(blob.get("purge_generation") or uuid.uuid4().hex)
        leased = await self.storage.update_blob(
            {
                "_id": blob_id,
                "status": BlobStatus.PURGE_PENDING.value,
                "purge_generation": blob.get("purge_generation"),
                "$or": [
                    {"purge_lease_owner": {"$exists": False}},
                    {"purge_lease_owner": None},
                    {"purge_lease_expires_at": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "purge_generation": generation,
                    "purge_lease_owner": worker_id,
                    "purge_lease_expires_at": _now_plus(DEFAULT_PURGE_LEASE_SECONDS),
                    "updated_at": now,
                }
            },
        )
        if not leased:
            return "busy"
        # A new owner may have committed while the purge lease was acquired;
        # recheck after the CAS before touching the physical object.
        refreshed_blob = await self.storage.get_blob(blob_id)
        if (refreshed_blob or {}).get("pending_owner_operation_ids") or await self.storage.owner_count(blob_id) > 0:
            await self.storage.update_blob(
                {"_id": blob_id, "purge_generation": generation, "purge_lease_owner": worker_id},
                {
                    "$set": {
                        "status": BlobStatus.ACTIVE.value,
                        "purge_lease_owner": None,
                        "purge_lease_expires_at": None,
                        "updated_at": utc_now(),
                    }
                },
            )
            return "shared"
        try:
            await storage.delete_file(str(blob["storage_key"]))
        except Exception as exc:
            await self.storage.update_blob(
                {"_id": blob_id, "purge_generation": generation, "purge_lease_owner": worker_id},
                {
                    "$set": {
                        "status": BlobStatus.PURGE_PENDING.value,
                        "last_purge_error": str(exc)[:2048],
                        "next_purge_at": _now_plus(60),
                        "purge_lease_owner": None,
                        "purge_lease_expires_at": None,
                        "updated_at": utc_now(),
                    },
                    "$inc": {"purge_attempts": 1},
                },
            )
            return "failed"
        await self.storage.update_blob(
            {"_id": blob_id, "purge_generation": generation, "purge_lease_owner": worker_id},
            {
                "$set": {
                    "status": BlobStatus.PURGED.value,
                    "purged_at": utc_now(),
                    "purge_lease_owner": None,
                    "purge_lease_expires_at": None,
                    "updated_at": utc_now(),
                }
            },
        )
        return "purged"


# A shorter name is useful at route boundaries and retained as an explicit API.
StorageDomainService = UserStorageQuotaService
QuotaService = UserStorageQuotaService

_DEFAULT_STORAGE_SERVICE: UserStorageQuotaService | None = None


def get_user_storage_service() -> UserStorageQuotaService:
    """Return the process-local domain facade used by source adapters."""
    global _DEFAULT_STORAGE_SERVICE
    if _DEFAULT_STORAGE_SERVICE is None:
        _DEFAULT_STORAGE_SERVICE = UserStorageQuotaService()
    return _DEFAULT_STORAGE_SERVICE


def get_managed_storage_service() -> UserStorageQuotaService:
    """Discovery hook for Skill/WeCom integration without collection imports."""
    return get_user_storage_service()
