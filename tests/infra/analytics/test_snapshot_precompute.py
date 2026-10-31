"""Pre-aggregated read path: dashboards read snapshots, never scan traces."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infra.analytics import snapshot as snapshot_module
from src.infra.analytics import storage as storage_module
from src.infra.analytics.date_range import CST, today_cst
from src.infra.analytics.snapshot import (
    _COMPLETE_MARKER_USER_ID,
    _merge_results,
    enrich_snapshot_extras,
    refresh_today_snapshot,
)
from src.infra.analytics.storage import AnalyticsStorage
from src.infra.analytics.usage_query import UsageFilters


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return list(self.docs)


def _today_filters() -> UsageFilters:
    start = datetime.strptime(today_cst(), "%Y-%m-%d").replace(tzinfo=CST)
    return UsageFilters(start=start, end=start + timedelta(days=1))


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "date": today_cst(),
        "user_id": "u1",
        "persona_preset_id": "p1",
        "agent_id": "fast",
        "new_sessions": 1,
        "new_session_ids": ["s1"],
        "active_sessions": 1,
        "active_session_ids": ["s1"],
        "user_messages": 4,
        "tokens": 100,
        "last_active_at": None,
        "hour_messages": [0] * 24,
        "hour_tokens": [0] * 24,
        "model_tokens": [{"model": "m1", "tokens": 100}],
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_concurrent_reads_share_one_snapshot_computation(monkeypatch) -> None:
    calls = 0
    gate = asyncio.Event()

    async def fake_read(filters, storage=None):
        nonlocal calls
        calls += 1
        await gate.wait()
        return {"total": {"user_messages": 1}}

    monkeypatch.setattr(storage_module, "read_or_freeze", fake_read)
    storage = AnalyticsStorage()
    filters = _today_filters()

    tasks = [asyncio.create_task(storage._read_usage(filters)) for _ in range(8)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1
    assert all(result is results[0] for result in results)
    # Served from cache afterwards.
    await storage._read_usage(filters)
    assert calls == 1


@pytest.mark.asyncio
async def test_incomplete_or_failed_reads_are_not_cached(monkeypatch) -> None:
    outcomes: list[Any] = [
        RuntimeError("boom"),
        {"snapshot_complete": False},
        {"snapshot_complete": True},
    ]

    async def fake_read(filters, storage=None):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(storage_module, "read_or_freeze", fake_read)
    storage = AnalyticsStorage()
    filters = _today_filters()

    with pytest.raises(RuntimeError):
        await storage._read_usage(filters)
    assert (await storage._read_usage(filters))["snapshot_complete"] is False
    assert (await storage._read_usage(filters))["snapshot_complete"] is True
    assert outcomes == []


@pytest.mark.asyncio
async def test_today_refresh_overwrites_rows_then_marker_then_prunes(monkeypatch) -> None:
    monkeypatch.setattr(
        snapshot_module, "compute_day_rows", AsyncMock(return_value=[_row()])
    )
    calls: list[str] = []
    collection = MagicMock()
    collection.bulk_write = AsyncMock(side_effect=lambda *a, **k: calls.append("rows"))
    collection.update_one = AsyncMock(side_effect=lambda *a, **k: calls.append("marker"))
    collection.delete_many = AsyncMock(side_effect=lambda *a, **k: calls.append("prune"))
    storage = MagicMock()
    storage.today_snapshot = collection

    assert await refresh_today_snapshot(storage) == 1

    assert calls == ["rows", "marker", "prune"]
    (operation,) = collection.bulk_write.call_args.args[0]
    assert "$set" in operation._doc and "$setOnInsert" not in operation._doc
    assert operation._doc["$set"]["refreshed_at"]
    marker_query = collection.update_one.call_args.args[0]
    assert marker_query == {"date": today_cst(), "user_id": _COMPLETE_MARKER_USER_ID}
    prune = collection.delete_many.call_args.args[0]
    assert {"date": {"$ne": today_cst()}} in prune["$or"]


@pytest.mark.asyncio
async def test_today_read_uses_today_snapshot_without_touching_traces(monkeypatch) -> None:
    refreshed_at = datetime(2026, 10, 31, 2, 0)
    storage = MagicMock()
    storage.traces.aggregate.side_effect = AssertionError("traces must not be scanned")
    storage.sessions.aggregate.side_effect = AssertionError("sessions must not be scanned")
    storage.sessions.count_documents.side_effect = AssertionError("sessions must not be scanned")
    storage.today_snapshot.find_one = AsyncMock(
        return_value={
            "date": today_cst(),
            "user_id": _COMPLETE_MARKER_USER_ID,
            "refreshed_at": refreshed_at,
        }
    )

    def aggregate(pipeline):
        group = next((stage["$group"] for stage in pipeline if "$group" in stage), {})
        if group.get("_id") == "$date" and "user_messages" in group:
            return _Cursor(
                [
                    {
                        "_id": today_cst(),
                        "user_messages": 4,
                        "tokens": 100,
                        "active_session_ids": [["s1"]],
                        "new_session_ids": [["s1"]],
                        "active_session_ids_present": [True],
                        "new_session_ids_present": [True],
                    }
                ]
            )
        if group.get("_id") == "$model_tokens.model":
            return _Cursor([{"_id": "m1", "tokens": 100}])
        return _Cursor([])

    storage.today_snapshot.aggregate.side_effect = aggregate
    monkeypatch.setattr(
        "src.infra.analytics.activity_storage.ActivityStorage.distinct_users",
        AsyncMock(return_value=["u1"]),
    )

    result = await _merge_results(storage, _today_filters(), [today_cst()])

    assert result["total"]["user_messages"] == 4
    assert result["total"]["tokens"] == 100
    assert result["by_model"] == [{"model": "m1", "tokens": 100}]
    assert result["refreshed_at"] == refreshed_at
    assert result["extras_complete"] is True
    assert len(result["by_hour"]) == 24
    storage.traces.aggregate.assert_not_called()
    storage.snapshot.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_today_falls_back_to_live_until_worker_writes_marker(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.infra.analytics.activity_storage.ActivityStorage.distinct_users",
        AsyncMock(return_value=[]),
    )
    storage = MagicMock()
    storage.today_snapshot.find_one = AsyncMock(return_value=None)
    storage.traces.aggregate.return_value = _Cursor([])
    storage.sessions.aggregate.return_value = _Cursor([])
    storage.sessions.count_documents = AsyncMock(return_value=0)

    result = await _merge_results(storage, _today_filters(), [today_cst()])

    storage.traces.aggregate.assert_called()
    assert result["refreshed_at"] is None
    assert result["extras_complete"] is False


@pytest.mark.asyncio
async def test_enrichment_only_sets_extras_on_existing_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        snapshot_module,
        "compute_day_rows",
        AsyncMock(return_value=[_row(date="2026-08-01")]),
    )

    class _Redis:
        async def set(self, *args, **kwargs):
            return True

        async def eval(self, *args, **kwargs):
            return 1

    storage = MagicMock()
    storage.snapshot.find.return_value = _Cursor([{"date": "2026-08-01"}])
    storage.snapshot.bulk_write = AsyncMock()
    storage.snapshot.update_one = AsyncMock()

    assert await enrich_snapshot_extras(storage, _Redis(), 5) == 1

    (operation,) = storage.snapshot.bulk_write.call_args.args[0]
    assert operation._upsert is False
    assert set(operation._doc) == {"$set"}
    assert set(operation._doc["$set"]) == {"hour_messages", "hour_tokens", "model_tokens"}
    marker_update = storage.snapshot.update_one.call_args.args[1]
    assert marker_update["$set"] == {"extras_version": snapshot_module.EXTRAS_VERSION}
