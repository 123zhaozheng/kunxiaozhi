"""DailyFreezeWorker 无外部 Mongo/Redis 依赖的测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.infra.analytics.daily_freeze import DailyFreezeWorker


class _Redis:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.set_calls: list[tuple] = []
        self.eval_calls: list[tuple] = []

    async def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))
        return self.acquired

    async def eval(self, *args, **kwargs):
        self.eval_calls.append((args, kwargs))
        return 1

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_daily_freeze_uses_previous_utc8_day_and_releases_lock(monkeypatch) -> None:
    redis = _Redis()
    freeze = AsyncMock()
    monkeypatch.setattr("src.infra.analytics.daily_freeze.read_or_freeze", freeze)
    worker = DailyFreezeWorker(
        redis_client=redis,
        now_factory=lambda: datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc),
    )

    assert await worker.run_once() is True
    filters = freeze.call_args.args[0]
    assert filters.start.strftime("%Y-%m-%d") == "2026-09-13"
    assert filters.end.strftime("%Y-%m-%d") == "2026-09-14"
    assert redis.eval_calls


@pytest.mark.asyncio
async def test_daily_freeze_lock_contention_is_a_noop() -> None:
    redis = _Redis(acquired=False)
    worker = DailyFreezeWorker(redis_client=redis)

    assert await worker.run_once() is False
    assert len(redis.eval_calls) == 1
