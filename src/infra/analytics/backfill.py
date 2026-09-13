"""Analytics backfill worker.

Reconstructs historical ``user_daily_activity`` (source=message only) and
``analytics_daily_snapshot`` documents from existing traces.  Login history
cannot be recovered because it was never persisted separately.

Shape mirrors :mod:`src.infra.session.backfill`:
- Redis distributed lock (key ``analytics:backfill:lock``).
- Batched processing with ``asyncio.sleep`` between batches.
- Lock renewal while a batch runs.
- Idempotent upserts (safe to re-run).
- Progress tracked in ``analytics_backfill_state`` collection.
- Failures logged as warnings; never block application startup.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.infra.analytics.date_range import CST
from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.storage.redis import create_redis_client
from src.kernel.config import settings

logger = get_logger(__name__)

BACKFILL_LOCK_KEY = "analytics:backfill:lock"
BACKFILL_LOCK_TTL_SECONDS = 60
BACKFILL_BATCH_DAYS = 7
BACKFILL_BATCH_DELAY_SECONDS = 0.5
BACKFILL_LOCK_RENEW_INTERVAL_SECONDS = BACKFILL_LOCK_TTL_SECONDS / 3
STATE_COLLECTION = "analytics_backfill_state"
STATE_DOC_ID = "analytics_daily"

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


class AnalyticsBackfillWorker:
    """Backfill historical activity records and daily snapshots."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        batch_days: int = BACKFILL_BATCH_DAYS,
        batch_delay_seconds: float = BACKFILL_BATCH_DELAY_SECONDS,
        lock_ttl_seconds: int = BACKFILL_LOCK_TTL_SECONDS,
        renew_interval_seconds: float = BACKFILL_LOCK_RENEW_INTERVAL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._batch_days = batch_days
        self._batch_delay_seconds = batch_delay_seconds
        self._lock_ttl_seconds = lock_ttl_seconds
        self._renew_interval_seconds = renew_interval_seconds
        self._lock_value: str | None = None
        self._instance_id = str(uuid.uuid4())
        self._renew_task: asyncio.Task[None] | None = None

    # ── public API ──────────────────────────────────────────────────

    async def run_once(self) -> int:
        """Process one batch of days if this instance owns the lock.

        Returns the number of days processed (0 when lock not acquired or
        nothing remains).
        """
        acquired = await self._acquire_lock()
        if not acquired:
            return 0

        self._start_lock_renewal()
        try:
            return await self._process_batch()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analytics backfill batch failed: %s", exc)
            return 0
        finally:
            await self._stop_lock_renewal()
            await self._release_lock()

    async def run_until_complete(self) -> int:
        """Run batches until no unprocessed days remain."""
        total = 0
        while True:
            count = await self.run_once()
            if count <= 0:
                return total
            total += count
            await asyncio.sleep(self._batch_delay_seconds)

    async def close(self) -> None:
        await self._stop_lock_renewal()
        redis_client = self._redis
        self._redis = None
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:  # noqa: BLE001
                pass

    # ── internals ───────────────────────────────────────────────────

    async def _process_batch(self) -> int:
        db = get_mongo_client()[settings.MONGODB_DB]
        traces_col = db[settings.MONGODB_TRACES_COLLECTION]
        try:
            sessions_col = db[settings.MONGODB_SESSIONS_COLLECTION]
        except KeyError:
            # Older lightweight test doubles may not expose sessions.
            sessions_col = None
        activity_col = db["user_daily_activity"]
        snapshot_col = db["analytics_daily_snapshot"]
        state_col = db[STATE_COLLECTION]

        # Determine earliest trace date (CST)
        earliest_doc = await traces_col.find_one(
            {}, sort=[("started_at", 1)], projection={"started_at": 1}
        )
        if not earliest_doc or not earliest_doc.get("started_at"):
            logger.info("Analytics backfill: no traces found, nothing to do")
            return 0
        earliest_dt = earliest_doc["started_at"]
        if earliest_dt.tzinfo is None:
            earliest_dt = earliest_dt.replace(tzinfo=timezone.utc)
        earliest_date_str = earliest_dt.astimezone(CST).strftime("%Y-%m-%d")

        # Load progress cursor
        state_doc = await state_col.find_one({"_id": STATE_DOC_ID})
        cursor_date_str: str | None = state_doc.get("cursor_date") if state_doc else None
        failed_dates: list[str] = list(state_doc.get("failed_dates", [])) if state_doc else []
        failed_dates = sorted({date for date in failed_dates if date >= earliest_date_str})

        # Retry failed dates before advancing the normal cursor. A failed day
        # is never silently skipped by a later successful cursor update.
        days: list[str] = []
        if failed_dates:
            days = failed_dates[: self._batch_days]
            start_dt = datetime.strptime(days[0], "%Y-%m-%d").replace(tzinfo=CST)
        elif cursor_date_str and cursor_date_str >= earliest_date_str:
            # Advance past last completed date
            cursor_dt = datetime.strptime(cursor_date_str, "%Y-%m-%d").replace(
                tzinfo=CST
            )
            start_dt = cursor_dt + timedelta(days=1)
        else:
            start_dt = datetime.strptime(earliest_date_str, "%Y-%m-%d").replace(
                tzinfo=CST
            )

        today_str = datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d")
        # Never backfill today (it's always real-time)
        if start_dt.strftime("%Y-%m-%d") >= today_str:
            return 0

        end_dt = start_dt + timedelta(days=self._batch_days)
        end_date_str = end_dt.strftime("%Y-%m-%d")
        if end_date_str > today_str:
            end_dt = datetime.strptime(today_str, "%Y-%m-%d").replace(tzinfo=CST)
            end_date_str = today_str

        # Generate day list for the normal cursor path.
        if not days:
            cur = start_dt
            while cur.strftime("%Y-%m-%d") < today_str and cur < end_dt:
                days.append(cur.strftime("%Y-%m-%d"))
                cur += timedelta(days=1)
        if not days:
            return 0

        logger.info(
            "Analytics backfill processing %d days: %s .. %s",
            len(days),
            days[0],
            days[-1],
        )

        now_utc = datetime.now(timezone.utc)
        processed_count = 0

        for target_date in days:
            date_start = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=CST)
            date_end = date_start + timedelta(days=1)

            activity_ok = await self._backfill_activity_for_day(
                activity_col, traces_col, target_date, date_start, date_end, now_utc
            )
            snapshot_ok = await self._backfill_snapshot_for_day(
                snapshot_col,
                traces_col,
                sessions_col,
                target_date,
                date_start,
                date_end,
                now_utc,
            )

            if not (activity_ok and snapshot_ok):
                if target_date not in failed_dates:
                    failed_dates.append(target_date)
                    failed_dates.sort()
                try:
                    await state_col.update_one(
                        {"_id": STATE_DOC_ID},
                        {"$set": {"failed_dates": failed_dates, "updated_at": now_utc}},
                        upsert=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to persist failed backfill date %s: %s", target_date, exc)
                continue

            failed_dates = [date for date in failed_dates if date != target_date]
            # Update cursor only after both activity and snapshot succeeded.
            try:
                await state_col.update_one(
                    {"_id": STATE_DOC_ID},
                    {
                        "$set": {
                            "cursor_date": target_date,
                            "failed_dates": failed_dates,
                            "updated_at": now_utc,
                        }
                    },
                    upsert=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist backfill cursor: %s", exc)
            processed_count += 1

        return processed_count

    # ── per-day helpers ─────────────────────────────────────────────

    @staticmethod
    async def _backfill_activity_for_day(
        activity_col: Any,
        traces_col: Any,
        target_date: str,
        date_start: datetime,
        date_end: datetime,
        now_utc: datetime,
    ) -> bool:
        """Derive user_daily_activity docs with source=message for one day."""
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "started_at": {"$gte": date_start, "$lt": date_end},
                    "events.event_type": "user:message",
                }
            },
            {
                "$group": {
                    "_id": "$user_id",
                    "first_at": {"$min": "$started_at"},
                    "last_at": {"$max": "$started_at"},
                }
            },
        ]
        try:
            docs = await traces_col.aggregate(pipeline).to_list(length=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Activity backfill aggregate for %s failed: %s", target_date, exc)
            return False

        if not docs:
            return True

        ops: list[Any] = []
        for doc in docs:
            user_id = doc["_id"]
            if not user_id:
                continue
            from pymongo import UpdateOne

            ops.append(
                UpdateOne(
                    {"user_id": str(user_id), "date": target_date},
                    {
                        "$setOnInsert": {
                            "user_id": str(user_id),
                            "date": target_date,
                            "first_at": doc.get("first_at", now_utc),
                        },
                        "$addToSet": {"sources": "message"},
                        "$set": {"last_at": doc.get("last_at", now_utc)},
                    },
                    upsert=True,
                )
            )

        if ops:
            try:
                await activity_col.bulk_write(ops, ordered=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Activity backfill bulk_write for %s failed: %s", target_date, exc)
                return False
        return True

    @staticmethod
    async def _backfill_snapshot_for_day(
        snapshot_col: Any,
        traces_col: Any,
        sessions_col: Any,
        target_date: str,
        date_start: datetime,
        date_end: datetime,
        now_utc: datetime,
    ) -> bool:
        """Derive analytics_daily_snapshot docs for one day.

        Mirrors ``snapshot._freeze_dates`` and independently aggregates
        ``sessions.created_at`` for new sessions.
        """
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "started_at": {"$gte": date_start, "$lt": date_end},
                    "events.event_type": {"$in": ["user:message", "token:usage"]},
                }
            },
            {
                "$lookup": {
                    "from": "sessions",
                    "localField": "session_id",
                    "foreignField": "session_id",
                    "as": "_session",
                }
            },
            {
                "$addFields": {
                    "persona_preset_id": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$_session.metadata.persona_preset_id", 0]},
                            "$metadata.persona_preset_id",
                        ]
                    },
                }
            },
            {
                "$group": {
                    "_id": {
                        "user_id": "$user_id",
                        "persona_preset_id": "$persona_preset_id",
                        "agent_id": "$agent_id",
                    },
                    "user_messages": {
                        "$sum": {
                            "$size": {
                                "$filter": {
                                    "input": "$events",
                                    "as": "ev",
                                    "cond": {"$eq": ["$$ev.event_type", "user:message"]},
                                }
                            }
                        }
                    },
                    "tokens": {
                        "$sum": {
                            "$sum": {
                                "$map": {
                                    "input": {
                                        "$filter": {
                                            "input": "$events",
                                            "as": "ev",
                                            "cond": {"$eq": ["$$ev.event_type", "token:usage"]},
                                        }
                                    },
                                    "as": "ev",
                                    "in": {"$ifNull": ["$$ev.data.total_tokens", 0]},
                                }
                            }
                        }
                    },
                    "active_session_ids": {
                        "$addToSet": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {
                                            "$size": {
                                                "$filter": {
                                                    "input": "$events",
                                                    "as": "ev",
                                                    "cond": {"$eq": ["$$ev.event_type", "user:message"]},
                                                }
                                            }
                                        },
                                        0,
                                    ]
                                },
                                "$session_id",
                                None,
                            ]
                        }
                    },
                    "last_active_at": {"$max": "$started_at"},
                }
            },
            {"$match": {"_id.user_id": {"$nin": [None, ""]}}},
        ]

        try:
            docs = await traces_col.aggregate(pipeline).to_list(length=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Snapshot backfill aggregate for %s failed: %s", target_date, exc)
            return False

        try:
            session_docs = []
            if sessions_col is not None:
                session_docs = await sessions_col.aggregate(
                    [
                        {"$match": {"created_at": {"$gte": date_start, "$lt": date_end}}},
                        {
                            "$group": {
                                "_id": {
                                    "user_id": "$user_id",
                                    "persona_preset_id": "$metadata.persona_preset_id",
                                    "agent_id": "$agent_id",
                                },
                                "new_sessions": {"$sum": 1},
                                "new_session_ids": {
                                    "$addToSet": {
                                        "$ifNull": ["$session_id", {"$toString": "$_id"}]
                                    }
                                },
                            }
                        },
                    ]
                ).to_list(length=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Session backfill aggregate for %s failed: %s", target_date, exc)
            return False

        if not docs and not session_docs:
            return True

        session_map: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
        for session_doc in session_docs:
            sid = session_doc.get("_id") or {}
            key = (
                str(sid.get("user_id")) if sid.get("user_id") is not None else None,
                str(sid.get("persona_preset_id")) if sid.get("persona_preset_id") is not None else None,
                str(sid.get("agent_id")) if sid.get("agent_id") is not None else None,
            )
            session_ids = sorted({str(value) for value in session_doc.get("new_session_ids", []) if value})
            session_map[key] = {"new_sessions": len(session_ids), "new_session_ids": session_ids}

        from pymongo import UpdateOne

        ops: list[Any] = []
        trace_keys: set[tuple[str | None, str | None, str | None]] = set()
        for doc in docs:
            eid = doc["_id"]
            trace_key = (
                str(eid.get("user_id")) if eid.get("user_id") is not None else None,
                str(eid.get("persona_preset_id")) if eid.get("persona_preset_id") is not None else None,
                str(eid.get("agent_id")) if eid.get("agent_id") is not None else None,
            )
            trace_keys.add(trace_key)
            active_sess = len({s for s in (doc.get("active_session_ids") or []) if s})

            doc_to_upsert = {
                "date": target_date,
                "user_id": trace_key[0],
                "persona_preset_id": trace_key[1],
                "agent_id": trace_key[2],
                "new_sessions": session_map.get(
                    (
                        trace_key[0],
                        trace_key[1],
                        trace_key[2],
                    ),
                    {},
                ).get("new_sessions", 0),
                "new_session_ids": session_map.get(
                    (
                        trace_key[0],
                        trace_key[1],
                        trace_key[2],
                    ),
                    {},
                ).get("new_session_ids", []),
                "active_session_ids": [str(value) for value in (doc.get("active_session_ids") or []) if value],
                "active_sessions": active_sess,
                "user_messages": int(doc.get("user_messages", 0) or 0),
                "tokens": int(doc.get("tokens", 0) or 0),
                "last_active_at": doc.get("last_active_at"),
                "frozen_at": now_utc,
            }
            ops.append(
                UpdateOne(
                    {
                        "date": target_date,
                        "user_id": doc_to_upsert["user_id"],
                        "persona_preset_id": doc_to_upsert["persona_preset_id"],
                        "agent_id": doc_to_upsert["agent_id"],
                    },
                    {"$setOnInsert": doc_to_upsert},
                    upsert=True,
                )
            )

        for key, session_data in session_map.items():
            if key in trace_keys:
                continue
            user_id, persona_id, agent_id = key
            ops.append(
                UpdateOne(
                    {
                        "date": target_date,
                        "user_id": user_id,
                        "persona_preset_id": persona_id,
                        "agent_id": agent_id,
                    },
                    {
                        "$setOnInsert": {
                            "date": target_date,
                            "user_id": user_id,
                            "persona_preset_id": persona_id,
                            "agent_id": agent_id,
                            "new_sessions": session_data["new_sessions"],
                            "new_session_ids": session_data["new_session_ids"],
                            "active_sessions": 0,
                            "active_session_ids": [],
                            "user_messages": 0,
                            "tokens": 0,
                            "last_active_at": None,
                            "frozen_at": now_utc,
                        }
                    },
                    upsert=True,
                )
            )

        if ops:
            try:
                await snapshot_col.bulk_write(ops, ordered=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Snapshot backfill bulk_write for %s failed: %s", target_date, exc)
                return False
        return True

    # ── lock management (mirrors session backfill) ──────────────────

    async def _acquire_lock(self) -> bool:
        redis_client = self._get_redis()
        try:
            self._lock_value = self._instance_id
            acquired = await redis_client.set(
                BACKFILL_LOCK_KEY,
                self._lock_value,
                nx=True,
                ex=self._lock_ttl_seconds,
            )
            return bool(acquired)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to acquire analytics backfill lock: %s", exc)
            return False

    def _start_lock_renewal(self) -> None:
        if self._renew_interval_seconds <= 0:
            return
        if self._renew_task is None or self._renew_task.done():
            self._renew_task = asyncio.create_task(self._renew_lock_loop())

    async def _stop_lock_renewal(self) -> None:
        renew_task = self._renew_task
        self._renew_task = None
        if renew_task is None:
            return
        renew_task.cancel()
        try:
            await renew_task
        except asyncio.CancelledError:
            pass

    async def _renew_lock_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._renew_interval_seconds)
                await self._renew_lock()
        except asyncio.CancelledError:
            return

    async def _renew_lock(self) -> None:
        redis_client = self._redis
        lock_value = self._lock_value
        if redis_client is None or not lock_value:
            return
        try:
            renewed = await redis_client.eval(
                _RENEW_LOCK_LUA,
                1,
                BACKFILL_LOCK_KEY,
                lock_value,
                self._lock_ttl_seconds,
            )  # type: ignore[misc]
            if not renewed:
                logger.warning("Analytics backfill lock was lost before renewal")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to renew analytics backfill lock: %s", exc)

    async def _release_lock(self) -> None:
        redis_client = self._redis
        lock_value = self._lock_value
        self._lock_value = None
        if redis_client is None or not lock_value:
            return
        try:
            await redis_client.eval(
                _RELEASE_LOCK_LUA, 1, BACKFILL_LOCK_KEY, lock_value
            )  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to release analytics backfill lock: %s", exc)

    def _get_redis(self) -> Any:
        if self._redis is None:
            self._redis = create_redis_client(isolated_pool=True)
        return self._redis
