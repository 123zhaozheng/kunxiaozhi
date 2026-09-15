"""File record storage for content-hash based deduplication."""

import asyncio
from typing import Any, Optional

from src.infra.logging import get_logger
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

REFERENCE_KEYS_MAX = 100


def _bounded_unique_keys(keys: list[str], *, limit: int = REFERENCE_KEYS_MAX) -> list[str]:
    unique_keys: list[str] = []
    seen = set()
    for key in keys:
        clean = str(key).strip() if key else ""
        if not clean or len(clean.encode("utf-8")) > 1024 or clean in seen:
            continue
        seen.add(clean)
        unique_keys.append(clean)
        if len(unique_keys) >= limit:
            break
    return unique_keys


def _owner_scope(user_id: str, source: str | None = None) -> dict[str, Any]:
    """Match both additive and pre-migration owner fields without leaking rows."""
    owners = ("user_id", "uploaded_by")
    if source is None:
        return {"$or": [{field: user_id} for field in owners]}

    # Older file_records documents have no source field.  They were created by
    # the main upload path, so they remain eligible only in that user's scope;
    # never fall back to a global hash/key lookup.
    return {
        "$or": [
            {field: user_id, "source": source}
            for field in owners
        ]
        + [
            {field: user_id, "source": {"$exists": False}}
            for field in owners
        ]
        + [
            {field: user_id, "source": "legacy"}
            for field in owners
        ]
    }


class FileRecordStorage:
    """Storage layer for file records, keyed by content hash."""

    REFERENCE_KEYS_MAX = REFERENCE_KEYS_MAX

    def __init__(self):
        self._collection = None
        self._indexes_ensured = False
        self._indexes_task: asyncio.Task[bool] | None = None

    @property
    def collection(self):
        """Lazy-load MongoDB collection."""
        if self._collection is None:
            from src.infra.storage.mongodb import get_mongo_client

            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            self._collection = db["file_records"]
        return self._collection

    async def ensure_indexes_if_needed(self):
        """Ensure indexes exist (called lazily on first use)."""
        if self._indexes_ensured:
            return
        task = self._indexes_task
        if task is None or task.done():
            task = asyncio.create_task(self._ensure_indexes())
            self._indexes_task = task
        try:
            succeeded = await task
        except Exception:
            if task is self._indexes_task:
                self._indexes_task = None
            raise
        if task is self._indexes_task:
            self._indexes_ensured = succeeded
            if succeeded:
                self._indexes_task = None

    async def _ensure_indexes(self) -> bool:
        """Create required indexes on the file_records collection."""
        succeeded = True
        try:
            collection = self.collection
            await collection.create_index("hash", unique=True, background=True)
            await collection.create_index("key", unique=True, background=True)
            await collection.create_index("uploaded_by", background=True)
            # These indexes are additive.  The legacy global hash uniqueness is
            # intentionally retained as migration input; managed uploads use
            # user_files for owner-scoped deduplication.
            await collection.create_index([("user_id", 1), ("source", 1), ("hash", 1)], background=True)
            await collection.create_index([("user_id", 1), ("status", 1), ("created_at", -1)], background=True)
        except Exception as e:
            get_logger(__name__).warning(f"Failed to create file_records indexes: {e}")
            succeeded = False
        return succeeded

    async def find_by_hash(
        self,
        file_hash: str,
        *,
        user_id: str | None = None,
        source: str | None = None,
        include_deleted: bool = False,
    ) -> Optional[dict]:
        """Look up a file record by content hash.

        Args:
            file_hash: SHA-256 hex digest.

        Returns:
            Document dict with ``id`` (instead of ``_id``), or None.
        """
        await self.ensure_indexes_if_needed()
        query: dict[str, Any] = {"hash": file_hash}
        if user_id is not None:
            query.update(_owner_scope(user_id, source))
        elif source is not None:
            query["source"] = source
        if not include_deleted:
            query["status"] = {"$nin": ["deleted", "delete_pending"]}
        doc = await self.collection.find_one(query)
        if doc:
            doc["id"] = str(doc.pop("_id"))
        return doc

    async def find_by_key(
        self,
        key: str,
        *,
        user_id: str | None = None,
        include_deleted: bool = False,
    ) -> Optional[dict]:
        """Look up a file record by storage key.

        Args:
            key: Storage object key (e.g. "category/user_id/uuid.ext").

        Returns:
            Document dict with ``id`` (instead of ``_id``), or None.
        """
        await self.ensure_indexes_if_needed()
        query: dict[str, Any] = {"key": key}
        if user_id is not None:
            query.update(_owner_scope(user_id))
        if not include_deleted:
            query["status"] = {"$nin": ["deleted", "delete_pending"]}
        doc = await self.collection.find_one(query)
        if doc:
            doc["id"] = str(doc.pop("_id"))
        return doc

    async def create(
        self,
        file_hash: str,
        key: str,
        name: str,
        mime_type: str,
        size: int,
        category: str,
        uploaded_by: str,
        *,
        user_id: str | None = None,
        source: str = "legacy",
        file_id: str | None = None,
        status: str = "active",
    ) -> dict:
        """Insert a new file record.

        Args:
            file_hash: SHA-256 hex digest.
            key: Storage object key (e.g. "user_id/abc123hash").
            name: Original filename.
            mime_type: MIME type of the file.
            size: File size in bytes.
            category: One of "image", "video", "audio", "document".
            uploaded_by: User ID of the uploader.

        Returns:
            Document dict with ``id`` field.
        """
        await self.ensure_indexes_if_needed()
        now = utc_now()
        doc = {
            "hash": file_hash,
            "key": key,
            "name": name,
            "mime_type": mime_type,
            "size": size,
            "category": category,
            "uploaded_by": uploaded_by,
            "user_id": user_id or uploaded_by,
            "source": source,
            "file_id": file_id,
            "status": status,
            "reference_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        result = await self.collection.insert_one(doc)
        doc["id"] = str(result.inserted_id)
        return doc

    async def add_references(self, keys: list[str]) -> int:
        """Increment persisted message references for the given storage keys."""
        unique_keys = _bounded_unique_keys(keys)
        if not unique_keys:
            return 0

        await self.ensure_indexes_if_needed()
        result = await self.collection.update_many(
            {"key": {"$in": unique_keys}},
            {"$inc": {"reference_count": 1}, "$set": {"updated_at": utc_now()}},
        )
        return result.modified_count

    async def release_references(self, keys: list[str]) -> int:
        """Decrement persisted message references for the given storage keys."""
        unique_keys = _bounded_unique_keys(keys)
        if not unique_keys:
            return 0

        await self.ensure_indexes_if_needed()
        result = await self.collection.update_many(
            {
                "key": {"$in": unique_keys},
                "reference_count": {"$gt": 0},
            },
            {"$inc": {"reference_count": -1}, "$set": {"updated_at": utc_now()}},
        )
        return result.modified_count

    async def delete_by_key(
        self,
        key: str,
        *,
        user_id: str | None = None,
        logical: bool = False,
    ) -> bool:
        """Delete a file record by storage key.

        Args:
            key: Storage object key.

        Returns:
            True if a document was deleted, False otherwise.
        """
        await self.ensure_indexes_if_needed()
        query: dict[str, Any] = {"key": key}
        if user_id is not None:
            query.update(_owner_scope(user_id))
        if logical:
            result = await self.collection.update_one(
                {**query, "status": {"$nin": ["deleted", "delete_pending"]}},
                {"$set": {"status": "deleted", "deleted_at": utc_now(), "updated_at": utc_now()}},
            )
            return bool(getattr(result, "modified_count", 0))
        else:
            result = await self.collection.delete_one(query)
        return result.deleted_count > 0

    async def delete_by_hash(self, file_hash: str, *, user_id: str | None = None) -> bool:
        """Delete a file record by content hash.

        Args:
            file_hash: SHA-256 hex digest.

        Returns:
            True if a document was deleted, False otherwise.
        """
        await self.ensure_indexes_if_needed()
        query: dict[str, Any] = {"hash": file_hash}
        if user_id is not None:
            query.update(_owner_scope(user_id))
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0

    async def list_for_user(
        self,
        user_id: str,
        *,
        limit: int = 50,
        cursor: Any = None,
        source: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """List legacy records only within one owner scope."""
        await self.ensure_indexes_if_needed()
        limit = max(1, min(int(limit), 100))
        query: dict[str, Any] = _owner_scope(user_id)
        if source:
            query["source"] = source
        if status:
            query["status"] = status
        elif status is None:
            query["status"] = {"$nin": ["deleted"]}
        if cursor is not None:
            query["_id"] = {"$lt": cursor}
        cursor_obj = self.collection.find(query).sort("_id", -1).limit(limit)
        records: list[dict] = []
        async for doc in cursor_obj:
            doc["id"] = str(doc.pop("_id"))
            records.append(doc)
        return records

    async def status_for_user(self, user_id: str, keys: list[str]) -> list[dict]:
        """Return bounded status rows without revealing other owners."""
        clean_keys = list(
            dict.fromkeys(
                clean
                for key in keys
                if (clean := str(key).strip()) and len(clean.encode("utf-8")) <= 1024
            )
        )[:100]
        if not clean_keys:
            return []
        await self.ensure_indexes_if_needed()
        owner_query = _owner_scope(user_id)
        owner_query["key"] = {"$in": clean_keys}
        cursor = self.collection.find(owner_query)
        rows: list[dict] = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            rows.append(doc)
        return rows
