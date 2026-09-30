"""把实时用量事件汇入 ``usage_hourly`` 预聚合表。

写路径参照 new-api 的 ``quota_data``：请求线程只在进程内内存桶累加，
后台按间隔批量 flush，避免给聊天主链路加同步写。与参照实现不同的是，
落库用 ``$inc`` + 唯一索引的原子累加，多副本并发不会产生重复逻辑行。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from src.infra.analytics.usage_hourly import UsageHourlyStorage, hour_bucket
from src.infra.logging import get_logger
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

logger = get_logger(__name__)

USAGE_HOURLY_MIN_FLUSH_SECONDS = 30.0
USAGE_HOURLY_MAX_FLUSH_SECONDS = 3600.0
# 上限存在的意义是：Mongo 长时间不可用时，内存桶不会无限增长拖垮进程。
USAGE_HOURLY_MAX_PENDING_KEYS = 50_000

_MetricKey = tuple[datetime, str | None, str | None, str | None, str | None]


def resolve_model_label(data: dict[str, Any] | None) -> str:
    """与 analytics 的 ``_model_label`` 保持一致的模型标签口径。"""
    payload = data or {}
    model_id = payload.get("model_id")
    if model_id not in (None, ""):
        return str(model_id)
    model = payload.get("model")
    if model not in (None, ""):
        return str(model)
    return "unknown"


class UsageHourlyRecorder:
    """进程内小时桶缓冲，后台批量原子落库。"""

    def __init__(self, storage: UsageHourlyStorage | None = None) -> None:
        self._storage = storage
        self._pending: dict[_MetricKey, dict[str, int]] = {}
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._dropped_keys = 0

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "ANALYTICS_USAGE_HOURLY_ENABLED", False))

    @property
    def flush_seconds(self) -> float:
        configured = getattr(settings, "ANALYTICS_USAGE_HOURLY_FLUSH_SECONDS", 300)
        return min(
            max(float(configured or 0), USAGE_HOURLY_MIN_FLUSH_SECONDS),
            USAGE_HOURLY_MAX_FLUSH_SECONDS,
        )

    @property
    def storage(self) -> UsageHourlyStorage:
        if self._storage is None:
            self._storage = UsageHourlyStorage()
        return self._storage

    @property
    def pending_keys(self) -> int:
        return len(self._pending)

    async def record_token_usage(
        self,
        *,
        data: dict[str, Any] | None,
        user_id: str | None,
        agent_id: str | None = None,
        persona_preset_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """累加一个 ``token:usage`` 事件，仅落在内存桶上。"""
        if not self.enabled:
            return
        payload = data or {}
        try:
            tokens = int(payload.get("total_tokens", 0) or 0)
        except (TypeError, ValueError):
            tokens = 0
        key: _MetricKey = (
            hour_bucket(occurred_at or utc_now()),
            user_id,
            resolve_model_label(payload),
            persona_preset_id,
            agent_id,
        )
        async with self._lock:
            if key not in self._pending and len(self._pending) >= USAGE_HOURLY_MAX_PENDING_KEYS:
                self._dropped_keys += 1
                return
            bucket = self._pending.setdefault(key, {"tokens": 0, "runs": 0})
            bucket["tokens"] += tokens
            bucket["runs"] += 1

    async def record_user_message(
        self,
        *,
        user_id: str | None,
        agent_id: str | None = None,
        persona_preset_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """累加一条用户消息；模型维度不适用故记 ``None``，与快照回填行一致，
        使按模型聚合的 ``$ne: None`` 过滤将其排除，不产生零值图例项。"""
        if not self.enabled:
            return
        key: _MetricKey = (
            hour_bucket(occurred_at or utc_now()),
            user_id,
            None,
            persona_preset_id,
            agent_id,
        )
        async with self._lock:
            if key not in self._pending and len(self._pending) >= USAGE_HOURLY_MAX_PENDING_KEYS:
                self._dropped_keys += 1
                return
            bucket = self._pending.setdefault(key, {"user_messages": 0})
            bucket["user_messages"] = bucket.get("user_messages", 0) + 1

    async def flush(self) -> int:
        """把内存桶原子累加进 Mongo，返回写出的桶数。"""
        async with self._lock:
            if not self._pending:
                return 0
            drained = self._pending
            self._pending = {}
            dropped = self._dropped_keys
            self._dropped_keys = 0

        rows = [
            {
                "bucket": key[0],
                "user_id": key[1],
                "model": key[2],
                "persona_preset_id": key[3],
                "agent_id": key[4],
                "source": "live",
                "tokens": metrics.get("tokens", 0),
                "user_messages": metrics.get("user_messages", 0),
                "runs": metrics.get("runs", 0),
            }
            for key, metrics in drained.items()
        ]
        try:
            await self.storage.accumulate_many(rows)
        except Exception as exc:
            # 失败的桶合并回内存，下轮重试；聊天链路不受影响。
            async with self._lock:
                for key, metrics in drained.items():
                    target = self._pending.setdefault(key, {})
                    for field, value in metrics.items():
                        target[field] = target.get(field, 0) + value
            logger.warning("[UsageHourly] flush failed, %d buckets requeued: %s", len(rows), exc)
            return 0
        if dropped:
            logger.warning("[UsageHourly] dropped %d buckets due to backpressure", dropped)
        return len(rows)

    async def run_forever(self) -> None:
        """后台按间隔 flush，直到被取消。"""
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.flush_seconds)
            except asyncio.TimeoutError:
                pass
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[UsageHourly] periodic flush failed: %s", exc)

    async def close(self) -> None:
        """停止循环并尽力把剩余桶落库。"""
        self._stopping.set()
        try:
            await self.flush()
        except Exception as exc:
            logger.warning("[UsageHourly] final flush failed: %s", exc)


_recorder: UsageHourlyRecorder | None = None


def get_usage_hourly_recorder() -> UsageHourlyRecorder:
    """进程内单例。"""
    global _recorder
    if _recorder is None:
        _recorder = UsageHourlyRecorder()
    return _recorder


def reset_usage_hourly_recorder() -> None:
    """测试钩子：丢弃单例。"""
    global _recorder
    _recorder = None
