"""统计快照后台 worker：冻结昨天、刷新今日快照、补齐老快照的扩展字段。

看板请求只读这些预聚合结果（参照 new-api 的预聚合思路），不在请求里扫 traces。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.infra.analytics.date_range import CST
from src.infra.analytics.snapshot import (
    enrich_snapshot_extras,
    ensure_dates_frozen,
    refresh_today_snapshot,
)
from src.infra.logging import get_logger
from src.infra.storage.redis import create_redis_client

logger = get_logger(__name__)

DAILY_FREEZE_LOCK_KEY = "analytics:daily-freeze:lock"
DAILY_FREEZE_LOCK_TTL_SECONDS = 300
# Also the refresh cadence of today's snapshot, i.e. the dashboard's data delay.
DAILY_FREEZE_INTERVAL_SECONDS = 60.0
# Old dates enriched per tick, most recent first, so 7d/30d views fill quickly.
ENRICH_DATES_PER_TICK = 5

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


class DailyFreezeWorker:
    """Keep analytics snapshots current without delaying application startup."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        storage: Any | None = None,
        interval_seconds: float = DAILY_FREEZE_INTERVAL_SECONDS,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._redis = redis_client
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._lock_value: str | None = None
        self._lock_lost = False
        self._renew_task: asyncio.Task[None] | None = None

    async def run_once(self) -> bool:
        """Run one maintenance tick, returning ``False`` on lock/error."""
        redis_client: Any | None = None
        lock_value = str(uuid.uuid4())
        self._lock_value = lock_value
        try:
            redis_client = self._get_redis()
            acquired = await redis_client.set(
                DAILY_FREEZE_LOCK_KEY,
                lock_value,
                nx=True,
                ex=DAILY_FREEZE_LOCK_TTL_SECONDS,
            )
            if not acquired:
                logger.debug("[Analytics] Daily freeze lock is held by another instance")
                return False
            self._lock_lost = False
            self._renew_task = asyncio.create_task(self._renew_lock_loop())

            now_cst = self._now_factory().astimezone(CST)
            today = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday = today - timedelta(days=1)
            if self._lock_lost:
                logger.warning("[Analytics] Daily freeze aborted: lock lost before freezing")
                return False
            storage = self._get_storage()
            yesterday_str = yesterday.strftime("%Y-%m-%d")
            # Right after midnight yesterday is no longer "today"; freezing it
            # here keeps the first read of the new day off the freeze path.
            if await ensure_dates_frozen(storage, redis_client, [yesterday_str]):
                logger.warning("[Analytics] Daily snapshot freeze incomplete for %s", yesterday_str)
            if self._lock_lost:
                return False
            rows = await refresh_today_snapshot(storage)
            logger.debug("[Analytics] Today snapshot refreshed: %d rows", rows)
            if self._lock_lost:
                return False
            enriched = await enrich_snapshot_extras(storage, redis_client, ENRICH_DATES_PER_TICK)
            if enriched:
                logger.info("[Analytics] Snapshot extras filled for %d dates", enriched)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # worker failures must not affect app readiness
            logger.warning("[Analytics] Daily snapshot freeze failed: %s", exc)
            return False
        finally:
            self._lock_value = None
            if redis_client is not None:
                try:
                    await self._stop_lock_renewal()
                except Exception as exc:
                    logger.warning("[Analytics] Daily freeze renewal stop failed: %s", exc)
                finally:
                    try:
                        await redis_client.eval(
                            _RELEASE_LOCK_LUA,
                            1,
                            DAILY_FREEZE_LOCK_KEY,
                            lock_value,
                        )
                    except Exception as exc:
                        logger.warning("[Analytics] Failed to release daily freeze lock: %s", exc)

    async def _renew_lock_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(DAILY_FREEZE_LOCK_TTL_SECONDS / 3)
                redis_client = self._redis
                if redis_client is None or self._lock_value is None:
                    return
                renewed = await redis_client.eval(
                    _RENEW_LOCK_LUA,
                    1,
                    DAILY_FREEZE_LOCK_KEY,
                    self._lock_value,
                    DAILY_FREEZE_LOCK_TTL_SECONDS,
                )
                if not renewed:
                    self._lock_lost = True
                    logger.warning("[Analytics] Daily freeze lock was lost")
                    return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._lock_lost = True
            logger.warning("[Analytics] Daily freeze lock renewal failed: %s", exc)

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

    async def run_forever(self) -> None:
        """Run immediately and then at a bounded daily-maintenance interval."""
        while True:
            await self.run_once()
            await asyncio.sleep(self._interval_seconds)

    async def close(self) -> None:
        """Close an internally-created Redis client."""
        redis_client = self._redis
        self._redis = None
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception as exc:
                logger.debug("[Analytics] Failed to close daily freeze Redis client: %s", exc)

    def _get_storage(self) -> Any:
        if self._storage is None:
            from src.infra.analytics.storage import AnalyticsStorage

            self._storage = AnalyticsStorage()
        return self._storage

    def _get_redis(self) -> Any:
        if self._redis is None:
            self._redis = create_redis_client(isolated_pool=True)
        return self._redis
