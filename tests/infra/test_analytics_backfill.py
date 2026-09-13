"""Tests for AnalyticsBackfillWorker.

Covers Acceptance Criteria from prd.md R4 / implement.md S4:
- Idempotent (running twice produces identical state).
- Returns 0 when lock is not acquired.
- Exceptions never propagate (worker swallows and warns).
- Dates earlier than the earliest trace return zero / no-op.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.analytics.backfill import AnalyticsBackfillWorker

# ── Fake Redis ────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self, *, acquire: bool = True) -> None:
        self.acquire = acquire
        self.set_calls: list[tuple[tuple, dict]] = []
        self.eval_calls: list[tuple[tuple, dict]] = []
        self.closed = False

    async def set(self, *args: Any, **kwargs: Any) -> bool:
        self.set_calls.append((args, kwargs))
        return self.acquire

    async def eval(self, *args: Any, **kwargs: Any) -> int:
        self.eval_calls.append((args, kwargs))
        return 1

    async def aclose(self) -> None:
        self.closed = True


# ── Fake MongoDB collections ──────────────────────────────────────────

CST = timezone(timedelta(hours=8))


class _FakeCursor:
    """Async iterable cursor returned by aggregate()."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs)[:length]


class _FakeCollection:
    """Minimal async collection mock supporting find_one / aggregate / bulk_write / update_one."""

    def __init__(
        self,
        *,
        find_one_result: dict | None = None,
        aggregate_results: list[list[dict]] | None = None,
    ) -> None:
        self._find_one_result = find_one_result
        self._aggregate_queue: list[list[dict]] = list(aggregate_results or [])
        self.bulk_write_calls: list[Any] = []
        self.update_one_calls: list[Any] = []

    async def find_one(self, *args: Any, **kwargs: Any) -> dict | None:
        return self._find_one_result

    def aggregate(self, pipeline: list[dict], **kwargs: Any) -> _FakeCursor:
        if self._aggregate_queue:
            docs = self._aggregate_queue.pop(0)
        else:
            docs = []
        return _FakeCursor(docs)

    async def bulk_write(self, ops: list[Any], **kwargs: Any) -> Any:
        self.bulk_write_calls.append(ops)
        return MagicMock()

    async def update_one(self, *args: Any, **kwargs: Any) -> Any:
        self.update_one_calls.append(args)
        return MagicMock()


def _make_db(
    *,
    earliest_trace: datetime | None = None,
    activity_agg: list[list[dict]] | None = None,
    snapshot_agg: list[list[dict]] | None = None,
    state_doc: dict | None = None,
) -> dict[str, _FakeCollection]:
    """Build a fake DB dict with traces / user_daily_activity / snapshot / state collections."""
    trace_find_one: dict | None = None
    if earliest_trace is not None:
        trace_find_one = {"started_at": earliest_trace}

    # The backfill worker calls aggregate on traces twice per day (activity + snapshot),
    # so we need to interleave results appropriately.
    combined_agg: list[list[dict]] = []
    if activity_agg and snapshot_agg:
        for a, s in zip(activity_agg, snapshot_agg):
            combined_agg.append(a)
            combined_agg.append(s)
    elif activity_agg:
        combined_agg = activity_agg
    elif snapshot_agg:
        combined_agg = snapshot_agg

    traces_col = _FakeCollection(find_one_result=trace_find_one, aggregate_results=combined_agg)
    activity_col = _FakeCollection()
    snapshot_col = _FakeCollection()
    state_col = _FakeCollection(find_one_result=state_doc)

    return {
        "traces": traces_col,
        "user_daily_activity": activity_col,
        "analytics_daily_snapshot": snapshot_col,
        "analytics_backfill_state": state_col,
    }


# ── Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_once_returns_zero_when_lock_not_acquired() -> None:
    redis = _FakeRedis(acquire=False)
    worker = AnalyticsBackfillWorker(redis_client=redis)

    result = await worker.run_once()

    assert result == 0
    await worker.close()


@pytest.mark.asyncio
async def test_run_once_returns_zero_when_no_traces() -> None:
    """Earliest-trace query returns nothing → worker does 0 work."""
    redis = _FakeRedis(acquire=True)
    db = _make_db(earliest_trace=None)

    with patch("src.infra.analytics.backfill.get_mongo_client") as mock_gmc:
        mock_gmc.return_value.__getitem__ = lambda self_, key: db
        worker = AnalyticsBackfillWorker(redis_client=redis, batch_days=7)
        result = await worker.run_once()

    assert result == 0
    await worker.close()


@pytest.mark.asyncio
async def test_run_once_processes_batch_and_updates_cursor() -> None:
    """With traces present, one batch runs and writes cursor progress."""
    earliest = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)  # CST 2026-08-20 11:00
    activity_agg = [
        [{"_id": "u1", "first_at": earliest, "last_at": earliest}],
    ]
    snapshot_agg = [
        [
            {
                "_id": {"user_id": "u1", "persona_preset_id": None, "agent_id": "a1"},
                "user_messages": 5,
                "tokens": 100,
                "active_session_ids": ["s1"],
                "last_active_at": earliest,
            }
        ],
    ]
    db = _make_db(
        earliest_trace=earliest,
        activity_agg=activity_agg,
        snapshot_agg=snapshot_agg,
    )
    redis = _FakeRedis(acquire=True)

    # Pretend today is far in the future so 2026-08-20 is processable
    fake_today = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    with (
        patch("src.infra.analytics.backfill.get_mongo_client") as mock_gmc,
        patch("src.infra.analytics.backfill.datetime") as mock_dt,
    ):
        mock_gmc.return_value.__getitem__ = lambda self_, key: db
        # Make datetime.now(timezone.utc) return our fake today
        mock_dt.now.return_value = fake_today
        mock_dt.strptime = datetime.strptime
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        worker = AnalyticsBackfillWorker(redis_client=redis, batch_days=1)
        result = await worker.run_once()

    assert result == 1
    # Activity & snapshot bulk_write each called once
    assert len(db["user_daily_activity"].bulk_write_calls) == 1
    assert len(db["analytics_daily_snapshot"].bulk_write_calls) == 1
    # Cursor persisted
    assert len(db["analytics_backfill_state"].update_one_calls) >= 1
    await worker.close()


@pytest.mark.asyncio
async def test_idempotent_second_run_is_noop_when_cursor_catches_up() -> None:
    """Running again with cursor at yesterday yields 0 (nothing left)."""
    earliest = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    db = _make_db(
        earliest_trace=earliest,
        state_doc={"_id": "analytics_daily", "cursor_date": "2026-08-31"},
    )
    redis = _FakeRedis(acquire=True)

    fake_today = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    with (
        patch("src.infra.analytics.backfill.get_mongo_client") as mock_gmc,
        patch("src.infra.analytics.backfill.datetime") as mock_dt,
    ):
        mock_gmc.return_value.__getitem__ = lambda self_, key: db
        mock_dt.now.return_value = fake_today
        mock_dt.strptime = datetime.strptime
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        worker = AnalyticsBackfillWorker(redis_client=redis, batch_days=7)
        result = await worker.run_once()

    assert result == 0
    await worker.close()


@pytest.mark.asyncio
async def test_exception_in_batch_does_not_propagate() -> None:
    """If processing raises, run_once catches it and returns 0."""
    redis = _FakeRedis(acquire=True)

    with patch.object(
        AnalyticsBackfillWorker,
        "_process_batch",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        worker = AnalyticsBackfillWorker(redis_client=redis)
        result = await worker.run_once()

    assert result == 0
    await worker.close()


@pytest.mark.asyncio
async def test_close_releases_redis() -> None:
    redis = _FakeRedis()
    worker = AnalyticsBackfillWorker(redis_client=redis)
    await worker.close()
    assert redis.closed is True


@pytest.mark.asyncio
async def test_run_until_complete_stops_on_zero() -> None:
    """run_until_complete stops when run_once returns 0."""
    redis = _FakeRedis(acquire=False)
    worker = AnalyticsBackfillWorker(redis_client=redis, batch_delay_seconds=0)

    total = await worker.run_until_complete()

    assert total == 0
    await worker.close()


@pytest.mark.asyncio
async def test_failed_day_does_not_advance_cursor_and_is_recorded() -> None:
    """activity/snapshot 任一步失败都必须保留失败日期供下批重试。"""
    earliest = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    db = _make_db(earliest_trace=earliest, state_doc={"_id": "analytics_daily"})
    redis = _FakeRedis(acquire=True)
    fake_today = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    with (
        patch("src.infra.analytics.backfill.get_mongo_client") as mock_gmc,
        patch("src.infra.analytics.backfill.datetime") as mock_dt,
        patch.object(AnalyticsBackfillWorker, "_backfill_activity_for_day", new=AsyncMock(return_value=False)),
        patch.object(AnalyticsBackfillWorker, "_backfill_snapshot_for_day", new=AsyncMock(return_value=True)),
    ):
        mock_gmc.return_value.__getitem__ = lambda self_, key: db
        mock_dt.now.return_value = fake_today
        mock_dt.strptime = datetime.strptime
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        worker = AnalyticsBackfillWorker(redis_client=redis, batch_days=1)
        result = await worker.run_once()

    assert result == 0
    update = db["analytics_backfill_state"].update_one_calls[-1]
    state_update = update[1]["$set"]
    assert state_update["failed_dates"] == ["2026-08-20"]
    assert "cursor_date" not in state_update
    await worker.close()
