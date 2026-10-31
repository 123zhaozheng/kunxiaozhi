"""不可变日快照与统计合并。

历史日期只在第一次读取（或每日冻结任务）时从 traces/sessions 计算并写入快照；
读取时再应用筛选条件，因此筛选不会造成部分快照被永久写入。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from bson import ObjectId
from pymongo import UpdateOne

from src.infra.analytics.date_range import (
    CST,
    day_buckets,
    range_to_date_strings,
    today_cst,
)
from src.infra.logging import get_logger
from src.infra.storage.redis import create_redis_client

logger = get_logger(__name__)

SNAPSHOT_COLLECTION_NAME = "analytics_daily_snapshot"
_SNAPSHOT_LOCK_KEY_PREFIX = "analytics:snapshot:freeze:"
_SNAPSHOT_LOCK_TTL_SECONDS = 60
_MISSING = object()
# Marks a date as fully frozen. Rows written by an older build (which froze
# only the filtered subset it happened to read) predate this marker, so such a
# date is re-frozen once to fill the gaps. $setOnInsert keeps existing rows
# untouched, and the marker itself carries no metric.
_COMPLETE_MARKER_USER_ID = "__snapshot_complete__"
# Today's numbers are pre-aggregated by a background worker into their own
# collection (overwritten each refresh) so a dashboard read never scans traces.
TODAY_SNAPSHOT_COLLECTION_NAME = "analytics_today_snapshot"
# Breakdown fields added on top of the immutable core counts. A daily marker
# without ``extras_version`` means its rows predate them and get enriched.
EXTRAS_VERSION = 1
_EXTRA_FIELDS = ("hour_messages", "hour_tokens", "model_tokens")


def snapshot_match(filters: Any, dates: list[str]) -> dict[str, Any]:
    """Build the legacy, filter-aware snapshot match document.

    The freeze path deliberately does not use this filter-aware helper. It is
    kept for callers that need to inspect already-frozen rows with filters.
    """
    match: dict[str, Any] = {"date": {"$in": dates}}
    pp_id = getattr(filters, "persona_preset_id", None)
    agent_id = getattr(filters, "agent_id", None)
    if pp_id:
        match["persona_preset_id"] = pp_id
    if agent_id:
        match["agent_id"] = agent_id
    return match


def snapshot_group_stages(
    dimension: Literal["total", "day", "persona", "agent", "user", "user_persona"],
) -> list[dict[str, Any]]:
    """Build grouping stages used when reading immutable snapshot rows."""
    id_mapping: dict[str, Any] = {
        "total": None,
        "day": "$date",
        "persona": "$persona_preset_id",
        "agent": "$agent_id",
        "user": "$user_id",
        "user_persona": {
            "user_id": "$user_id",
            "persona_preset_id": "$persona_preset_id",
        },
    }
    group_stage: dict[str, Any] = {
        "$group": {
            "_id": id_mapping[dimension],
            "new_sessions": {"$sum": {"$ifNull": ["$new_sessions", 0]}},
            "active_sessions": {"$sum": {"$ifNull": ["$active_sessions", 0]}},
            "user_messages": {"$sum": {"$ifNull": ["$user_messages", 0]}},
            "tokens": {"$sum": {"$ifNull": ["$tokens", 0]}},
            # Arrays are pushed rather than summed. The application flattens
            # them so a session present on multiple days is counted once.
            "active_session_ids": {"$push": "$active_session_ids"},
            "new_session_ids": {"$push": "$new_session_ids"},
            "active_session_ids_present": {
                "$addToSet": {
                    "$ne": [{"$type": "$active_session_ids"}, "missing"]
                }
            },
            "new_session_ids_present": {
                "$addToSet": {
                    "$ne": [{"$type": "$new_session_ids"}, "missing"]
                }
            },
            "last_active_at": {"$max": "$last_active_at"},
        }
    }
    if dimension != "total":
        return [group_stage, {"$sort": {"_id": 1}}]
    return [group_stage]


def _flatten_ids(value: Any) -> set[str]:
    """Flatten an aggregation result containing arrays (and legacy nulls)."""
    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for item in value:
            result.update(_flatten_ids(item))
        return result
    if value is None or value == "":
        return set()
    return {str(value)}


def _group_ids(doc: dict[str, Any], field: str) -> tuple[bool, set[str]]:
    """Return ``(modern_field_present, ids)`` for a grouped snapshot row."""
    presence = doc.get(f"{field}_present", _MISSING)
    if presence is not _MISSING:
        presence_values = presence if isinstance(presence, list) else [presence]
        # A mixed rollout (old + new rows) must use the old integer semantics
        # for the whole grouped day rather than silently dropping old rows.
        if not all(bool(value) for value in presence_values):
            return False, set()
    raw = doc.get(field, _MISSING)
    if raw is _MISSING:
        return False, set()
    # A modern row may contain an empty list, which must still count as modern.
    if isinstance(raw, (list, tuple, set)):
        return True, _flatten_ids(raw)
    return False, set()


def _visible_metric(doc: dict[str, Any], field: str, ids_field: str) -> tuple[int, bool, set[str]]:
    supported, ids = _group_ids(doc, ids_field)
    if supported:
        return len(ids), True, ids
    # Pre-upgrade rows have no ID array; their stored integer is the only
    # available value, so cross-day de-duplication is not possible for them.
    logger.debug("[Analytics] Snapshot row without %s, using legacy integer", ids_field)
    return int(doc.get(field, 0) or 0), False, set()


def _merge_metric_items(
    items: list[dict[str, Any]],
    key_field: str | tuple[str, ...],
    metric_fields: tuple[str, ...] = ("new_sessions", "active_sessions"),
) -> list[dict[str, Any]]:
    """Merge dimension rows while preserving modern ID-set semantics.

    Legacy rows contribute their stored integer. Modern rows contribute the
    union of their identifier arrays. This also makes mixed-version rollouts
    safe while old documents are still being read.

    ``key_field`` may be a tuple to merge on a composite key (user × persona)
    without collapsing rows that differ only in the secondary field.
    """
    key_fields = (key_field,) if isinstance(key_field, str) else key_field
    merged: dict[Any, dict[str, Any]] = {}
    for source in items:
        key = tuple(source.get(field) for field in key_fields)
        target = merged.get(key)
        is_new = target is None
        if target is None:
            target = dict(source)
            target["_active_session_ids"] = set()
            target["_new_session_ids"] = set()
            target["_active_session_ids_supported"] = False
            target["_new_session_ids_supported"] = False
            target["_active_sessions_legacy"] = 0
            target["_new_sessions_legacy"] = 0
            merged[key] = target
        for field, ids_field in (
            ("active_sessions", "active_session_ids"),
            ("new_sessions", "new_session_ids"),
        ):
            supported = bool(source.get(f"_{ids_field}_supported", False))
            if supported:
                target[f"_{ids_field}_supported"] = True
                target[f"_{ids_field}"].update(source.get(f"_{ids_field}", set()))
            else:
                target[f"_{field}_legacy"] += int(source.get(field, 0) or 0)
        for field in (*metric_fields, "user_messages", "tokens"):
            if field not in ("active_sessions", "new_sessions"):
                # The first source row was copied into ``target`` above;
                # subsequent rows need to be accumulated explicitly.
                if not is_new:
                    target[field] = int(target.get(field, 0) or 0) + int(source.get(field, 0) or 0)
        if not target.get("persona_preset_name") and source.get("persona_preset_name"):
            target["persona_preset_name"] = source["persona_preset_name"]
        if source.get("last_active_at") and (
            not target.get("last_active_at") or source["last_active_at"] > target["last_active_at"]
        ):
            target["last_active_at"] = source["last_active_at"]

    result: list[dict[str, Any]] = []
    for target in merged.values():
        for field, ids_field in (
            ("active_sessions", "active_session_ids"),
            ("new_sessions", "new_session_ids"),
        ):
            if target[f"_{ids_field}_supported"]:
                target[field] = len(target[f"_{ids_field}"]) + target[f"_{field}_legacy"]
            else:
                target[field] = target[f"_{field}_legacy"]
        result.append(target)
    return result


def _public_metric_item(item: dict[str, Any]) -> dict[str, Any]:
    """Remove internal ID sets before returning API-facing dictionaries."""
    return {
        key: value
        for key, value in item.items()
        if not key.startswith("_") and key not in {"active_session_ids", "new_session_ids"}
    }


def _session_id_expr() -> dict[str, Any]:
    return {"$ifNull": ["$session_id", {"$toString": "$_id"}]}


def _row_key(eid: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        str(eid.get("user_id")) if eid.get("user_id") is not None else None,
        str(eid.get("persona_preset_id")) if eid.get("persona_preset_id") is not None else None,
        str(eid.get("agent_id")) if eid.get("agent_id") is not None else None,
    )


def _empty_row(date: str, key: tuple[str | None, str | None, str | None]) -> dict[str, Any]:
    return {
        "date": date,
        "user_id": key[0],
        "persona_preset_id": key[1],
        "agent_id": key[2],
        "new_sessions": 0,
        "new_session_ids": [],
        "active_sessions": 0,
        "active_session_ids": [],
        "user_messages": 0,
        "tokens": 0,
        "last_active_at": None,
        "hour_messages": [0] * 24,
        "hour_tokens": [0] * 24,
        "model_tokens": [],
    }


async def compute_day_rows(storage: Any, target_date: str) -> list[dict[str, Any]]:
    """Aggregate one UTC+8 day into snapshot rows keyed by user × persona × agent.

    Shared by the immutable daily freeze and the periodically refreshed today
    snapshot, so both carry the same core counts and breakdown extras.
    Raises on aggregation failure; callers must not write a partial day.
    """
    from src.infra.analytics.storage import _model_label_expr
    from src.infra.analytics.usage_query import UsageFilters, new_sessions_match, usage_facts_stages

    # Deliberately omit every read filter: a date is frozen as a full
    # snapshot, and filtering is applied only by _merge_results.
    date_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=CST)
    date_filters = UsageFilters(start=date_dt, end=date_dt + timedelta(days=1))
    key_fields = {
        "user_id": "$user_id",
        "persona_preset_id": "$persona_preset_id",
        "agent_id": "$agent_id",
    }
    hour_docs = await storage.traces.aggregate(
        usage_facts_stages(date_filters)
        + [
            {
                "$group": {
                    "_id": {
                        **key_fields,
                        "hour": {"$hour": {"date": "$started_at", "timezone": "Asia/Shanghai"}},
                    },
                    "user_messages": {"$sum": "$user_messages"},
                    "tokens": {"$sum": "$tokens"},
                    "active_session_ids": {
                        "$addToSet": {
                            "$cond": [{"$gt": ["$user_messages", 0]}, "$session_id", None]
                        }
                    },
                    "last_active_at": {"$max": "$started_at"},
                }
            },
            {"$match": {"_id.user_id": {"$nin": [None, ""]}}},
        ]
    ).to_list(length=None)
    # Peak/hourly ownership follows each user:message event's own timestamp,
    # not the trace start (a long run can span hours or midnight).
    message_hour_docs = await storage.traces.aggregate(
        usage_facts_stages(date_filters)[:-1]
        + [
            {"$match": {"user_messages": {"$gt": 0}}},
            {"$unwind": "$events"},
            {
                "$match": {
                    "events.event_type": "user:message",
                    "events.timestamp": {"$gte": date_filters.start, "$lt": date_filters.end},
                }
            },
            {
                "$group": {
                    "_id": {
                        **key_fields,
                        "hour": {"$hour": {"date": "$events.timestamp", "timezone": "Asia/Shanghai"}},
                    },
                    "user_messages": {"$sum": 1},
                }
            },
            {"$match": {"_id.user_id": {"$nin": [None, ""]}}},
        ]
    ).to_list(length=None)
    model_docs = await storage.traces.aggregate(
        # [:-1] keeps ``events`` (the final stage projects them away).
        usage_facts_stages(date_filters)[:-1]
        + [
            {"$unwind": "$events"},
            {"$match": {"events.event_type": "token:usage"}},
            {
                "$group": {
                    "_id": {**key_fields, "model": _model_label_expr()},
                    "tokens": {"$sum": {"$ifNull": ["$events.data.total_tokens", 0]}},
                }
            },
            {"$match": {"_id.user_id": {"$nin": [None, ""]}}},
        ]
    ).to_list(length=None)
    session_docs = await storage.sessions.aggregate(
        [
            {"$match": new_sessions_match(date_filters)},
            {
                "$group": {
                    "_id": {
                        "user_id": "$user_id",
                        "persona_preset_id": "$metadata.persona_preset_id",
                        "agent_id": "$agent_id",
                    },
                    "new_sessions": {"$sum": 1},
                    "new_session_ids": {"$addToSet": _session_id_expr()},
                }
            },
        ]
    ).to_list(length=None)

    rows: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
    active_ids: dict[tuple[str | None, str | None, str | None], set[str]] = {}
    for doc in hour_docs:
        eid = doc.get("_id") or {}
        key = _row_key(eid)
        row = rows.setdefault(key, _empty_row(target_date, key))
        hour = int(eid.get("hour") or 0) % 24
        messages = int(doc.get("user_messages", 0) or 0)
        tokens = int(doc.get("tokens", 0) or 0)
        row["user_messages"] += messages
        row["tokens"] += tokens
        row["hour_tokens"][hour] += tokens
        active_ids.setdefault(key, set()).update(_flatten_ids(doc.get("active_session_ids")))
        last = doc.get("last_active_at")
        if last and (row["last_active_at"] is None or last > row["last_active_at"]):
            row["last_active_at"] = last
    for key, ids in active_ids.items():
        rows[key]["active_session_ids"] = sorted(ids)
        rows[key]["active_sessions"] = len(ids)

    for doc in message_hour_docs:
        eid = doc.get("_id") or {}
        key = _row_key(eid)
        row = rows.setdefault(key, _empty_row(target_date, key))
        row["hour_messages"][int(eid.get("hour") or 0) % 24] += int(doc.get("user_messages", 0) or 0)

    for doc in model_docs:
        eid = doc.get("_id") or {}
        key = _row_key(eid)
        tokens = int(doc.get("tokens", 0) or 0)
        if tokens <= 0:
            continue
        row = rows.setdefault(key, _empty_row(target_date, key))
        row["model_tokens"].append({"model": str(eid.get("model") or "unknown"), "tokens": tokens})

    for doc in session_docs:
        key = _row_key(doc.get("_id") or {})
        row = rows.setdefault(key, _empty_row(target_date, key))
        ids = sorted(set(row["new_session_ids"]) | _flatten_ids(doc.get("new_session_ids")))
        row["new_session_ids"] = ids
        row["new_sessions"] = len(ids)
    return list(rows.values())


async def refresh_today_snapshot(storage: Any) -> int:
    """Recompute today's rows into the today snapshot; returns the row count.

    Rows are overwritten (today is still changing), the marker is written
    after the rows, and stale rows (and any previous day) are removed last so
    readers never lose the marker mid-refresh.
    """
    today = today_cst()
    run_ts = datetime.now(timezone.utc)
    rows = await compute_day_rows(storage, today)
    collection = storage.today_snapshot
    ops = [
        UpdateOne(
            {
                "date": today,
                "user_id": row["user_id"],
                "persona_preset_id": row["persona_preset_id"],
                "agent_id": row["agent_id"],
            },
            {"$set": {**row, "refreshed_at": run_ts}},
            upsert=True,
        )
        for row in rows
    ]
    if ops:
        await collection.bulk_write(ops, ordered=False)
    await collection.update_one(
        {"date": today, "user_id": _COMPLETE_MARKER_USER_ID},
        {
            "$set": {
                "date": today,
                "user_id": _COMPLETE_MARKER_USER_ID,
                "persona_preset_id": None,
                "agent_id": None,
                "refreshed_at": run_ts,
            }
        },
        upsert=True,
    )
    await collection.delete_many(
        {"$or": [{"date": {"$ne": today}}, {"refreshed_at": {"$lt": run_ts}}]}
    )
    return len(rows)


async def ensure_dates_frozen(storage: Any, redis_client: Any, dates: list[str]) -> set[str]:
    """Freeze historical dates whose completion marker is missing.

    Returns the dates still missing afterwards (lock race / failure / no Redis).
    """
    today = today_cst()
    candidates = [date for date in dates if date != today]
    if not candidates:
        return set()
    # Completeness is date-based and marker-based. Never let a filtered
    # read decide that a partial persona/agent snapshot freezes the
    # whole date, and re-freeze dates written before the marker existed.
    cursor = storage.snapshot.find(
        {"date": {"$in": candidates}, "user_id": _COMPLETE_MARKER_USER_ID},
        {"date": 1},
    )
    existing_docs = await cursor.to_list(length=None)
    existing_dates = {doc.get("date") for doc in existing_docs if doc.get("date")}
    truly_missing = [date for date in candidates if date not in existing_dates]
    if not truly_missing:
        return set()
    if redis_client is None:
        return set(truly_missing)
    frozen = await _freeze_dates(storage, redis_client, truly_missing, None)
    return set(truly_missing) - frozen


async def enrich_snapshot_extras(storage: Any, redis_client: Any, limit: int) -> int:
    """Fill breakdown extras on the most recent frozen dates that lack them."""
    cursor = storage.snapshot.find(
        {"user_id": _COMPLETE_MARKER_USER_ID, "extras_version": {"$exists": False}},
        {"date": 1},
    ).sort("date", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    dates = [str(doc["date"]) for doc in docs if doc.get("date")]
    if not dates:
        return 0
    return len(await _freeze_dates(storage, redis_client, dates, None, enrich_only=True))


async def read_or_freeze(filters: Any, storage: Any | None = None) -> dict[str, Any]:
    """Read usage metrics, freezing missing historical dates first.

    The returned mapping always includes ``by_user_persona`` so downstream
    usage and CSV views can share the snapshot source.
    """
    from src.infra.analytics.usage_query import UsageFilters

    if not isinstance(filters, UsageFilters):
        filters = UsageFilters(
            start=filters.start,
            end=filters.end,
            persona_preset_id=getattr(filters, "persona_preset_id", None),
            agent_id=getattr(filters, "agent_id", None),
            role_user_ids=getattr(filters, "role_user_ids", None),
        )

    start_str, end_inclusive = range_to_date_strings(filters.start, filters.end)
    dates = day_buckets(start_str, end_inclusive)

    if storage is None:
        from src.infra.analytics.storage import AnalyticsStorage

        storage = AnalyticsStorage()

    snapshot_unavailable = False
    try:
        # The background worker freezes every past day, so this is only a
        # safety net: Redis is opened solely when a date is actually missing.
        missing = await ensure_dates_frozen(storage, None, dates)
        if missing:
            redis_client = None
            try:
                redis_client = create_redis_client(isolated_pool=True)
            except Exception as exc:
                logger.warning("[Analytics] Redis client creation failed: %s", exc)
            try:
                # A lock race, aggregate failure, or marker write failure
                # must never be mistaken for an empty snapshot.
                if redis_client is None or await ensure_dates_frozen(
                    storage, redis_client, sorted(missing)
                ):
                    snapshot_unavailable = True
            finally:
                if redis_client is not None:
                    await redis_client.aclose()
    except Exception as exc:
        snapshot_unavailable = True
        logger.warning("[Analytics] Snapshot check/freeze failed: %s", exc)

    result = await _merge_results(
        storage, filters, dates, skip_historical=snapshot_unavailable
    )
    # This extra flag preserves the historical mapping for existing callers
    # while allowing detail/CSV consumers to distinguish incomplete fallback
    # output from a legitimate empty dimension.
    result["snapshot_complete"] = bool(
        result.get("snapshot_complete", True) and not snapshot_unavailable
    )
    return result


async def _freeze_dates(
    storage: Any,
    redis_client: Any,
    dates: list[str],
    filters: Any,
    *,
    enrich_only: bool = False,
) -> set[str]:
    """Freeze each date under its own lock; filtered reads never affect writes.

    ``enrich_only`` targets already-frozen dates: only the breakdown extras
    are written, with no upsert, so the immutable core counts cannot shift.
    """
    release_lua = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
    renew_lua = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

    completed_dates: set[str] = set()
    for target_date in dates:
        lock_key = f"{_SNAPSHOT_LOCK_KEY_PREFIX}{target_date}"
        instance_id = str(ObjectId())
        try:
            acquired = await redis_client.set(
                lock_key,
                instance_id,
                nx=True,
                ex=_SNAPSHOT_LOCK_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("[Analytics] Could not acquire snapshot lock for %s: %s", target_date, exc)
            continue
        if not acquired:
            logger.info("[Analytics] Snapshot date %s is being frozen by another instance", target_date)
            continue

        renew_task: asyncio.Task[None] | None = None
        lock_lost = False

        async def stop_renewal() -> None:
            nonlocal renew_task
            task = renew_task
            renew_task = None
            if task is None:
                return
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        async def renew_loop() -> None:
            nonlocal lock_lost
            try:
                while True:
                    await asyncio.sleep(_SNAPSHOT_LOCK_TTL_SECONDS / 3)
                    renewed = await redis_client.eval(
                        renew_lua,
                        1,
                        lock_key,
                        instance_id,
                        _SNAPSHOT_LOCK_TTL_SECONDS,
                    )
                    if not renewed:
                        lock_lost = True
                        logger.warning("[Analytics] Snapshot lock lost for %s", target_date)
                        return
            except asyncio.CancelledError:
                return
            except Exception as exc:
                lock_lost = True
                logger.warning("[Analytics] Snapshot lock renewal failed for %s: %s", target_date, exc)

        renew_task = asyncio.create_task(renew_loop())
        try:
            if lock_lost:
                continue
            try:
                rows = await compute_day_rows(storage, target_date)
            except Exception as exc:
                logger.warning("[Analytics] Day aggregation for %s failed: %s", target_date, exc)
                continue

            if lock_lost:
                continue
            frozen_at = datetime.now(timezone.utc)
            ops: list[UpdateOne] = []
            for row in rows:
                key = {
                    "date": target_date,
                    "user_id": row["user_id"],
                    "persona_preset_id": row["persona_preset_id"],
                    "agent_id": row["agent_id"],
                }
                extras = {k: row[k] for k in _EXTRA_FIELDS}
                if enrich_only:
                    ops.append(UpdateOne(key, {"$set": extras}, upsert=False))
                    continue
                core = {k: v for k, v in row.items() if k not in _EXTRA_FIELDS}
                core["frozen_at"] = frozen_at
                # Core counts stay immutable once frozen ($setOnInsert).
                ops.append(
                    UpdateOne(key, {"$setOnInsert": core, "$set": extras}, upsert=True)
                )
            if ops:
                await storage.snapshot.bulk_write(ops, ordered=False)
                logger.info("[Analytics] Frozen snapshots for %s: %d rows", target_date, len(ops))
            if lock_lost:
                continue
            # Written last and only while the lock still holds, so a partial
            # freeze is never marked complete. Also freezes genuinely empty
            # days, whose numbers must stay immutable once observed.
            await storage.snapshot.update_one(
                {"date": target_date, "user_id": _COMPLETE_MARKER_USER_ID},
                {
                    "$setOnInsert": {
                        "date": target_date,
                        "user_id": _COMPLETE_MARKER_USER_ID,
                        "persona_preset_id": None,
                        "agent_id": None,
                        "frozen_at": frozen_at,
                    },
                    "$set": {"extras_version": EXTRAS_VERSION},
                },
                upsert=True,
            )
            completed_dates.add(target_date)
        except Exception as exc:
            logger.warning("[Analytics] Snapshot freeze for %s failed: %s", target_date, exc)
        finally:
            # Nested finally: releasing the lock must not depend on the
            # renewal task shutting down cleanly.
            try:
                await stop_renewal()
            except Exception as exc:
                logger.warning("[Analytics] Snapshot lock renewal stop failed for %s: %s", target_date, exc)
            finally:
                try:
                    await redis_client.eval(release_lua, 1, lock_key, instance_id)
                except Exception as exc:
                    logger.warning("[Analytics] Failed to release snapshot lock for %s: %s", target_date, exc)


    return completed_dates


async def _today_snapshot_marker(storage: Any) -> dict[str, Any] | None:
    """Today's snapshot marker, or ``None`` until the worker has written it."""
    try:
        doc = await storage.today_snapshot.find_one(
            {"date": today_cst(), "user_id": _COMPLETE_MARKER_USER_ID}
        )
    except Exception as exc:
        logger.debug("[Analytics] Today snapshot unavailable: %s", exc)
        return None
    return doc if isinstance(doc, dict) else None


async def _extras_complete(
    storage: Any,
    historical_dates: list[str],
    skip_historical: bool,
    today_from_traces: bool,
) -> bool:
    """True when every date in range carries the hour/model breakdowns."""
    if today_from_traces or (historical_dates and skip_historical):
        return False
    if not historical_dates:
        return True
    try:
        docs = await storage.snapshot.find(
            {
                "date": {"$in": historical_dates},
                "user_id": _COMPLETE_MARKER_USER_ID,
                "extras_version": {"$gte": EXTRAS_VERSION},
            },
            {"date": 1},
        ).to_list(length=None)
    except Exception as exc:
        logger.debug("[Analytics] Snapshot extras markers unavailable: %s", exc)
        return False
    return set(historical_dates) <= {doc.get("date") for doc in docs}


def _apply_breakdowns(
    result: dict[str, Any],
    dates: list[str],
    hour_docs: list[dict[str, Any]],
    model_totals: dict[str, int],
    day_user_sets: dict[str, set[str]],
) -> None:
    """Derive hourly trend, peak slot, model totals and daily users."""
    by_date_hour: dict[tuple[str, int], list[int]] = {}
    for doc in hour_docs:
        key = doc.get("_id") or {}
        slot = by_date_hour.setdefault((str(key.get("date")), int(key.get("hour") or 0)), [0, 0])
        slot[0] += int(doc.get("user_messages", 0) or 0)
        slot[1] += int(doc.get("tokens", 0) or 0)

    result["by_hour"] = []
    if len(dates) == 1:
        result["by_hour"] = [
            {
                "hour": hour,
                "user_messages": by_date_hour.get((dates[0], hour), [0, 0])[0],
                "tokens": by_date_hour.get((dates[0], hour), [0, 0])[1],
            }
            for hour in range(24)
        ]

    # weekday follows Mongo $dayOfWeek - 1 (Sunday = 0), the API contract.
    slots: dict[tuple[int, int], int] = {}
    for (date, hour), (messages, _tokens) in by_date_hour.items():
        try:
            weekday = datetime.strptime(date, "%Y-%m-%d").isoweekday() % 7
        except ValueError:
            continue
        slots[(weekday, hour)] = slots.get((weekday, hour), 0) + messages
    best = max(
        ((count, -weekday, -hour) for (weekday, hour), count in slots.items() if count > 0),
        default=None,
    )
    result["peak"] = (
        {"weekday": -best[1], "hour": -best[2], "user_messages": best[0]} if best else None
    )

    result["by_model"] = sorted(
        ({"model": model, "tokens": tokens} for model, tokens in model_totals.items() if tokens > 0),
        key=lambda item: (-item["tokens"], item["model"]),
    )
    result["active_users_by_day"] = {
        date: len(day_user_sets.get(date, set())) for date in dates
    }


async def _aggregate_session_groups(storage: Any, filters: Any) -> list[dict[str, Any]]:
    """Aggregate new sessions independently from trace documents."""
    from src.infra.analytics.usage_query import new_sessions_match

    try:
        return await storage.sessions.aggregate(
            [
                {"$match": new_sessions_match(filters)},
                {
                    "$group": {
                        "_id": {
                            "user_id": "$user_id",
                            "persona_preset_id": "$metadata.persona_preset_id",
                            "agent_id": "$agent_id",
                        },
                        "new_sessions": {"$sum": 1},
                        "new_session_ids": {"$addToSet": _session_id_expr()},
                    }
                },
            ]
        ).to_list(length=None)
    except Exception as exc:
        logger.debug("[Analytics] Session dimension aggregate unavailable: %s", exc)
        return []


def _session_metric_map(docs: list[dict[str, Any]], field: str) -> dict[Any, dict[str, Any]]:
    mapping: dict[Any, dict[str, Any]] = {}
    for doc in docs:
        eid = doc.get("_id") or {}
        ids = sorted(_flatten_ids(doc.get("new_session_ids")))
        mapping[eid.get(field)] = {
            "new_sessions": int(doc.get("new_sessions", len(ids)) or 0),
            "new_session_ids": ids,
        }
    return mapping


async def _merge_results(
    storage: Any,
    filters: Any,
    dates: list[str],
    *,
    skip_historical: bool = False,
) -> dict[str, Any]:
    """Merge historical snapshots and today's live aggregates."""
    from src.infra.analytics.usage_query import UsageFilters, new_sessions_match, usage_facts_stages

    today = today_cst()
    historical_dates = [date for date in dates if date != today]
    snapshot_read_failed = False
    result: dict[str, Any] = {
        "total": {"new_sessions": 0, "active_sessions": 0, "user_messages": 0, "tokens": 0},
        "trend": [],
        "by_persona": [],
        "by_agent": [],
        "by_user": [],
        "by_user_persona": [],
        "active_users": 0,
        "using_users": 0,
        "by_hour": [],
        "peak": None,
        "by_model": [],
        "active_users_by_day": {},
        "extras_complete": False,
        "refreshed_at": None,
        "snapshot_complete": not (
            skip_historical and any(date != today_cst() for date in dates)
        ) and not snapshot_read_failed,
    }
    persona_user_sets: dict[str | None, set[str]] = {}
    agent_user_sets: dict[str | None, set[str]] = {}
    realtime_user_ids: set[str] = set()
    realtime_fallback_active_session_ids: set[str] = set()
    realtime_fallback_user_messages = 0
    realtime_fallback_tokens = 0
    historical_items: list[dict[str, Any]] = []
    persona_items: list[dict[str, Any]] = []
    agent_items: list[dict[str, Any]] = []
    user_items: list[dict[str, Any]] = []
    user_persona_items: list[dict[str, Any]] = []
    hour_docs: list[dict[str, Any]] = []
    model_totals: dict[str, int] = {}
    day_user_sets: dict[str, set[str]] = {}
    extras_failed = False
    historical_day_loaded = False

    today_marker = await _today_snapshot_marker(storage) if today in dates else None
    sources: list[tuple[Any, list[str], bool]] = []
    if historical_dates and not skip_historical:
        sources.append((storage.snapshot, historical_dates, True))
    if today_marker is not None:
        sources.append((storage.today_snapshot, [today], False))
    result["refreshed_at"] = today_marker.get("refreshed_at") if today_marker else None

    try:
        for snapshot_collection, source_dates, is_historical in sources:
            # The completion marker carries no metric and must never reach an
            # aggregation; excluding it here covers every dimension below.
            base_match: list[dict[str, Any]] = [
                {
                    "$match": {
                        "date": {"$in": source_dates},
                        "user_id": {"$ne": _COMPLETE_MARKER_USER_ID},
                    }
                }
            ]
            if getattr(filters, "persona_preset_id", None):
                base_match.append({"$match": {"persona_preset_id": filters.persona_preset_id}})
            if getattr(filters, "agent_id", None):
                base_match.append({"$match": {"agent_id": filters.agent_id}})
            if getattr(filters, "role_user_ids", None) is not None:
                base_match.append({"$match": {"user_id": {"$in": filters.role_user_ids}}})

            dimension_docs: dict[str, list[dict[str, Any]]] = {}
            for dimension in ("day", "persona", "agent", "user", "user_persona"):
                try:
                    dimension_docs[dimension] = await snapshot_collection.aggregate(
                        base_match + snapshot_group_stages(dimension)  # type: ignore[arg-type]
                    ).to_list(length=None)
                    if dimension == "day" and is_historical:
                        historical_day_loaded = True
                except Exception as exc:
                    logger.warning("[Analytics] Snapshot %s aggregation failed: %s", dimension, exc)
                    snapshot_read_failed = True
                    dimension_docs[dimension] = []

            for doc in dimension_docs["day"]:
                value = dict(doc)
                value["date"] = str(value.pop("_id", ""))
                active, active_supported, active_ids = _visible_metric(value, "active_sessions", "active_session_ids")
                new, new_supported, new_ids = _visible_metric(value, "new_sessions", "new_session_ids")
                value["active_sessions"] = active
                value["new_sessions"] = new
                value["_active_session_ids_supported"] = active_supported
                value["_active_session_ids"] = active_ids
                value["_new_session_ids_supported"] = new_supported
                value["_new_session_ids"] = new_ids
                historical_items.append(value)

            def dimension_items(docs: list[dict[str, Any]], dimension: str) -> list[dict[str, Any]]:
                values: list[dict[str, Any]] = []
                for doc in docs:
                    value = dict(doc)
                    identifier = value.pop("_id", None)
                    if dimension == "persona":
                        value["persona_preset_id"] = str(identifier) if identifier else None
                        value.setdefault("persona_preset_name", None)
                    elif dimension == "agent":
                        value["agent_id"] = str(identifier) if identifier else None
                    elif dimension == "user":
                        value["user_id"] = str(identifier) if identifier else None
                    elif dimension == "user_persona":
                        identifier = identifier or {}
                        value["user_id"] = str(identifier.get("user_id")) if identifier.get("user_id") else None
                        pid = identifier.get("persona_preset_id")
                        value["persona_preset_id"] = str(pid) if pid else None
                        value.setdefault("persona_preset_name", None)
                    active, active_supported, active_ids = _visible_metric(value, "active_sessions", "active_session_ids")
                    new, new_supported, new_ids = _visible_metric(value, "new_sessions", "new_session_ids")
                    value["active_sessions"] = active
                    value["new_sessions"] = new
                    value["_active_session_ids_supported"] = active_supported
                    value["_active_session_ids"] = active_ids
                    value["_new_session_ids_supported"] = new_supported
                    value["_new_session_ids"] = new_ids
                    values.append(value)
                return values

            persona_items.extend(dimension_items(dimension_docs["persona"], "persona"))
            agent_items.extend(dimension_items(dimension_docs["agent"], "agent"))
            user_items.extend(dimension_items(dimension_docs["user"], "user"))
            user_persona_items.extend(dimension_items(dimension_docs["user_persona"], "user_persona"))

            try:
                persona_user_docs = await snapshot_collection.aggregate(
                    base_match
                    + [
                        {
                            "$group": {
                                "_id": "$persona_preset_id",
                                "user_ids": {
                                    "$addToSet": {
                                        "$cond": [{"$gt": ["$user_messages", 0]}, "$user_id", None]
                                    }
                                },
                            }
                        }
                    ]
                ).to_list(length=None)
                for doc in persona_user_docs:
                    pid = str(doc.get("_id")) if doc.get("_id") else None
                    persona_user_sets.setdefault(pid, set()).update(
                        {str(uid) for uid in (doc.get("user_ids") or []) if uid}
                    )
            except Exception as exc:
                logger.debug("[Analytics] Snapshot persona users unavailable: %s", exc)
                snapshot_read_failed = True
            try:
                agent_user_docs = await snapshot_collection.aggregate(
                    base_match
                    + [
                        {
                            "$group": {
                                "_id": "$agent_id",
                                "user_ids": {
                                    "$addToSet": {
                                        "$cond": [{"$gt": ["$user_messages", 0]}, "$user_id", None]
                                    }
                                },
                            }
                        }
                    ]
                ).to_list(length=None)
                for doc in agent_user_docs:
                    aid = str(doc.get("_id")) if doc.get("_id") else None
                    agent_user_sets.setdefault(aid, set()).update(
                        {str(uid) for uid in (doc.get("user_ids") or []) if uid}
                    )
            except Exception as exc:
                logger.debug("[Analytics] Snapshot agent users unavailable: %s", exc)
                snapshot_read_failed = True

            try:
                hour_docs.extend(
                    await snapshot_collection.aggregate(
                        base_match
                        + [
                            {"$match": {"hour_messages": {"$type": "array"}}},
                            {"$unwind": {"path": "$hour_messages", "includeArrayIndex": "hour"}},
                            {
                                "$group": {
                                    "_id": {"date": "$date", "hour": "$hour"},
                                    "user_messages": {"$sum": "$hour_messages"},
                                    "tokens": {"$sum": {"$arrayElemAt": ["$hour_tokens", "$hour"]}},
                                }
                            },
                        ]
                    ).to_list(length=None)
                )
                for doc in await snapshot_collection.aggregate(
                    base_match
                    + [
                        {"$unwind": "$model_tokens"},
                        {
                            "$group": {
                                "_id": "$model_tokens.model",
                                "tokens": {"$sum": "$model_tokens.tokens"},
                            }
                        },
                    ]
                ).to_list(length=None):
                    model = str(doc.get("_id") or "unknown")
                    model_totals[model] = model_totals.get(model, 0) + int(doc.get("tokens", 0) or 0)
                for doc in await snapshot_collection.aggregate(
                    base_match
                    + [
                        {"$match": {"user_messages": {"$gt": 0}}},
                        {"$group": {"_id": "$date", "user_ids": {"$addToSet": "$user_id"}}},
                    ]
                ).to_list(length=None):
                    day_user_sets.setdefault(str(doc.get("_id")), set()).update(
                        {str(uid) for uid in (doc.get("user_ids") or []) if uid}
                    )
            except Exception as exc:
                logger.warning("[Analytics] Snapshot breakdown aggregation failed: %s", exc)
                extras_failed = True

        if historical_dates and (skip_historical or not historical_day_loaded):
            realtime = await _compute_realtime_aggregate(storage, filters, historical_dates)
            historical_items.extend(realtime.get("trend", []))
            realtime_user_ids.update({str(uid) for uid in realtime.get("active_user_ids", set()) if uid})
            try:
                fallback_docs = await storage.traces.aggregate(
                    usage_facts_stages(filters)
                    + [
                        {
                            "$group": {
                                "_id": None,
                                "active_user_ids": {
                                    "$addToSet": {
                                        "$cond": [{"$gt": ["$user_messages", 0]}, "$user_id", None]
                                    }
                                },
                            }
                        }
                    ]
                ).to_list(length=1)
                if fallback_docs:
                    fallback_dates = {
                        str(doc.get("_id"))
                        for doc in fallback_docs
                        if doc.get("_id") is not None
                    }
                    if not fallback_dates.intersection(historical_dates):
                        for doc in fallback_docs:
                            realtime_fallback_active_session_ids.update(
                                _flatten_ids(doc.get("active_session_ids"))
                            )
                            realtime_fallback_user_messages += int(
                                doc.get("user_messages", 0) or 0
                            )
                            realtime_fallback_tokens += int(
                                doc.get("tokens", doc.get("total_tokens", 0)) or 0
                            )
                    realtime_user_ids.update(
                        {
                            str(uid)
                            for uid in (fallback_docs[0].get("active_user_ids") or [])
                            if uid
                        }
                    )
            except Exception as exc:
                logger.debug("[Analytics] Realtime fallback user set unavailable: %s", exc)

        # Cold-start fallback only: until the worker has written today's
        # snapshot, today's metrics are aggregated live from traces.
        if today in dates and today_marker is None:
            today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=CST)
            tomorrow_dt = today_dt + timedelta(days=1)
            today_filters = UsageFilters(
                start=today_dt,
                end=tomorrow_dt,
                persona_preset_id=getattr(filters, "persona_preset_id", None),
                agent_id=getattr(filters, "agent_id", None),
                role_user_ids=getattr(filters, "role_user_ids", None),
            )
            fact_docs = []
            try:
                fact_docs = await storage.traces.aggregate(
                    usage_facts_stages(today_filters)
                    + [
                        {
                            "$group": {
                                "_id": None,
                                "user_messages": {"$sum": "$user_messages"},
                                "tokens": {"$sum": "$tokens"},
                                "active_session_ids": {
                                    "$addToSet": {
                                        "$cond": [{"$gt": ["$user_messages", 0]}, "$session_id", None]
                                    }
                                },
                            }
                        }
                    ]
                ).to_list(length=1)
            except Exception as exc:
                logger.warning("[Analytics] Realtime total aggregation failed: %s", exc)
            fact = fact_docs[0] if fact_docs else {}
            session_match = new_sessions_match(today_filters)
            try:
                today_new_sessions = int(await storage.sessions.count_documents(session_match))
            except Exception:
                today_new_sessions = 0
            today_active_ids = _flatten_ids(fact.get("active_session_ids"))
            historical_items.append(
                {
                    "date": today,
                    "new_sessions": today_new_sessions,
                    "active_sessions": len(today_active_ids),
                    "user_messages": int(fact.get("user_messages", 0) or 0),
                    "tokens": int(fact.get("tokens", 0) or 0),
                    "_active_session_ids_supported": True,
                    "_active_session_ids": today_active_ids,
                    "_new_session_ids_supported": False,
                    "_new_session_ids": set(),
                }
            )

            session_docs = await _aggregate_session_groups(storage, today_filters)
            persona_new = _session_metric_map(session_docs, "persona_preset_id")
            agent_new = _session_metric_map(session_docs, "agent_id")
            user_persona_new: dict[tuple[Any, Any], dict[str, Any]] = {
                (
                    doc.get("_id", {}).get("user_id"),
                    doc.get("_id", {}).get("persona_preset_id"),
                ): {
                    "new_sessions": int(doc.get("new_sessions", 0) or 0),
                    "new_session_ids": sorted(_flatten_ids(doc.get("new_session_ids"))),
                }
                for doc in session_docs
            }

            async def today_dimension(dimension: str) -> list[dict[str, Any]]:
                identifier: Any = {
                    "persona": "$persona_preset_id",
                    "agent": "$agent_id",
                    "user": "$user_id",
                    "user_persona": {
                        "user_id": "$user_id",
                        "persona_preset_id": "$persona_preset_id",
                    },
                }[dimension]
                group: dict[str, Any] = {
                    "_id": identifier,
                    "user_messages": {"$sum": "$user_messages"},
                    "tokens": {"$sum": "$tokens"},
                    "active_session_ids": {
                        "$addToSet": {
                            "$cond": [{"$gt": ["$user_messages", 0]}, "$session_id", None]
                        }
                    },
                    "last_active_at": {"$max": "$started_at"},
                }
                if dimension in {"persona", "agent"}:
                    group["active_user_ids"] = {
                        "$addToSet": {
                            "$cond": [{"$gt": ["$user_messages", 0]}, "$user_id", None]
                        }
                    }
                try:
                    return await storage.traces.aggregate(usage_facts_stages(today_filters) + [{"$group": group}]).to_list(length=None)
                except Exception as exc:
                    logger.warning("[Analytics] Realtime %s aggregation failed: %s", dimension, exc)
                    return []

            for doc in await today_dimension("persona"):
                pid = str(doc.get("_id")) if doc.get("_id") else None
                active_ids = _flatten_ids(doc.get("active_session_ids"))
                persona_user_sets.setdefault(pid, set()).update(
                    {str(uid) for uid in (doc.get("active_user_ids") or []) if uid}
                )
                persona_items.append(
                    {
                        "persona_preset_id": pid,
                        "persona_preset_name": doc.get("persona_preset_name"),
                        "new_sessions": persona_new.get(doc.get("_id"), {}).get("new_sessions", 0),
                        "new_session_ids": set(persona_new.get(doc.get("_id"), {}).get("new_session_ids", [])),
                        "active_sessions": len(active_ids),
                        "active_session_ids": active_ids,
                        "user_messages": int(doc.get("user_messages", 0) or 0),
                        "tokens": int(doc.get("tokens", 0) or 0),
                        "_new_session_ids_supported": True,
                        "_active_session_ids_supported": True,
                        "_new_session_ids": set(persona_new.get(doc.get("_id"), {}).get("new_session_ids", [])),
                        "_active_session_ids": active_ids,
                    }
                )
            for doc in await today_dimension("agent"):
                aid = str(doc.get("_id")) if doc.get("_id") else None
                active_ids = _flatten_ids(doc.get("active_session_ids"))
                agent_user_sets.setdefault(aid, set()).update(
                    {str(uid) for uid in (doc.get("active_user_ids") or []) if uid}
                )
                agent_items.append(
                    {
                        "agent_id": aid,
                        "new_sessions": agent_new.get(doc.get("_id"), {}).get("new_sessions", 0),
                        "new_session_ids": set(agent_new.get(doc.get("_id"), {}).get("new_session_ids", [])),
                        "active_sessions": len(active_ids),
                        "active_session_ids": active_ids,
                        "user_messages": int(doc.get("user_messages", 0) or 0),
                        "tokens": int(doc.get("tokens", 0) or 0),
                        "_new_session_ids_supported": True,
                        "_active_session_ids_supported": True,
                        "_new_session_ids": set(agent_new.get(doc.get("_id"), {}).get("new_session_ids", [])),
                        "_active_session_ids": active_ids,
                    }
                )
            for doc in await today_dimension("user"):
                uid = str(doc.get("_id")) if doc.get("_id") else None
                if uid:
                    user_items.append(
                        {
                            "user_id": uid,
                            "user_messages": int(doc.get("user_messages", 0) or 0),
                            "tokens": int(doc.get("tokens", 0) or 0),
                        }
                    )
                    if int(doc.get("user_messages", 0) or 0) > 0:
                        day_user_sets.setdefault(today, set()).add(uid)
            for doc in await today_dimension("user_persona"):
                identifier = doc.get("_id") or {}
                uid = str(identifier.get("user_id")) if identifier.get("user_id") else None
                if uid:
                    pid = identifier.get("persona_preset_id")
                    metric = user_persona_new.get((identifier.get("user_id"), pid), {})
                    user_persona_items.append(
                        {
                            "user_id": uid,
                            "persona_preset_id": str(pid) if pid else None,
                            "persona_preset_name": doc.get("persona_preset_name"),
                            "new_sessions": int(metric.get("new_sessions", 0) or 0),
                            "new_session_ids": set(metric.get("new_session_ids", [])),
                            "active_sessions": len(_flatten_ids(doc.get("active_session_ids"))),
                            "active_session_ids": _flatten_ids(doc.get("active_session_ids")),
                            "user_messages": int(doc.get("user_messages", 0) or 0),
                            "tokens": int(doc.get("tokens", 0) or 0),
                            "last_active_at": doc.get("last_active_at"),
                            "_new_session_ids_supported": True,
                            "_active_session_ids_supported": True,
                            "_new_session_ids": set(metric.get("new_session_ids", [])),
                            "_active_session_ids": _flatten_ids(doc.get("active_session_ids")),
                        }
                    )
            # A newly-created session may have no trace yet. Keep it in the
            # persona/agent/user×persona dimensions instead of relying on a
            # trace row to make the independent sessions aggregate visible.
            for session_doc in session_docs:
                identifier = session_doc.get("_id") or {}
                session_ids = _flatten_ids(session_doc.get("new_session_ids"))
                new_count = int(session_doc.get("new_sessions", 0) or 0)
                new_supported = bool(session_ids)
                persona_id = identifier.get("persona_preset_id")
                agent_id = identifier.get("agent_id")
                persona_items.append(
                    {
                        "persona_preset_id": str(persona_id) if persona_id else None,
                        "new_sessions": len(session_ids) if new_supported else new_count,
                        "new_session_ids": session_ids,
                        "active_sessions": 0,
                        "active_session_ids": set(),
                        "user_messages": 0,
                        "tokens": 0,
                        "_new_session_ids_supported": new_supported,
                        "_new_session_ids": session_ids,
                        "_active_session_ids_supported": True,
                        "_active_session_ids": set(),
                    }
                )
                agent_items.append(
                    {
                        "agent_id": str(agent_id) if agent_id else None,
                        "new_sessions": len(session_ids) if new_supported else new_count,
                        "new_session_ids": session_ids,
                        "active_sessions": 0,
                        "active_session_ids": set(),
                        "user_messages": 0,
                        "tokens": 0,
                        "_new_session_ids_supported": new_supported,
                        "_new_session_ids": session_ids,
                        "_active_session_ids_supported": True,
                        "_active_session_ids": set(),
                    }
                )
                user_id = identifier.get("user_id")
                if user_id is not None:
                    user_persona_items.append(
                        {
                            "user_id": str(user_id),
                            "persona_preset_id": str(persona_id) if persona_id else None,
                            "persona_preset_name": None,
                            "new_sessions": len(session_ids) if new_supported else new_count,
                            "new_session_ids": session_ids,
                            "active_sessions": 0,
                            "active_session_ids": set(),
                            "user_messages": 0,
                            "tokens": 0,
                            "last_active_at": None,
                            "_new_session_ids_supported": new_supported,
                            "_new_session_ids": session_ids,
                            "_active_session_ids_supported": True,
                            "_active_session_ids": set(),
                        }
                    )

        merged_days = _merge_metric_items(historical_items, "date")
        result["trend"] = [
            {
                "date": item.get("date"),
                "new_sessions": item.get("new_sessions", 0),
                "active_sessions": item.get("active_sessions", 0),
                "user_messages": int(item.get("user_messages", 0) or 0),
                "tokens": int(item.get("tokens", 0) or 0),
            }
            for item in sorted(merged_days, key=lambda value: str(value.get("date", "")))
        ]
        total_active_ids: set[str] = set()
        total_new_ids: set[str] = set()
        total_active_legacy = 0
        total_new_legacy = 0
        for item in merged_days:
            total_active_ids.update(item.get("_active_session_ids", set()))
            total_new_ids.update(item.get("_new_session_ids", set()))
            total_active_legacy += int(item.get("_active_sessions_legacy", 0) or 0)
            total_new_legacy += int(item.get("_new_sessions_legacy", 0) or 0)
        result["total"] = {
            "new_sessions": len(total_new_ids) + total_new_legacy,
            "active_sessions": (
                len(total_active_ids)
                + total_active_legacy
                + len(realtime_fallback_active_session_ids)
            ),
            "user_messages": (
                sum(int(item.get("user_messages", 0) or 0) for item in result["trend"])
                + realtime_fallback_user_messages
            ),
            "tokens": (
                sum(int(item.get("tokens", 0) or 0) for item in result["trend"])
                + realtime_fallback_tokens
            ),
        }

        result["by_persona"] = [
            _public_metric_item(item)
            for item in _merge_metric_items(persona_items, "persona_preset_id")
        ]
        result["by_agent"] = [
            _public_metric_item(item)
            for item in _merge_metric_items(agent_items, "agent_id")
        ]
        merged_users: dict[str, dict[str, Any]] = {}
        for item in user_items:
            uid = item.get("user_id")
            if not uid:
                continue
            target = merged_users.setdefault(uid, {"user_id": uid, "user_messages": 0, "tokens": 0})
            target["user_messages"] += int(item.get("user_messages", 0) or 0)
            target["tokens"] += int(item.get("tokens", 0) or 0)
        result["by_user"] = list(merged_users.values())
        # user×persona rows must not collapse different personas, and legacy
        # rows without ID arrays must keep their stored integers.
        result["by_user_persona"] = [
            _public_metric_item(item)
            for item in _merge_metric_items(
                user_persona_items, ("user_id", "persona_preset_id")
            )
        ]

        _apply_breakdowns(result, dates, hour_docs, model_totals, day_user_sets)
        result["extras_complete"] = not extras_failed and await _extras_complete(
            storage, historical_dates, skip_historical, today in dates and today_marker is None
        )

        for item in result["by_persona"]:
            item["active_users"] = len(persona_user_sets.get(item.get("persona_preset_id"), set()))
        for item in result["by_agent"]:
            item["active_users"] = len(agent_user_sets.get(item.get("agent_id"), set()))

        # Login activity rows carry no persona/agent/role dimension, so any
        # dimensional filter must derive its user counts from filtered rows.
        has_dimension_filter = bool(
            getattr(filters, "persona_preset_id", None)
            or getattr(filters, "agent_id", None)
            or getattr(filters, "role_user_ids", None) is not None
        )
        if has_dimension_filter:
            result["using_users"] = len({item["user_id"] for item in result["by_user"] if item.get("user_id")})
            result["active_users"] = result["using_users"]
        else:
            from src.infra.analytics.activity_storage import ActivityStorage

            activity = ActivityStorage()
            start_str, end_str = range_to_date_strings(filters.start, filters.end)
            using_users = await activity.distinct_users(start_str, end_str, source="message")
            active_users = await activity.distinct_users(start_str, end_str)
            using_user_ids = {str(uid) for uid in using_users if uid}
            active_user_ids = {str(uid) for uid in active_users if uid}
            result["using_users"] = len(using_user_ids) or len(realtime_user_ids)
            result["active_users"] = len(active_user_ids) or len(realtime_user_ids)
    except ImportError:
        result["using_users"] = len(result["by_user"])
        result["active_users"] = result["using_users"]
    except Exception as exc:
        logger.warning("[Analytics] Merging results failed: %s", exc)
        result["using_users"] = len(result["by_user"])
        result["active_users"] = result["using_users"]

    if snapshot_read_failed:
        result["snapshot_complete"] = False
    return result


async def _compute_realtime_aggregate(storage: Any, filters: Any, dates: list[str]) -> dict[str, Any]:
    """Compute a complete per-date realtime fallback without cross-day inflation."""
    from src.infra.analytics.usage_query import new_sessions_match, usage_facts_stages

    result: dict[str, Any] = {
        "total": {"new_sessions": 0, "active_sessions": 0, "user_messages": 0, "tokens": 0},
        "trend": [],
        "active_user_ids": set(),
    }
    requested_dates = set(dates)
    trace_pipeline = usage_facts_stages(filters) + [
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$started_at",
                        "timezone": "Asia/Shanghai",
                    }
                },
                "user_messages": {"$sum": "$user_messages"},
                "tokens": {"$sum": "$tokens"},
                "active_session_ids": {
                    "$addToSet": {
                        "$cond": [{"$gt": ["$user_messages", 0]}, "$session_id", None]
                    }
                },
                "active_user_ids": {
                    "$addToSet": {
                        "$cond": [{"$gt": ["$user_messages", 0]}, "$user_id", None]
                    }
                },
            }
        }
    ]
    try:
        trace_docs = await storage.traces.aggregate(trace_pipeline).to_list(length=None)
    except Exception as exc:
        logger.warning("[Analytics] Realtime fallback traces failed: %s", exc)
        trace_docs = []

    session_by_date: dict[str, dict[str, Any]] = {}
    try:
        session_docs = await storage.sessions.aggregate(
            [
                {"$match": new_sessions_match(filters)},
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$created_at",
                                "timezone": "Asia/Shanghai",
                            }
                        },
                        "new_sessions": {"$sum": 1},
                        "new_session_ids": {
                            "$addToSet": {"$ifNull": ["$session_id", {"$toString": "$_id"}]}
                        },
                    }
                },
            ]
        ).to_list(length=None)
        for doc in session_docs:
            date = str(doc.get("_id") or "")
            if date not in requested_dates:
                continue
            ids = _flatten_ids(doc.get("new_session_ids"))
            session_by_date[date] = {
                "ids": ids,
                "count": len(ids) if ids else int(doc.get("new_sessions", 0) or 0),
                "supported": bool(ids),
            }
    except Exception as exc:
        logger.debug("[Analytics] Realtime fallback session aggregate failed: %s", exc)
        for date in dates:
            date_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=CST)
            date_filters = type(filters)(
                start=date_dt,
                end=date_dt + timedelta(days=1),
                persona_preset_id=getattr(filters, "persona_preset_id", None),
                agent_id=getattr(filters, "agent_id", None),
                role_user_ids=getattr(filters, "role_user_ids", None),
            )
            try:
                session_by_date[date] = {
                    "ids": set(),
                    "count": int(
                        await storage.sessions.count_documents(new_sessions_match(date_filters))
                    ),
                    "supported": False,
                }
            except Exception:
                session_by_date[date] = {"ids": set(), "count": 0, "supported": False}

    by_date = {
        str(doc.get("_id")): doc
        for doc in trace_docs
        if str(doc.get("_id")) in requested_dates
    }
    for doc in by_date.values():
        result["active_user_ids"].update(
            {str(uid) for uid in (doc.get("active_user_ids") or []) if uid}
        )

    for date in dates:
        doc = by_date.get(date, {})
        day_active_ids = _flatten_ids(doc.get("active_session_ids"))
        session = session_by_date.get(date, {"ids": set(), "count": 0, "supported": True})
        result["trend"].append(
            {
                "date": date,
                "new_sessions": int(session["count"]),
                "active_sessions": len(day_active_ids),
                "user_messages": int(doc.get("user_messages", 0) or 0),
                "tokens": int(doc.get("tokens", 0) or 0),
                "_active_session_ids_supported": True,
                "_active_session_ids": day_active_ids,
                "_new_session_ids_supported": bool(session["supported"]),
                "_new_session_ids": set(session["ids"]),
                "_new_sessions_legacy": int(session["count"]) if not session["supported"] else 0,
            }
        )

    total_active_ids: set[str] = set()
    new_ids: set[str] = set()
    active_legacy = 0
    new_legacy = 0
    for item in result["trend"]:
        total_active_ids.update(item["_active_session_ids"])
        new_ids.update(item["_new_session_ids"])
        new_legacy += int(item.get("_new_sessions_legacy", 0) or 0)
    result["total"] = {
        "new_sessions": len(new_ids) + new_legacy,
        "active_sessions": len(total_active_ids) + active_legacy,
        "user_messages": sum(int(item.get("user_messages", 0) or 0) for item in result["trend"]),
        "tokens": sum(int(item.get("tokens", 0) or 0) for item in result["trend"]),
    }
    return result
