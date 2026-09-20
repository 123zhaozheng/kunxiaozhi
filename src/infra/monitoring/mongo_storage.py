"""MongoDB metadata storage metrics."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pymongo.errors import OperationFailure

from src.infra.checkpoint.cleanup_worker import CHECKPOINT_CLEANUP_MIN_RETENTION_DAYS
from src.infra.logging import get_logger
from src.infra.storage import mongodb as mongodb_storage
from src.infra.storage.checkpoint import (
    CHECKPOINT_COLLECTION_NAME,
    CHECKPOINT_WRITES_COLLECTION_NAME,
    get_checkpointer_diagnostics,
    is_checkpoint_backend_enabled,
)
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

logger = get_logger(__name__)


def get_mongo_client() -> Any:
    """Return the canonical Motor client, keeping the dependency patchable in tests."""
    return mongodb_storage.get_mongo_client()


_CLEANUP_DEFAULT_RETENTION_DAYS = 30
_CLEANUP_DEFAULT_INTERVAL_HOURS = 24
_CLEANUP_COUNT_MAX_TIME_MS = 2000


def _error_message(error: Exception) -> str:
    message = str(error).strip()
    return message or type(error).__name__


def _collection_names() -> list[str]:
    return [
        CHECKPOINT_COLLECTION_NAME,
        CHECKPOINT_WRITES_COLLECTION_NAME,
        settings.MONGODB_SESSIONS_COLLECTION,
        settings.MONGODB_TRACES_COLLECTION,
    ]


def _database_metrics(
    *, available: bool, error: str | None = None, **values: Any
) -> dict[str, Any]:
    return {
        "name": settings.MONGODB_DB,
        "available": available,
        "data_size": values.get("data_size"),
        "storage_size": values.get("storage_size"),
        "index_size": values.get("index_size"),
        "objects": values.get("objects"),
        "collections": values.get("collections"),
        "error": error,
    }


def _collection_metrics(
    name: str,
    *,
    available: bool,
    error: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "available": available,
        "size": values.get("size"),
        "storage_size": values.get("storage_size"),
        "total_index_size": values.get("total_index_size"),
        "count": values.get("count"),
        "error": error,
    }


def _effective_cleanup_settings() -> tuple[bool, int, float]:
    enabled = bool(getattr(settings, "CHECKPOINT_CLEANUP_ENABLED", False))
    retention_days = max(
        int(
            getattr(
                settings,
                "CHECKPOINT_CLEANUP_RETENTION_DAYS",
                _CLEANUP_DEFAULT_RETENTION_DAYS,
            )
            or 0
        ),
        CHECKPOINT_CLEANUP_MIN_RETENTION_DAYS,
    )
    interval_hours = max(
        float(
            getattr(
                settings,
                "CHECKPOINT_CLEANUP_INTERVAL_HOURS",
                _CLEANUP_DEFAULT_INTERVAL_HOURS,
            )
            or 0
        ),
        1.0,
    )
    return enabled, retention_days, interval_hours


def _cleanup_metrics(
    *,
    available: bool,
    error: str | None = None,
    backlog_sessions: int | None = None,
) -> dict[str, Any]:
    enabled, retention_days, interval_hours = _effective_cleanup_settings()
    return {
        "enabled": enabled,
        "retention_days": retention_days,
        "interval_hours": interval_hours,
        "backlog_sessions": backlog_sessions,
        "approximate": True,
        "available": available,
        "error": error,
    }


def _checkpoint_backend_metrics() -> dict[str, Any]:
    try:
        diagnostics = get_checkpointer_diagnostics()
        backend = diagnostics.get("configured_backend", "mongodb")
        enabled = is_checkpoint_backend_enabled()
    except Exception as exc:
        logger.warning("[MongoStorage] Checkpoint diagnostics failed: %s", exc)
        backend = str(getattr(settings, "CHECKPOINT_BACKEND", "mongodb"))
        enabled = False
    return {"backend": backend, "enabled": enabled}


def build_mongo_storage_unavailable_response(error: Exception | str) -> dict[str, Any]:
    """Build the stable response shape used when Mongo cannot be acquired."""
    reason = _error_message(error) if isinstance(error, Exception) else str(error)
    return {
        "available": False,
        "database": _database_metrics(available=False, error=reason),
        "collections": [
            _collection_metrics(name, available=False, error=reason) for name in _collection_names()
        ],
        "cleanup": _cleanup_metrics(available=False, error=reason),
        "checkpoint_backend": _checkpoint_backend_metrics(),
    }


def _storage_values(document: dict[str, Any]) -> dict[str, Any]:
    storage = document.get("storageStats") or document
    return {
        "size": storage.get("size", 0),
        "storage_size": storage.get("storageSize", 0),
        "total_index_size": storage.get("totalIndexSize", 0),
        "count": storage.get("count", 0),
    }


def _sum_storage_values(documents: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"size": 0, "storage_size": 0, "total_index_size": 0, "count": 0}
    for document in documents:
        values = _storage_values(document)
        for key in totals:
            totals[key] += values[key]
    return totals


async def _get_collection_metrics(db: Any, name: str) -> dict[str, Any]:
    try:
        collection = db[name]
        cursor = collection.aggregate([{"$collStats": {"storageStats": {}}}])
        documents = await cursor.to_list(length=None)
        values = _sum_storage_values(documents)
        return _collection_metrics(name, available=True, error=None, **values)
    except OperationFailure:
        try:
            values = _storage_values(await db.command("collStats", name))
            return _collection_metrics(name, available=True, error=None, **values)
        except Exception as fallback_error:
            logger.warning(
                "[MongoStorage] Collection %s metadata query failed after collStats fallback: %s",
                name,
                fallback_error,
            )
            return _collection_metrics(
                name,
                available=False,
                error=_error_message(fallback_error),
            )
    except Exception as exc:
        logger.warning("[MongoStorage] Collection %s metadata query failed: %s", name, exc)
        return _collection_metrics(name, available=False, error=_error_message(exc))


async def _get_database_metrics(db: Any) -> dict[str, Any]:
    try:
        stats = await db.command("dbStats")
        return _database_metrics(
            available=True,
            error=None,
            data_size=stats.get("dataSize", 0),
            storage_size=stats.get("storageSize", 0),
            index_size=stats.get("indexSize", 0),
            objects=stats.get("objects", 0),
            collections=stats.get("collections", 0),
        )
    except Exception as exc:
        logger.warning("[MongoStorage] Database metadata query failed: %s", exc)
        return _database_metrics(available=False, error=_error_message(exc))


async def _get_cleanup_metrics(db: Any) -> dict[str, Any]:
    try:
        enabled, retention_days, interval_hours = _effective_cleanup_settings()
        cutoff = utc_now() - timedelta(days=retention_days)
        backlog_sessions = await db[settings.MONGODB_SESSIONS_COLLECTION].count_documents(
            {"updated_at": {"$lt": cutoff}},
            maxTimeMS=_CLEANUP_COUNT_MAX_TIME_MS,
        )
        return {
            "enabled": enabled,
            "retention_days": retention_days,
            "interval_hours": interval_hours,
            "backlog_sessions": backlog_sessions,
            "approximate": True,
            "available": True,
            "error": None,
        }
    except Exception as exc:
        logger.warning("[MongoStorage] Cleanup backlog query failed: %s", exc)
        return _cleanup_metrics(available=False, error=_error_message(exc))


async def get_mongodb_storage_metrics() -> dict[str, Any]:
    """Return MongoDB storage metadata while degrading each metric independently."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB]
    except Exception as exc:
        logger.warning("[MongoStorage] MongoDB connection unavailable: %s", exc)
        return build_mongo_storage_unavailable_response(exc)

    try:
        database = await _get_database_metrics(db)
        cleanup = await _get_cleanup_metrics(db)
        collections = [await _get_collection_metrics(db, name) for name in _collection_names()]
        return {
            "available": True,
            "database": database,
            "collections": collections,
            "cleanup": cleanup,
            "checkpoint_backend": _checkpoint_backend_metrics(),
        }
    except Exception as exc:
        logger.warning("[MongoStorage] Metrics collection failed: %s", exc)
        return build_mongo_storage_unavailable_response(exc)


__all__ = [
    "build_mongo_storage_unavailable_response",
    "get_mongodb_storage_metrics",
]
