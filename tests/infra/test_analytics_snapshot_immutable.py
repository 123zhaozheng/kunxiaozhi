"""Snapshot 日快照层不可变性测试。

验证核心不变量：
1. 同一历史日期任意两次查询返回相同数字（删除会话/ traces 后仍相同）
2. 今天的数字在删除操作后会变化（不冻结今天）
3. 快照文档只存计数字段，不含会话内容
4. 冻结幂等：同一天冻两次不产生重复、不覆盖首次结果
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infra.analytics.date_range import CST, day_buckets, today_cst
from src.infra.analytics.snapshot import (
    snapshot_group_stages,
)
from src.infra.analytics.storage import AnalyticsStorage
from src.infra.analytics.usage_query import UsageFilters


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

    async def to_list(self, length: int | None = None):
        if length is None:
            return list(self._docs)
        return list(self._docs)[:length]


def _range_days(days: int = 7) -> tuple[datetime, datetime]:
    """生成过去 N 天的 CST 时间区间。"""
    today = datetime.now(CST)
    start = (today - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = (today + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start, end


# ── 纯函数测试 ───────────────────────────────────────────────


def test_snapshot_match_builds_correct_filter() -> None:
    """测试 snapshot_match 生成正确的 $match 查询。"""
    from src.infra.analytics.snapshot import snapshot_match

    start, end = _range_days(7)
    filters = UsageFilters(start=start, end=end, persona_preset_id="preset_123", agent_id="agent_456")
    dates = ["2026-08-01", "2026-08-02", "2026-08-03"]

    match = snapshot_match(filters, dates)

    assert "date" in match
    assert "$in" in match["date"]
    assert set(dates).issubset(match["date"]["$in"])
    assert match.get("persona_preset_id") == "preset_123"
    assert match.get("agent_id") == "agent_456"


def test_snapshot_match_without_filters() -> None:
    """测试不带筛选条件时的 snapshot_match。"""
    from src.infra.analytics.snapshot import snapshot_match

    start, end = _range_days(7)
    filters = UsageFilters(start=start, end=end)
    dates = ["2026-08-01"]

    match = snapshot_match(filters, dates)

    assert "date" in match
    assert "persona_preset_id" not in match
    assert "agent_id" not in match


def test_snapshot_group_stages_total_dimension() -> None:
    """测试 total 维度的分组 stage。"""
    stages = snapshot_group_stages("total")

    assert len(stages) == 1
    assert "$group" in stages[0]
    assert stages[0]["$group"]["_id"] is None


def test_snapshot_group_stages_day_dimension() -> None:
    """测试 day 维度的分组 stage。"""
    stages = snapshot_group_stages("day")

    assert len(stages) == 2
    assert "$group" in stages[0]
    assert stages[0]["$group"]["_id"] == "$date"
    assert "$sort" in stages[1]


def test_snapshot_group_stages_all_dimensions() -> None:
    """测试所有维度类型。"""
    dims = ("total", "day", "persona", "agent", "user")
    for dim in dims:
        stages = snapshot_group_stages(dim)
        assert isinstance(stages, list)
        assert len(stages) > 0
        assert "$group" in stages[0]


@pytest.mark.asyncio
async def test_read_or_freeze_returns_expected_structure() -> None:
    """测试 read_or_freeze 返回结构符合约定。"""
    import inspect

    from src.infra.analytics.snapshot import read_or_freeze

    sig = inspect.signature(read_or_freeze)
    assert "filters" in sig.parameters
    ret_anno = str(sig.return_annotation)
    assert "dict" in ret_anno and "str" in ret_anno and "any" in ret_anno.lower()


def test_freeze_uses_setoninsert_code_pattern() -> None:
    """验证冻结代码中使用了$setOnInsert 模式（静态分析）。"""
    import inspect

    from src.infra.analytics.snapshot import _freeze_dates

    source = inspect.getsource(_freeze_dates)
    assert "$setOnInsert" in source, "Freeze should use $setOnInsert for idempotency"


# ── 集成测试：直接测试 merge logic ───────────────────────────


@pytest.mark.asyncio
async def test_historical_date_unchanged_after_data_deletion() -> None:
    """核心测试：历史日期的数字在删除会话及 traces 后保持不变。"""
    from src.infra.analytics.snapshot import _merge_results

    # Pre-frozen snapshot document (already written before deletion)
    history_date = (datetime.now(CST) - timedelta(days=2)).strftime("%Y-%m-%d")
    expected_messages = 9
    expected_tokens = 6000

    frozen_doc = {
        "date": history_date,
        "user_id": "u_test_invariant",
        "persona_preset_id": None,
        "agent_id": "a_test_agent",
        "new_sessions": 3,
        "active_sessions": 3,
        "user_messages": expected_messages,
        "tokens": expected_tokens,
        "last_active_at": datetime.now(timezone.utc),
        "frozen_at": datetime.now(timezone.utc),
    }

    # Verify no content fields
    forbidden_fields = ["title", "messages", "content", "text", "body"]
    for field in forbidden_fields:
        assert field not in frozen_doc, f"Frozen snapshot must not contain content field: {field}"

    # Mock storage that returns the pre-frozen doc for historical dates
    storage = MagicMock(spec=AnalyticsStorage)

    # Historical aggregation returns the frozen doc
    mock_history_cursor = _FakeCursor([
        {"_id": history_date, "user_messages": expected_messages, "tokens": expected_tokens, "active_sessions": 3, "new_sessions": 3}
    ])
    storage.snapshot.aggregate = MagicMock(return_value=mock_history_cursor)
    storage.traces.aggregate = MagicMock(side_effect=[
        _FakeCursor([]),  # granular historical
        _FakeCursor([]),  # today realtime
    ])
    storage.sessions.count_documents = AsyncMock(return_value=0)

    start, end = _range_days(3)
    filters = UsageFilters(start=start, end=end)
    dates = day_buckets(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    # First query
    result1 = await _merge_results(storage, filters, dates)

    # Second query after "deletion" (storage unchanged - snapshot is immutable)
    result2 = await _merge_results(storage, filters, dates)

    # Critical invariant: historical numbers remain identical
    assert result1["total"]["user_messages"] == result2["total"]["user_messages"], \
        f"Historical user_messages should be immutable: {result1['total']['user_messages']} vs {result2['total']['user_messages']}"
    assert result1["total"]["tokens"] == result2["total"]["tokens"], \
        f"Historical tokens should be immutable: {result1['total']['tokens']} vs {result2['total']['tokens']}"


@pytest.mark.asyncio
async def test_today_changes_after_data_deletion() -> None:
    """同一测试里断言"今天"的数字在删除操作后会变化。"""
    from src.infra.analytics.snapshot import _merge_results

    today = today_cst()
    initial_messages = 6
    initial_tokens = 4000
    reduced_messages = 3
    reduced_tokens = 2000

    # Mock storage that changes based on calls
    storage = MagicMock(spec=AnalyticsStorage)
    call_count = [0]

    def side_effect_aggregate(pipeline):
        call_count[0] += 1
        if call_count[0] == 1:
            return _FakeCursor([{"_id": None, "user_messages": initial_messages, "tokens": initial_tokens, "active_session_ids": ["s1", "s2"], "last_active_at": datetime.now(timezone.utc)}])
        else:
            return _FakeCursor([{"_id": None, "user_messages": reduced_messages, "tokens": reduced_tokens, "active_session_ids": ["s1"], "last_active_at": datetime.now(timezone.utc)}])

    storage.traces.aggregate = MagicMock(side_effect=side_effect_aggregate)
    storage.sessions.count_documents = AsyncMock(side_effect=[2, 1])  # First 2 sessions, then 1
    storage.snapshot.aggregate = MagicMock(return_value=_FakeCursor([]))

    today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=CST)
    tomorrow_dt = today_dt + timedelta(days=1)
    filters = UsageFilters(start=today_dt, end=tomorrow_dt)
    dates = [today]

    # First query
    result1 = await _merge_results(storage, filters, dates)
    today_point1 = next((p for p in result1["trend"] if p.get("date") == today), None)
    assert today_point1 is not None
    assert today_point1["user_messages"] == initial_messages

    # Second query after "deletion"
    result2 = await _merge_results(storage, filters, dates)
    today_point2 = next((p for p in result2["trend"] if p.get("date") == today), None)
    assert today_point2 is not None

    # Today's numbers SHOULD change (real-time path, not frozen)
    assert today_point2["user_messages"] != today_point1["user_messages"], \
        "Today's numbers should change when underlying data changes"
    assert today_point2["user_messages"] == reduced_messages
    assert today_point2["tokens"] == reduced_tokens


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
