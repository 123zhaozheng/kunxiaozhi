"""AnalyticsStorage 点赞率：rating 必须用 up/down，禁止 like。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.infra.analytics.storage import AnalyticsStorage, _compute_up_vote_rate


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def test_compute_up_vote_rate_basic():
    assert _compute_up_vote_rate(3, 1) == 75.0
    assert _compute_up_vote_rate(1, 1) == 50.0
    assert _compute_up_vote_rate(0, 0) == 0.0
    assert _compute_up_vote_rate(2, 0) == 100.0
    assert _compute_up_vote_rate(0, 5) == 0.0


@pytest.mark.asyncio
async def test_get_overview_up_vote_rate_uses_up_down_not_like():
    """有 up/down 反馈时点赞率 > 0，且聚合匹配 rating ∈ {up, down}。"""
    storage = AnalyticsStorage()

    feedback_result = [{"_id": None, "up": 3, "down": 1}]

    feedback_collection = MagicMock()
    feedback_pipeline_holder: list[list[dict[str, Any]]] = []

    def feedback_aggregate(pipeline, *args, **kwargs):
        feedback_pipeline_holder.append(pipeline)
        return _FakeCursor(feedback_result)

    feedback_collection.aggregate = feedback_aggregate

    users_collection = MagicMock()
    users_collection.aggregate = MagicMock(return_value=_FakeCursor([{"value": 0}]))
    sessions_collection = MagicMock()
    sessions_collection.aggregate = MagicMock(return_value=_FakeCursor([{"value": 0}]))
    traces_collection = MagicMock()
    traces_collection.aggregate = MagicMock(return_value=_FakeCursor([{"value": 0}]))

    storage._users = users_collection
    storage._sessions = sessions_collection
    storage._traces = traces_collection
    storage._feedback = feedback_collection

    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 17, tzinfo=timezone.utc)

    # Patch _fan_out to still exercise real aggregation path via our collections,
    # but avoid real Mongo. We re-implement a thin fan_out over our mocks.
    async def fake_fan_out(jobs):
        results = []
        for collection, pipeline in jobs:
            cursor = collection.aggregate(pipeline)
            docs: list[dict[str, Any]] = []
            async for doc in cursor:
                docs.append(doc)
            results.append(docs)
        return results

    with patch.object(storage, "_fan_out", side_effect=fake_fan_out):
        overview = await storage.get_overview(start, end)

    assert overview.up_vote_rate == 75.0
    assert overview.up_vote_rate > 0

    assert feedback_pipeline_holder, "feedback aggregate must be called"
    pipeline = feedback_pipeline_holder[0]
    match_stage = next(stage["$match"] for stage in pipeline if "$match" in stage)
    # 禁止 like；必须显式匹配 up/down
    assert match_stage.get("rating") == {"$in": ["up", "down"]}
    assert "like" not in str(pipeline)

    group_stage = next(stage["$group"] for stage in pipeline if "$group" in stage)
    assert group_stage["up"] == {
        "$sum": {"$cond": [{"$eq": ["$rating", "up"]}, 1, 0]}
    }
    assert group_stage["down"] == {
        "$sum": {"$cond": [{"$eq": ["$rating", "down"]}, 1, 0]}
    }


@pytest.mark.asyncio
async def test_get_overview_up_vote_rate_zero_without_feedback():
    storage = AnalyticsStorage()

    async def fake_fan_out(jobs):
        # 返回空结果：无反馈
        return [[] for _ in jobs]

    with patch.object(storage, "_fan_out", side_effect=fake_fan_out):
        overview = await storage.get_overview(
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

    assert overview.up_vote_rate == 0.0


@pytest.mark.asyncio
async def test_get_feedback_summary_percentage_uses_up_down():
    storage = AnalyticsStorage()
    feedback_collection = MagicMock()
    feedback_collection.aggregate = MagicMock(
        return_value=_FakeCursor(
            [{"_id": None, "total": 4, "up": 3, "down": 1, "reasons": [None, None, None, "hallucination"]}]
        )
    )
    storage._feedback = feedback_collection

    summary = await storage.get_feedback_summary(
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert summary.up_count == 3
    assert summary.down_count == 1
    assert summary.up_percentage == 75.0
    # 确保聚合条件没有 like
    pipeline = feedback_collection.aggregate.call_args[0][0]
    assert "like" not in str(pipeline)
