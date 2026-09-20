"""Backfill hourly usage from immutable daily analytics snapshots."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.infra.analytics.snapshot import (
    _COMPLETE_MARKER_USER_ID,
    SNAPSHOT_COLLECTION_NAME,
)
from src.infra.analytics.usage_hourly import UsageHourlyStorage
from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.storage.redis import create_redis_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

logger = get_logger(__name__)

USAGE_HOURLY_BACKFILL_LOCK_KEY = "analytics:usage-hourly-backfill:lock"
USAGE_HOURLY_BACKFILL_LOCK_TTL_SECONDS = 300
USAGE_HOURLY_BACKFILL_BATCH_DAYS = 7
USAGE_HOURLY_BACKFILL_BATCH_MAX = 365
USAGE_HOURLY_BACKFILL_LOCK_RENEW_INTERVAL_SECONDS = (
    USAGE_HOURLY_BACKFILL_LOCK_TTL_SECONDS / 3
)
USAGE_HOURLY_BACKFILL_STATE_COLLECTION = "analytics_usage_hourly_backfill_state"
USAGE_HOURLY_BACKFILL_STATE_ID = "usage_hourly"

BACKFILL_LOCK_KEY = USAGE_HOURLY_BACKFILL_LOCK_KEY
BACKFILL_LOCK_TTL_SECONDS = USAGE_HOURLY_BACKFILL_LOCK_TTL_SECONDS
BACKFILL_BATCH_DAYS = USAGE_HOURLY_BACKFILL_BATCH_DAYS
STATE_COLLECTION = USAGE_HOURLY_BACKFILL_STATE_COLLECTION
STATE_DOC_ID = USAGE_HOURLY_BACKFILL_STATE_ID

_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
_RENEW_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


def _parse_snapshot_date(value: Any) -> tuple[str, datetime] | None:
    if isinstance(value, datetime):
        snapshot_date = value.astimezone(timezone.utc).date()
    elif isinstance(value, date):
        snapshot_date = value
    elif isinstance(value, str):
        try:
            snapshot_date = date.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    date_string = snapshot_date.isoformat()
    return date_string, datetime.combine(snapshot_date, datetime.min.time(), timezone.utc)


class UsageHourlyBackfillWorker:
    """Copy immutable snapshot metrics into model-less hourly rows."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        snapshot_collection: Any | None = None,
        usage_collection: Any | None = None,
        usage_storage: UsageHourlyStorage | None = None,
        state_collection: Any | None = None,
        batch_days: int | None = None,
        batch_delay_seconds: float = 0.0,
        lock_ttl_seconds: int = USAGE_HOURLY_BACKFILL_LOCK_TTL_SECONDS,
        renew_interval_seconds: float | None = None,
        now_factory: Callable[[], datetime] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._redis = redis_client
        self._snapshot_collection = snapshot_collection
        self._usage_storage = usage_storage or UsageHourlyStorage(collection=usage_collection)
        self._state_collection = state_collection
        self._batch_days = batch_days
        self._batch_delay_seconds = max(float(batch_delay_seconds), 0.0)
        self._lock_ttl_seconds = max(int(lock_ttl_seconds or 0), 1)
        self._renew_interval_seconds = (
            float(renew_interval_seconds)
            if renew_interval_seconds is not None
            else self._lock_ttl_seconds / 3
        )
        self._now_factory = now_factory or utc_now
        self._enabled = enabled
        self._lock_value: str | None = None
        self._lock_lost = False
        self._renew_task: asyncio.Task[None] | None = None
        self._cursor_date: str | None = None

    @property
    def enabled(self) -> bool:
        if self._enabled is not None:
            return bool(self._enabled)
        return bool(getattr(settings, "ANALYTICS_USAGE_HOURLY_ENABLED", False))

    @property
    def batch_days(self) -> int:
        configured = (
            self._batch_days
            if self._batch_days is not None
            else getattr(
                settings,
                "ANALYTICS_USAGE_HOURLY_BACKFILL_BATCH_DAYS",
                USAGE_HOURLY_BACKFILL_BATCH_DAYS,
            )
        )
        return min(max(int(configured or 0), 1), USAGE_HOURLY_BACKFILL_BATCH_MAX)

    @property
    def interval_seconds(self) -> float:
        configured = getattr(settings, "ANALYTICS_USAGE_HOURLY_FLUSH_SECONDS", 300)
        return min(max(float(configured or 0), 1.0), 86400.0)

    async def run_once(self) -> int:
        """Process one bounded date range while holding the Redis lease."""
        if not self.enabled:
            return 0

        redis_client: Any | None = None
        lock_value = str(uuid.uuid4())
        self._lock_value = lock_value
        try:
            redis_client = self._get_redis()
            acquired = await redis_client.set(
                USAGE_HOURLY_BACKFILL_LOCK_KEY,
                lock_value,
                nx=True,
                ex=self._lock_ttl_seconds,
            )
            if not acquired:
                logger.debug("[Analytics] Usage hourly backfill lock is held by another instance")
                return 0
            self._lock_lost = False
            self._renew_task = asyncio.create_task(self._renew_lock_loop())
            return await self._process_batch()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[Analytics] Usage hourly backfill batch failed: %s", exc)
            return 0
        finally:
            if redis_client is not None:
                try:
                    await self._stop_lock_renewal()
                except Exception as exc:
                    logger.warning("[Analytics] Usage hourly backfill renewal stop failed: %s", exc)
                try:
                    await redis_client.eval(
                        _RELEASE_LOCK_LUA,
                        1,
                        USAGE_HOURLY_BACKFILL_LOCK_KEY,
                        lock_value,
                    )
                except Exception as exc:
                    logger.warning("[Analytics] Failed to release usage hourly backfill lock: %s", exc)
            self._lock_value = None

    async def run_until_complete(self) -> int:
        """Run date batches until the snapshot range is exhausted."""
        total_days = 0
        while True:
            processed = await self.run_once()
            if processed <= 0:
                return total_days
            total_days += processed
            if self._batch_delay_seconds:
                await asyncio.sleep(self._batch_delay_seconds)

    async def run_forever(self) -> None:
        """Run maintenance batches at the configured interval."""
        while True:
            await self.run_once()
            await asyncio.sleep(self.interval_seconds)

    async def close(self) -> None:
        """Stop lock renewal and close an internally-created Redis client."""
        await self._stop_lock_renewal()
        redis_client = self._redis
        self._redis = None
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception as exc:
                logger.debug("[Analytics] Failed to close usage hourly backfill Redis: %s", exc)

    async def _process_batch(self) -> int:
        snapshot_collection = self._get_snapshot_collection()
        earliest = await self._find_boundary(snapshot_collection, ascending=True)
        latest = await self._find_boundary(snapshot_collection, ascending=False)
        if earliest is None or latest is None:
            return 0

        state = await self._load_state()
        cursor_date = state.get("cursor_date") if state else self._cursor_date
        start_date = earliest
        if isinstance(cursor_date, str):
            try:
                start_date = max(start_date, date.fromisoformat(cursor_date) + timedelta(days=1))
            except ValueError:
                pass
        if start_date > latest:
            return 0

        end_date = min(start_date + timedelta(days=self.batch_days), latest + timedelta(days=1))
        start_string = start_date.isoformat()
        end_string = end_date.isoformat()
        cursor = snapshot_collection.find(
            {
                "date": {"$gte": start_string, "$lt": end_string},
                "user_id": {"$ne": _COMPLETE_MARKER_USER_ID},
            }
        )
        documents = await self._cursor_to_list(cursor)
        now = self._now_factory()
        rows: list[dict[str, Any]] = []
        for document in documents:
            row = self._snapshot_row(document, now)
            if row is not None:
                rows.append(row)

        if self._lock_lost:
            logger.warning("[Analytics] Usage hourly backfill aborted: lock lost")
            return 0
        if rows:
            await self._usage_storage.upsert_snapshot_rows(rows)
        if self._lock_lost:
            logger.warning("[Analytics] Usage hourly backfill cursor not advanced: lock lost")
            return 0

        last_date = (end_date - timedelta(days=1)).isoformat()
        await self._save_state(last_date)
        return (end_date - start_date).days

    async def _find_boundary(self, collection: Any, *, ascending: bool) -> date | None:
        direction = 1 if ascending else -1
        query = {"user_id": {"$ne": _COMPLETE_MARKER_USER_ID}}
        try:
            document = await collection.find_one(
                query,
                sort=[("date", direction)],
                projection={"date": 1},
            )
        except TypeError:
            document = await collection.find_one(query, sort=[("date", direction)])
        if not document:
            return None
        parsed = _parse_snapshot_date(document.get("date"))
        return date.fromisoformat(parsed[0]) if parsed else None

    async def _load_state(self) -> dict[str, Any] | None:
        try:
            document = await self._get_state_collection().find_one(
                {"_id": USAGE_HOURLY_BACKFILL_STATE_ID}
            )
        except Exception as exc:
            logger.warning("[Analytics] Usage hourly backfill state read failed: %s", exc)
            return {"cursor_date": self._cursor_date} if self._cursor_date else None
        if document:
            self._cursor_date = document.get("cursor_date")
        return document

    async def _save_state(self, cursor_date: str) -> None:
        self._cursor_date = cursor_date
        try:
            await self._get_state_collection().update_one(
                {"_id": USAGE_HOURLY_BACKFILL_STATE_ID},
                {"$set": {"cursor_date": cursor_date, "updated_at": self._now_factory()}},
                upsert=True,
            )
        except Exception as exc:
            logger.warning("[Analytics] Usage hourly backfill state write failed: %s", exc)

    @staticmethod
    async def _cursor_to_list(cursor: Any) -> list[dict[str, Any]]:
        if hasattr(cursor, "to_list"):
            return list(await cursor.to_list(length=None))
        return [document async for document in cursor]

    @staticmethod
    def _snapshot_row(document: dict[str, Any], updated_at: datetime) -> dict[str, Any] | None:
        if document.get("user_id") == _COMPLETE_MARKER_USER_ID:
            return None
        parsed = _parse_snapshot_date(document.get("date"))
        if parsed is None:
            return None
        _, bucket = parsed
        return {
            "bucket": bucket,
            "user_id": document.get("user_id"),
            "model": None,
            "persona_preset_id": document.get("persona_preset_id"),
            "agent_id": document.get("agent_id"),
            "source": "snapshot",
            "tokens": int(document.get("tokens", 0) or 0),
            "user_messages": int(document.get("user_messages", 0) or 0),
            "runs": int(document.get("runs", 0) or 0),
            "updated_at": updated_at,
        }

    async def _renew_lock_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._renew_interval_seconds)
                redis_client = self._redis
                if redis_client is None or self._lock_value is None:
                    return
                renewed = await redis_client.eval(
                    _RENEW_LOCK_LUA,
                    1,
                    USAGE_HOURLY_BACKFILL_LOCK_KEY,
                    self._lock_value,
                    self._lock_ttl_seconds,
                )
                if not renewed:
                    self._lock_lost = True
                    logger.warning("[Analytics] Usage hourly backfill lock was lost")
                    return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._lock_lost = True
            logger.warning("[Analytics] Usage hourly backfill lock renewal failed: %s", exc)

    async def _stop_lock_renewal(self) -> None:
        task = self._renew_task
        self._renew_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _get_redis(self) -> Any:
        if self._redis is None:
            self._redis = create_redis_client(isolated_pool=True)
        return self._redis

    def _get_snapshot_collection(self) -> Any:
        if self._snapshot_collection is None:
            self._snapshot_collection = self._get_database()[SNAPSHOT_COLLECTION_NAME]
        return self._snapshot_collection

    def _get_state_collection(self) -> Any:
        if self._state_collection is None:
            self._state_collection = self._get_database()[USAGE_HOURLY_BACKFILL_STATE_COLLECTION]
        return self._state_collection

    @staticmethod
    def _get_database() -> Any:
        return get_mongo_client()[settings.MONGODB_DB]


UsageHourlyBackfill = UsageHourlyBackfillWorker
