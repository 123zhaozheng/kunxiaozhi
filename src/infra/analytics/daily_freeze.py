"""每日跨日冻结统计快照的后台 worker。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.infra.analytics.date_range import CST
from src.infra.analytics.snapshot import read_or_freeze
from src.infra.analytics.usage_query import UsageFilters
from src.infra.logging import get_logger
from src.infra.storage.redis import create_redis_client

logger = get_logger(__name__)

DAILY_FREEZE_LOCK_KEY = "analytics:daily-freeze:lock"
DAILY_FREEZE_LOCK_TTL_SECONDS = 300
DAILY_FREEZE_INTERVAL_SECONDS = 3600.0

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
    """Freeze yesterday's UTC+8 snapshot without delaying application startup."""

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
        """Freeze yesterday, returning ``False`` on lock/error/no-op."""
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
            filters = UsageFilters(start=yesterday, end=today)
            await read_or_freeze(filters, storage=self._storage)
            logger.info("[Analytics] Daily snapshot freeze completed for %s", yesterday.strftime("%Y-%m-%d"))
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

    def _get_redis(self) -> Any:
        if self._redis is None:
            self._redis = create_redis_client(isolated_pool=True)
        return self._redis
