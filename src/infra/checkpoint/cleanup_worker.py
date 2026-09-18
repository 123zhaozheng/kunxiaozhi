"""不活跃会话 checkpoint 的周期清理 worker。

按会话（LangGraph thread）整体删除，而不是让 MongoDB 按单文档 TTL 过期：
``deepagents`` 的 messages 通道是 DeltaChannel，状态依赖祖先链回放重建，
单文档过期会打断这条链，留下半损坏状态；整 thread 删除保证"要么全在、
要么全无"，此时 LangGraph 退化为空 checkpoint，会话可继续对话。

历史消息与统计都存放在 traces / sessions / 快照集合中，不受本清理影响。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any, Callable

from src.infra.logging import get_logger
from src.infra.storage.checkpoint import (
    delete_checkpoints_for_thread,
    is_checkpoint_backend_enabled,
)
from src.infra.storage.redis import create_redis_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

logger = get_logger(__name__)

CHECKPOINT_CLEANUP_LOCK_KEY = "checkpoint:cleanup:lock"
CHECKPOINT_CLEANUP_LOCK_TTL_SECONDS = 300
CHECKPOINT_CLEANUP_MIN_RETENTION_DAYS = 7
CHECKPOINT_CLEANUP_BATCH_MAX = 1000

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


class CheckpointCleanupWorker:
    """删除不活跃会话的 LangGraph checkpoint，不影响历史与统计。"""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        session_storage: Any | None = None,
        approval_storage: Any | None = None,
        retention_days: int | None = None,
        batch_limit: int | None = None,
        interval_seconds: float | None = None,
        now_factory: Callable[[], Any] | None = None,
        delete_thread: Callable[[str], Any] | None = None,
    ) -> None:
        self._redis = redis_client
        self._session_storage = session_storage
        self._approval_storage = approval_storage
        self._retention_days = retention_days
        self._batch_limit = batch_limit
        self._interval_seconds = interval_seconds
        self._now_factory = now_factory or utc_now
        self._delete_thread = delete_thread or delete_checkpoints_for_thread
        self._lock_value: str | None = None
        self._lock_lost = False
        self._renew_task: asyncio.Task[None] | None = None

    @property
    def retention_days(self) -> int:
        configured = (
            self._retention_days
            if self._retention_days is not None
            else getattr(settings, "CHECKPOINT_CLEANUP_RETENTION_DAYS", 30)
        )
        return max(int(configured or 0), CHECKPOINT_CLEANUP_MIN_RETENTION_DAYS)

    @property
    def batch_limit(self) -> int:
        configured = (
            self._batch_limit
            if self._batch_limit is not None
            else getattr(settings, "CHECKPOINT_CLEANUP_BATCH_LIMIT", 200)
        )
        return min(max(int(configured or 0), 1), CHECKPOINT_CLEANUP_BATCH_MAX)

    @property
    def interval_seconds(self) -> float:
        if self._interval_seconds is not None:
            return float(self._interval_seconds)
        hours = getattr(settings, "CHECKPOINT_CLEANUP_INTERVAL_HOURS", 24)
        return max(float(hours or 0), 1.0) * 3600.0

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "CHECKPOINT_CLEANUP_ENABLED", False))

    async def run_once(self) -> int:
        """Delete one bounded batch, returning the number of cleaned sessions."""
        if not self.enabled or not is_checkpoint_backend_enabled():
            return 0

        redis_client: Any | None = None
        lock_value = str(uuid.uuid4())
        self._lock_value = lock_value
        cleaned = 0
        try:
            redis_client = self._get_redis()
            acquired = await redis_client.set(
                CHECKPOINT_CLEANUP_LOCK_KEY,
                lock_value,
                nx=True,
                ex=CHECKPOINT_CLEANUP_LOCK_TTL_SECONDS,
            )
            if not acquired:
                logger.debug("[CheckpointCleanup] Lock is held by another instance")
                return 0
            self._lock_lost = False
            self._renew_task = asyncio.create_task(self._renew_lock_loop())

            cutoff = self._now_factory() - timedelta(days=self.retention_days)
            storage = self._get_session_storage()
            session_ids = await storage.list_inactive_session_ids(
                cutoff=cutoff,
                limit=self.batch_limit,
            )
            for session_id in session_ids:
                if self._lock_lost:
                    logger.warning("[CheckpointCleanup] Aborted: lock lost mid-batch")
                    break
                if await self._has_pending_approval(session_id):
                    # A blocked tool call is still awaiting a human decision.
                    continue
                try:
                    await self._delete_thread(session_id)
                    cleaned += 1
                except Exception as exc:
                    logger.warning(
                        "[CheckpointCleanup] Failed to delete checkpoints for %s: %s",
                        session_id,
                        exc,
                    )
            if cleaned:
                logger.info(
                    "[CheckpointCleanup] Cleaned %d session(s) inactive since %s",
                    cleaned,
                    cutoff.isoformat(),
                )
            return cleaned
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # worker failures must never affect app readiness
            logger.warning("[CheckpointCleanup] Cleanup run failed: %s", exc)
            return cleaned
        finally:
            self._lock_value = None
            if redis_client is not None:
                try:
                    await self._stop_lock_renewal()
                except Exception as exc:
                    logger.warning("[CheckpointCleanup] Renewal stop failed: %s", exc)
                finally:
                    try:
                        await redis_client.eval(
                            _RELEASE_LOCK_LUA,
                            1,
                            CHECKPOINT_CLEANUP_LOCK_KEY,
                            lock_value,
                        )
                    except Exception as exc:
                        logger.warning("[CheckpointCleanup] Failed to release lock: %s", exc)

    async def _has_pending_approval(self, session_id: str) -> bool:
        try:
            storage = self._get_approval_storage()
            pending = await storage.list_pending(session_id=session_id, limit=1)
            return bool(pending)
        except Exception as exc:
            # Fail closed: skip this session rather than risk deleting live state.
            logger.warning(
                "[CheckpointCleanup] Pending-approval check failed for %s: %s",
                session_id,
                exc,
            )
            return True

    async def _renew_lock_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(CHECKPOINT_CLEANUP_LOCK_TTL_SECONDS / 3)
                redis_client = self._redis
                if redis_client is None or self._lock_value is None:
                    return
                renewed = await redis_client.eval(
                    _RENEW_LOCK_LUA,
                    1,
                    CHECKPOINT_CLEANUP_LOCK_KEY,
                    self._lock_value,
                    CHECKPOINT_CLEANUP_LOCK_TTL_SECONDS,
                )
                if not renewed:
                    self._lock_lost = True
                    logger.warning("[CheckpointCleanup] Lock was lost")
                    return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._lock_lost = True
            logger.warning("[CheckpointCleanup] Lock renewal failed: %s", exc)

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
        """Run immediately and then at the configured maintenance interval."""
        while True:
            await self.run_once()
            await asyncio.sleep(self.interval_seconds)

    async def close(self) -> None:
        """Close an internally-created Redis client."""
        redis_client = self._redis
        self._redis = None
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception as exc:
                logger.debug("[CheckpointCleanup] Failed to close Redis client: %s", exc)

    def _get_redis(self) -> Any:
        if self._redis is None:
            self._redis = create_redis_client(isolated_pool=True)
        return self._redis

    def _get_session_storage(self) -> Any:
        if self._session_storage is None:
            from src.infra.session.storage import SessionStorage

            self._session_storage = SessionStorage()
        return self._session_storage

    def _get_approval_storage(self) -> Any:
        if self._approval_storage is None:
            from src.infra.storage.mongodb import ApprovalStorage

            self._approval_storage = ApprovalStorage()
        return self._approval_storage
