"""MongoDB storage for hourly analytics usage buckets."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal, cast

from pymongo import UpdateOne

from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

logger = get_logger(__name__)

USAGE_HOURLY_COLLECTION_NAME = "usage_hourly"
USAGE_HOURLY_UNIQUE_INDEX_NAME = "usage_hourly_unique_idx"
USAGE_HOURLY_BUCKET_MODEL_INDEX_NAME = "usage_hourly_bucket_model_idx"
USAGE_HOURLY_BUCKET_USER_INDEX_NAME = "usage_hourly_bucket_user_idx"
USAGE_HOURLY_INDEX_RETRY_ATTEMPTS = 3

UsageSource = Literal["live", "snapshot"]

_INDEXES: tuple[tuple[str, list[tuple[str, int]], bool], ...] = (
    (
        USAGE_HOURLY_UNIQUE_INDEX_NAME,
        [
            ("bucket", 1),
            ("user_id", 1),
            ("model", 1),
            ("persona_preset_id", 1),
            ("agent_id", 1),
            ("source", 1),
        ],
        True,
    ),
    (
        USAGE_HOURLY_BUCKET_MODEL_INDEX_NAME,
        [("bucket", -1), ("model", 1)],
        False,
    ),
    (
        USAGE_HOURLY_BUCKET_USER_INDEX_NAME,
        [("bucket", -1), ("user_id", 1)],
        False,
    ),
)


def hour_bucket(dt: datetime) -> datetime:
    """Return ``dt`` truncated to an aware UTC hour."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _key_filter(
    *,
    bucket: datetime,
    user_id: str | None,
    model: str | None,
    persona_preset_id: str | None,
    agent_id: str | None,
    source: UsageSource,
) -> dict[str, Any]:
    return {
        "bucket": hour_bucket(bucket),
        "user_id": user_id,
        "model": model,
        "persona_preset_id": persona_preset_id,
        "agent_id": agent_id,
        "source": source,
    }


def _metric_value(value: Any) -> int:
    return int(value or 0)


def _validate_source(source: str) -> UsageSource:
    if source not in ("live", "snapshot"):
        raise ValueError(f"Unsupported usage source: {source!r}")
    return source  # type: ignore[return-value]


def get_usage_hourly_collection() -> Any:
    """Return the lazily-created ``usage_hourly`` collection."""
    return get_mongo_client()[settings.MONGODB_DB][USAGE_HOURLY_COLLECTION_NAME]


class UsageHourlyStorage:
    """Atomic writes for the denormalized hourly usage collection."""

    def __init__(self, collection: Any | None = None) -> None:
        self._collection = collection

    @property
    def collection(self) -> Any:
        if self._collection is None:
            self._collection = get_usage_hourly_collection()
        return self._collection

    async def ensure_indexes(self) -> bool:
        """Create the usage query indexes with bounded transient retries."""
        ready = True
        for name, keys, unique in _INDEXES:
            try:
                await self._create_index_with_retry(
                    name,
                    keys,
                    unique=unique,
                )
            except Exception as exc:
                ready = False
                logger.warning("Failed to ensure usage hourly index %s: %s", name, exc)
        if ready:
            logger.info("Usage hourly indexes ensured")
        return ready

    async def _create_index_with_retry(
        self,
        name: str,
        keys: list[tuple[str, int]],
        *,
        unique: bool = False,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(USAGE_HOURLY_INDEX_RETRY_ATTEMPTS):
            try:
                await self.collection.create_index(
                    keys,
                    name=name,
                    unique=unique,
                    background=True,
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt + 1 < USAGE_HOURLY_INDEX_RETRY_ATTEMPTS:
                    await asyncio.sleep(0.05 * (2**attempt))
        assert last_error is not None
        raise last_error

    async def accumulate(
        self,
        bucket: datetime | None = None,
        user_id: str | None = None,
        model: str | None = None,
        persona_preset_id: str | None = None,
        agent_id: str | None = None,
        *,
        source: str = "live",
        tokens: int = 0,
        user_messages: int = 0,
        runs: int = 0,
        timestamp: datetime | None = None,
        at: datetime | None = None,
        dt: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> Any:
        """Atomically add one usage delta to its unique hourly row.

        ``timestamp`` and ``at`` are accepted as aliases for ``bucket`` so
        callers can pass an event time without pre-truncating it themselves.
        """
        event_time = bucket or timestamp or at or dt
        if event_time is None:
            raise ValueError("bucket or an event timestamp is required")
        validated_source = _validate_source(source)
        update = {
            "$inc": {
                "tokens": _metric_value(tokens),
                "user_messages": _metric_value(user_messages),
                "runs": _metric_value(runs),
            },
            "$set": {"updated_at": updated_at or utc_now()},
        }
        return await self.collection.update_one(
            _key_filter(
                bucket=event_time,
                user_id=user_id,
                model=model,
                persona_preset_id=persona_preset_id,
                agent_id=agent_id,
                source=validated_source,
            ),
            update,
            upsert=True,
        )

    async def accumulate_many(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = 500,
        updated_at: datetime | None = None,
    ) -> list[Any]:
        """Atomically add a batch of usage deltas with ``bulk_write``."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        operations: list[UpdateOne] = []
        results: list[Any] = []
        for row in rows:
            event_time = row.get("bucket") or row.get("timestamp") or row.get("at") or row.get("dt")
            if not isinstance(event_time, datetime):
                raise ValueError("each usage row requires a datetime bucket")
            source = _validate_source(str(row.get("source", "live")))
            operations.append(
                self._increment_operation(
                    bucket=event_time,
                    user_id=row.get("user_id"),
                    model=row.get("model"),
                    persona_preset_id=row.get("persona_preset_id"),
                    agent_id=row.get("agent_id"),
                    source=source,
                    tokens=row.get("tokens", 0),
                    user_messages=row.get("user_messages", 0),
                    runs=row.get("runs", 0),
                    updated_at=updated_at,
                )
            )
            if len(operations) >= batch_size:
                results.append(await self.collection.bulk_write(operations, ordered=False))
                operations = []
        if operations:
            results.append(await self.collection.bulk_write(operations, ordered=False))
        return results

    async def bulk_accumulate(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = 500,
        updated_at: datetime | None = None,
    ) -> list[Any]:
        """Alias for :meth:`accumulate_many`."""
        return await self.accumulate_many(rows, batch_size=batch_size, updated_at=updated_at)

    async def upsert_snapshot_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        batch_size: int = 500,
    ) -> list[Any]:
        """Insert immutable snapshot rows without incrementing existing rows."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        operations: list[UpdateOne] = []
        results: list[Any] = []
        for row in rows:
            event_time = row.get("bucket")
            if not isinstance(event_time, datetime):
                raise ValueError("each snapshot row requires a datetime bucket")
            source = _validate_source(str(row.get("source", "snapshot")))
            if source != "snapshot":
                raise ValueError("snapshot backfill rows must use source='snapshot'")
            bucket = hour_bucket(event_time)
            document = {
                "bucket": bucket,
                "user_id": row.get("user_id"),
                "model": row.get("model"),
                "persona_preset_id": row.get("persona_preset_id"),
                "agent_id": row.get("agent_id"),
                "source": source,
                "tokens": _metric_value(row.get("tokens", 0)),
                "user_messages": _metric_value(row.get("user_messages", 0)),
                "runs": _metric_value(row.get("runs", 0)),
                "updated_at": row.get("updated_at") or utc_now(),
            }
            operations.append(
                UpdateOne(
                    _key_filter(
                        bucket=bucket,
                        user_id=cast(str | None, document["user_id"]),
                        model=cast(str | None, document["model"]),
                        persona_preset_id=cast(str | None, document["persona_preset_id"]),
                        agent_id=cast(str | None, document["agent_id"]),
                        source=source,
                    ),
                    {"$setOnInsert": document},
                    upsert=True,
                )
            )
            if len(operations) >= batch_size:
                results.append(await self.collection.bulk_write(operations, ordered=False))
                operations = []
        if operations:
            results.append(await self.collection.bulk_write(operations, ordered=False))
        return results

    def _increment_operation(
        self,
        *,
        bucket: datetime,
        user_id: str | None,
        model: str | None,
        persona_preset_id: str | None,
        agent_id: str | None,
        source: UsageSource,
        tokens: Any,
        user_messages: Any,
        runs: Any,
        updated_at: datetime | None,
    ) -> UpdateOne:
        return UpdateOne(
            _key_filter(
                bucket=bucket,
                user_id=user_id,
                model=model,
                persona_preset_id=persona_preset_id,
                agent_id=agent_id,
                source=source,
            ),
            {
                "$inc": {
                    "tokens": _metric_value(tokens),
                    "user_messages": _metric_value(user_messages),
                    "runs": _metric_value(runs),
                },
                "$set": {"updated_at": updated_at or utc_now()},
            },
            upsert=True,
        )


UsageHourlyStore = UsageHourlyStorage


async def ensure_indexes(collection: Any | None = None) -> bool:
    """Ensure indexes using an optional injected collection."""
    return await UsageHourlyStorage(collection=collection).ensure_indexes()


async def accumulate_usage(
    bucket: datetime,
    user_id: str | None = None,
    model: str | None = None,
    persona_preset_id: str | None = None,
    agent_id: str | None = None,
    *,
    source: str = "live",
    tokens: int = 0,
    user_messages: int = 0,
    runs: int = 0,
) -> Any:
    """Module-level convenience wrapper for one atomic update."""
    return await UsageHourlyStorage().accumulate(
        bucket=bucket,
        user_id=user_id,
        model=model,
        persona_preset_id=persona_preset_id,
        agent_id=agent_id,
        source=source,
        tokens=tokens,
        user_messages=user_messages,
        runs=runs,
    )
