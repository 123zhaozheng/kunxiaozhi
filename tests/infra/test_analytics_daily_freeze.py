"""DailyFreezeWorker 无外部 Mongo/Redis 依赖的测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.infra.analytics.daily_freeze import ENRICH_DATES_PER_TICK, DailyFreezeWorker


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
async def test_daily_freeze_tick_freezes_yesterday_refreshes_today_and_enriches(monkeypatch) -> None:
    redis = _Redis()
    storage = object()
    ensure = AsyncMock(return_value=set())
    refresh = AsyncMock(return_value=3)
    enrich = AsyncMock(return_value=0)
    monkeypatch.setattr("src.infra.analytics.daily_freeze.ensure_dates_frozen", ensure)
    monkeypatch.setattr("src.infra.analytics.daily_freeze.refresh_today_snapshot", refresh)
    monkeypatch.setattr("src.infra.analytics.daily_freeze.enrich_snapshot_extras", enrich)
    worker = DailyFreezeWorker(
        redis_client=redis,
        storage=storage,
        now_factory=lambda: datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc),
    )

    assert await worker.run_once() is True
    # 2026-09-13 16:30 UTC is 2026-09-14 00:30 UTC+8, so "yesterday" is 09-13.
    ensure.assert_awaited_once_with(storage, redis, ["2026-09-13"])
    refresh.assert_awaited_once_with(storage)
    enrich.assert_awaited_once_with(storage, redis, ENRICH_DATES_PER_TICK)
    assert redis.eval_calls


@pytest.mark.asyncio
async def test_daily_freeze_lock_contention_is_a_noop() -> None:
    redis = _Redis(acquired=False)
    worker = DailyFreezeWorker(redis_client=redis)

    assert await worker.run_once() is False
    assert len(redis.eval_calls) == 1
