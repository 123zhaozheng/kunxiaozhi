"""Snapshot layer for immutable historical analytics metrics.

This module provides read_or_freeze() - a strategy that:
- Today's data is always computed in real-time from traces (will vary).
- Historical days are frozen via snapshots; first query computes and writes,
  subsequent queries return the same numbers even if underlying data changes.
- Missing snapshots or read failures degrade gracefully to real-time computation.

Key invariant: The same historical date queried twice returns identical numbers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from bson import ObjectId

from src.infra.analytics.date_range import CST, day_buckets, today_cst
from src.infra.logging import get_logger
from src.infra.storage.redis import create_redis_client

logger = get_logger(__name__)

SNAPSHOT_COLLECTION_NAME = "analytics_daily_snapshot"
_SNAPSHOT_LOCK_KEY_PREFIX = "analytics:snapshot:freeze:"
_SNAPSHOT_LOCK_TTL_SECONDS = 60


def snapshot_match(filters: Any, dates: list[str]) -> dict:
    """Build MongoDB $match for missing snapshot check.

    Args:
        filters: UsageFilters-like object with persona_preset_id / agent_id attrs.
        dates: List of "YYYY-MM-DD" strings to check.

    Returns:
        A $match document matching snapshot docs for those dates.
    """
    match: dict[str, Any] = {"date": {"$in": dates}}
    pp_id = getattr(filters, "persona_preset_id", None)
    ag_id = getattr(filters, "agent_id", None)
    if pp_id:
        match["persona_preset_id"] = pp_id
    if ag_id:
        match["agent_id"] = ag_id
    return match


def snapshot_group_stages(
    dimension: Literal["total", "day", "persona", "agent", "user"],
) -> list[dict]:
    """Build aggregation stages for merging snapshot + real-time results."""
    _id_mapping: dict[str, Any] = {
        "total": None,
        "day": "$date",
        "persona": "$persona_preset_id",
        "agent": "$agent_id",
        "user": "$user_id",
    }
    group_stage = {
        "$group": {
            "_id": _id_mapping[dimension],
            "new_sessions": {"$sum": {"$ifNull": ["$new_sessions", 0]}},
            "active_sessions": {"$sum": {"$ifNull": ["$active_sessions", 0]}},
            "user_messages": {"$sum": {"$ifNull": ["$user_messages", 0]}},
            "tokens": {"$sum": {"$ifNull": ["$tokens", 0]}},
            "last_active_at": {"$max": "$last_active_at"},
        }
    }
    if dimension != "total":
        return [group_stage, {"$sort": {"_id": 1}}]
    return [group_stage]


def _to_half_open(match: dict[str, Any], field: str) -> dict[str, Any]:
    """将 ``field`` 上的闭区间 ``$lte`` 改写成半开区间 ``$lt``。

    日快照按 ``[date 00:00+08:00, date+1d 00:00+08:00)`` 切分，若保留 ``$lte``，
    次日零点整的文档会同时落进相邻两天，导致边界重复计数。
    """
    cond = match.get(field)
    if isinstance(cond, dict) and "$lte" in cond:
        cond = dict(cond)
        cond["$lt"] = cond.pop("$lte")
        match = dict(match)
        match[field] = cond
    return match


async def read_or_freeze(filters: Any, storage: Any | None = None) -> dict[str, Any]:
    """Read usage metrics with snapshot-first strategy.

    Strategy:
    1. Determine today (CST) and all dates in the interval.
    2. Find which historical dates lack snapshots.
    3. If missing: acquire lock, compute real-time aggregates per missing date,
       batch upsert snapshot docs ($setOnInsert semantics), release lock.
    4. Read: historical days from analytics_daily_snapshot, today from traces.
    5. Merge both result sets and return.

    Invariants guaranteed:
    1. Same historical date produces identical numbers across queries.
    2. Today's numbers may change (real-time, never frozen).
    3. Snapshot miss or read failure degrades to real-time, never empty.

    Returns:
        Dict with keys: total, trend, by_persona, by_agent, by_user,
        active_users, using_users.
    """
    from src.infra.analytics.usage_query import UsageFilters

    # Normalize to UF if needed
    if not isinstance(filters, UsageFilters):
        filters = UsageFilters(
            start=filters.start,
            end=filters.end,
            persona_preset_id=getattr(filters, "persona_preset_id", None),
            agent_id=getattr(filters, "agent_id", None),
            role_user_ids=getattr(filters, "role_user_ids", None),
        )

    today = today_cst()
    start_str = filters.start.strftime("%Y-%m-%d")
    end_dt_for_buckets = filters.end.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if end_dt_for_buckets > filters.end:
        end_dt_for_buckets -= timedelta(days=1)
    end_inclusive = end_dt_for_buckets.strftime("%Y-%m-%d")

    dates = day_buckets(start_str, end_inclusive)

    if storage is None:
        from src.infra.analytics.storage import AnalyticsStorage
        storage = AnalyticsStorage()
    redis_client = None
    try:
        redis_client = create_redis_client(isolated_pool=True)
    except Exception as ex:
        logger.warning("Redis client creation failed for snapshot layer: %s", ex)

    try:
        missing_dates = [d for d in dates if d != today]

        if missing_dates:
            match_doc: dict[str, Any] = {"date": {"$in": missing_dates}}
            if filters.persona_preset_id:
                match_doc["persona_preset_id"] = filters.persona_preset_id
            if filters.agent_id:
                match_doc["agent_id"] = filters.agent_id

            cursor = storage.snapshot.find(match_doc)
            existing_docs = await cursor.to_list(length=None)
            existing_dates = set(d.get("date") for d in existing_docs if d.get("date"))
            truly_missing = [d for d in missing_dates if d not in existing_dates]

            # Only freeze if there are truly missing dates AND we have a valid redis_client
            if truly_missing and redis_client is not None:
                await _freeze_dates(storage, redis_client, truly_missing, filters)
    except Exception as ex:
        logger.warning("Error checking/freeze snapshots: %s", ex)
    finally:
        if redis_client is not None:
            await redis_client.aclose()

    return await _merge_results(storage, filters, dates)


async def _freeze_dates(
    storage: Any,
    redis_client: Any,
    dates: list[str],
    filters: Any,
) -> None:
    """Compute and freeze snapshots for given dates using Redis lock."""
    from src.infra.analytics.usage_query import (
        UsageFilters,
        new_sessions_match,
        usage_facts_stages,
    )

    lock_key = f"{_SNAPSHOT_LOCK_KEY_PREFIX}{dates[0]}"
    instance_id = str(ObjectId())

    acquired = await redis_client.set(lock_key, instance_id, nx=True, ex=_SNAPSHOT_LOCK_TTL_SECONDS)
    if not acquired:
        logger.warning("Could not acquire snapshot freeze lock for %s", dates)
        return

    lua_renew = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

    renew_task: asyncio.Task | None = None

    def stop_renewal() -> None:
        nonlocal renew_task
        if renew_task and not renew_task.done():
            renew_task.cancel()
            try:
                renew_task.result()
            except asyncio.CancelledError:
                pass

    async def renew_loop() -> None:
        try:
            while True:
                await asyncio.sleep(_SNAPSHOT_LOCK_TTL_SECONDS / 3)
                renewed = await redis_client.eval(
                    lua_renew, 1, lock_key, instance_id, _SNAPSHOT_LOCK_TTL_SECONDS
                )
                if not renewed:
                    logger.warning("Snapshot freeze lock was lost")
                    break
        except asyncio.CancelledError:
            pass

    renew_task = asyncio.create_task(renew_loop())

    try:
        collection = storage.snapshot
        now_utc = datetime.now(timezone.utc)

        for target_date in dates:
            date_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=CST
            )
            next_date_dt = date_dt + timedelta(days=1)

            date_filters = UsageFilters(
                start=date_dt,
                end=next_date_dt,
                persona_preset_id=getattr(filters, "persona_preset_id", None),
                agent_id=getattr(filters, "agent_id", None),
                role_user_ids=getattr(filters, "role_user_ids", None),
            )

            # Get granular docs grouped by user/persona/agent from traces
            base_stages = usage_facts_stages(date_filters)
            granular_pipeline = base_stages + [
                {
                    "$group": {
                        "_id": {
                            "user_id": "$user_id",
                            "persona_preset_id": "$persona_preset_id",
                            "agent_id": "$agent_id",
                        },
                        "user_messages": {"$sum": "$user_messages"},
                        "tokens": {"$sum": "$tokens"},
                        "active_session_ids": {"$addToSet": {"$cond": [{"$gt": ["$user_messages", 0]}, "$session_id", None]}},
                        "last_active_at": {"$max": "$started_at"},
                    }
                },
                {"$match": {"_id.user_id": {"$nin": [None, ""]}}}
            ]

            try:
                granular_docs = await storage.traces.aggregate(granular_pipeline).to_list(length=None)
            except Exception:
                granular_docs = []

            # Build new_sessions map from sessions collection with correct $lt boundary
            session_match = new_sessions_match(date_filters)
            # Fix: use $lt instead of $lte to avoid off-by-one at day boundary
            created_at_match = session_match.get("created_at", {})
            if isinstance(created_at_match, dict) and "$lte" in created_at_match:
                lt_value = created_at_match["$lte"]
                # Convert inclusive $lte to exclusive $lt
                if hasattr(lt_value, "microsecond"):
                    # For datetime, add 1 microsecond then use <
                    created_at_match["$lt"] = lt_value.replace(microsecond=lt_value.microsecond + 1) if lt_value.microsecond < 999999 else lt_value.replace(microsecond=0) + timedelta(seconds=1)
                else:
                    created_at_match["$lt"] = lt_value

            try:
                new_sessions_docs = await storage.sessions.aggregate([
                    {"$match": session_match},
                    {
                        "$group": {
                            "_id": {
                                "user_id": "$user_id",
                                "persona_preset_id": "$metadata.persona_preset_id",
                                "agent_id": "$agent_id",
                            },
                            "new_sessions": {"$sum": 1},
                        }
                    },
                ]).to_list(length=None)
            except Exception:
                new_sessions_docs = []

            # Build lookup map for new_sessions
            new_sessions_map: dict[tuple[str, str | None, str], int] = {}
            for nsdoc in new_sessions_docs:
                eid = nsdoc["_id"]
                key = (str(eid.get("user_id")), eid.get("persona_preset_id"), eid.get("agent_id"))
                new_sessions_map[key] = int(nsdoc.get("new_sessions", 0) or 0)

            # Build ops for bulk write with proper new_sessions per group
            ops: list[dict] = []
            for gdoc in granular_docs:
                eid = gdoc["_id"]
                active_sess = len({s for s in (gdoc.get("active_session_ids") or []) if s})

                key = (eid["user_id"], eid["persona_preset_id"], eid["agent_id"])
                new_sess = new_sessions_map.get(key, 0)

                doc_to_upsert = {
                    "date": target_date,
                    "user_id": str(eid["user_id"]),
                    "persona_preset_id": eid["persona_preset_id"],
                    "agent_id": eid["agent_id"],
                    "new_sessions": new_sess,
                    "active_sessions": active_sess,
                    "user_messages": int(gdoc.get("user_messages", 0) or 0),
                    "tokens": int(gdoc.get("tokens", 0) or 0),
                    "last_active_at": gdoc.get("last_active_at"),
                    "frozen_at": now_utc,
                }

                ops.append({
                    "updateOne": {
                        "filter": {
                            "date": target_date,
                            "user_id": doc_to_upsert["user_id"],
                            "persona_preset_id": doc_to_upsert["persona_preset_id"],
                            "agent_id": doc_to_upsert["agent_id"],
                        },
                        "update": {"$setOnInsert": doc_to_upsert},
                        "upsert": True,
                    }
                })

            if ops:
                try:
                    await collection.bulk_write(ops, ordered=False)
                    logger.info("Frozen snapshots for %s: %d rows", target_date, len(ops))
                except Exception as ex:
                    logger.warning("Bulk write for %s failed: %s", target_date, ex)

    finally:
        stop_renewal()
        lua_release = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
        try:
            await redis_client.eval(lua_release, 1, lock_key, instance_id)
        except Exception:
            pass


async def _merge_results(storage: Any, filters: Any, dates: list[str]) -> dict[str, Any]:
    """Merge snapshot (historical) and real-time (today) results.

    Ensures invariant: Never returns empty data - degrades to realtime on any failure.
    """
    from src.infra.analytics.usage_query import UsageFilters, new_sessions_match, usage_facts_stages

    today = today_cst()
    historical_dates = [d for d in dates if d != today]

    result: dict[str, Any] = {
        "total": {"new_sessions": 0, "active_sessions": 0, "user_messages": 0, "tokens": 0},
        "trend": [],
        "by_persona": [],
        "by_agent": [],
        "by_user": [],
        "active_users": 0,
        "using_users": 0,
    }

    try:
        # Process historical dates from snapshots
        if historical_dates:
            snapshot_collection = storage.snapshot

            for dim_str in ("total", "day", "persona"):
                pipeline: list[dict[str, Any]] = [{"$match": {"date": {"$in": historical_dates}}}]
                if getattr(filters, "persona_preset_id", None):
                    pipeline.append({"$match": {"persona_preset_id": filters.persona_preset_id}})
                if getattr(filters, "agent_id", None):
                    pipeline.append({"$match": {"agent_id": filters.agent_id}})
                pipeline.extend(snapshot_group_stages(dim_str))  # type: ignore[arg-type]

                try:
                    docs = await snapshot_collection.aggregate(pipeline).to_list(length=None)
                except Exception:
                    docs = []

                if dim_str == "total" and docs:
                    d = docs[0]
                    result["total"]["new_sessions"] = int(d.get("new_sessions", 0) or 0)
                    result["total"]["active_sessions"] = int(d.get("active_sessions", 0) or 0)
                    result["total"]["user_messages"] = int(d.get("user_messages", 0) or 0)
                    result["total"]["tokens"] = int(d.get("tokens", 0) or 0)
                elif dim_str == "day":
                    result["trend"].extend([{"date": str(d.get("_id")), **{k: int(v or 0) for k, v in d.items() if k != "_id"}} for d in docs])
                elif dim_str == "persona":
                    result["by_persona"].extend([{"persona_preset_id": str(d.get("_id")) if d.get("_id") else None, **{k: int(v or 0) for k, v in d.items() if k != "_id"}} for d in docs])

            # User dimension
            user_pipeline = [
                {"$match": {"date": {"$in": historical_dates}}},
                {"$group": {"_id": "$user_id", "user_messages": {"$sum": "$user_messages"}, "tokens": {"$sum": "$tokens"}}},
            ]
            try:
                user_docs = await snapshot_collection.aggregate(user_pipeline).to_list(length=None)
                result["by_user"].extend([{"user_id": str(d.get("_id")), "user_messages": int(d.get("user_messages", 0) or 0), "tokens": int(d.get("tokens", 0) or 0)} for d in user_docs if d.get("_id")])
            except Exception:
                pass

            # Fallback: for historical dates with no snapshot data, compute realtime
            # Check if we have any snapshot data
            has_snapshot_data = bool(result["trend"]) or bool(result["by_user"])
            if not has_snapshot_data and historical_dates:
                # Compute entire range as realtime fallback
                date_filters = UsageFilters(
                    start=filters.start,
                    end=filters.end,
                    persona_preset_id=getattr(filters, "persona_preset_id", None),
                    agent_id=getattr(filters, "agent_id", None),
                    role_user_ids=getattr(filters, "role_user_ids", None),
                )
                realtime_result = await _compute_realtime_aggregate(storage, date_filters, historical_dates)
                result["trend"].extend(realtime_result.get("trend", []))
                result["total"]["user_messages"] += realtime_result.get("total", {}).get("user_messages", 0)
                result["total"]["tokens"] += realtime_result.get("total", {}).get("tokens", 0)

        # Process today separately (always realtime, never frozen)
        today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=CST)
        tomorrow_dt = today_dt + timedelta(days=1)
        today_filters = UsageFilters(
            start=today_dt,
            end=tomorrow_dt,
            persona_preset_id=getattr(filters, "persona_preset_id", None),
            agent_id=getattr(filters, "agent_id", None),
            role_user_ids=getattr(filters, "role_user_ids", None),
        )

        # Only include today in trend if it's in the date range
        if today in dates:
            facts_stages = usage_facts_stages(today_filters) + [
                {
                    "$group": {
                        "_id": None,
                        "user_messages": {"$sum": "$user_messages"},
                        "tokens": {"$sum": "$tokens"},
                        "active_session_ids": {"$addToSet": {"$cond": [{"$gt": ["$user_messages", 0]}, "$session_id", None]}},
                    }
                },
            ]

            try:
                facts_docs = await storage.traces.aggregate(facts_stages).to_list(length=1)
            except Exception as ex:
                logger.warning("Real-time aggregate for today failed: %s", ex)
                facts_docs = []

            fact_doc = facts_docs[0] if facts_docs else {}
            today_user_messages = int(fact_doc.get("user_messages", 0) or 0)
            today_tokens = int(fact_doc.get("tokens", 0) or 0)
            today_active_sessions = len({s for s in (fact_doc.get("active_session_ids") or []) if s})

            # Get new sessions for today with correct $lt boundary
            session_match = new_sessions_match(today_filters)
            created_at_match = session_match.get("created_at", {})
            if isinstance(created_at_match, dict) and "$lte" in created_at_match:
                lt_value = created_at_match["$lte"]
                if hasattr(lt_value, "microsecond"):
                    created_at_match["$lt"] = lt_value.replace(microsecond=lt_value.microsecond + 1) if lt_value.microsecond < 999999 else lt_value.replace(microsecond=0) + timedelta(seconds=1)
                else:
                    created_at_match["$lt"] = lt_value

            try:
                today_new_sessions = await storage.sessions.count_documents(session_match)
            except Exception:
                today_new_sessions = 0

            result["trend"].append({
                "date": today,
                "new_sessions": today_new_sessions,
                "active_sessions": today_active_sessions,
                "user_messages": today_user_messages,
                "tokens": today_tokens,
            })

            result["total"]["new_sessions"] += today_new_sessions
            result["total"]["active_sessions"] += today_active_sessions
            result["total"]["user_messages"] += today_user_messages
            result["total"]["tokens"] += today_tokens

        # Active users / using_users from S2's activity_storage (may not exist yet)
        result["active_users"] = 0
        result["using_users"] = 0

        try:
            from src.infra.analytics.activity_storage import ActivityStorage
            _activity_storage = ActivityStorage()
            start_str = filters.start.strftime("%Y-%m-%d")
            end_str = filters.end.strftime("%Y-%m-%d")

            # Get distinct users who sent messages
            using_users_list = await _activity_storage.distinct_users(start_str, end_str, source="message")
            result["using_users"] = len(using_users_list)

            # Get all active users (login + message)
            active_users_list = await _activity_storage.distinct_users(start_str, end_str)
            result["active_users"] = len(active_users_list)

            # Contract: if persona/agent filter applied, active_users = using_users
            # because login records don't have persona/agent归属
            if getattr(filters, "persona_preset_id", None) or getattr(filters, "agent_id", None):
                result["active_users"] = result["using_users"]

        except ImportError:
            # ActivityStorage not available yet (S2 work in progress)
            # Fall back to trace-based count
            logger.info("ActivityStorage not available, using fallback for active users")
            result["using_users"] = len(result.get("by_user", []))
            result["active_users"] = result["using_users"]

        except Exception as ex:
            # Degrade gracefully
            logger.warning("ActivityStorage lookup failed: %s, using fallback", ex)
            result["using_users"] = len(result.get("by_user", []))
            result["active_users"] = result["using_users"]

    except Exception as ex:
        logger.warning("Merging results failed: %s, returning empty with zeros", ex)

    return result


async def _compute_realtime_aggregate(
    storage: Any,
    filters: Any,
    dates: list[str],
) -> dict[str, Any]:
    """Compute aggregate for dates using traces directly (fallback)."""
    from src.infra.analytics.usage_query import usage_facts_stages

    result: dict[str, Any] = {"total": {}, "trend": []}

    # Group by date for trend
    facts_stages = usage_facts_stages(filters) + [
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$started_at", "timezone": "Asia/Shanghai"}},
                "user_messages": {"$sum": "$user_messages"},
                "tokens": {"$sum": "$tokens"},
                "active_sessions": {"$sum": {"$cond": [{"$gt": ["$user_messages", 0]}, 1, 0]}},
                "new_sessions": {"$sum": {"$cond": [{"$gt": ["$user_messages", 0]}, 0, 0]}},
            }
        },
    ]

    try:
        trend_docs = await storage.traces.aggregate(facts_stages).to_list(length=None)
        for td in trend_docs:
            date_str = td.get("_id", "")
            if date_str in dates:
                result["trend"].append({
                    "date": date_str,
                    "user_messages": int(td.get("user_messages", 0)),
                    "tokens": int(td.get("tokens", 0)),
                    "active_sessions": int(td.get("active_sessions", 0)),
                    "new_sessions": int(td.get("new_sessions", 0)),
                })

        result["total"] = {
            "user_messages": sum(t.get("user_messages", 0) for t in result["trend"]),
            "tokens": sum(t.get("tokens", 0) for t in result["trend"]),
        }
    except Exception as ex:
        logger.warning("Realtime aggregate fallback failed: %s", ex)

    return result
