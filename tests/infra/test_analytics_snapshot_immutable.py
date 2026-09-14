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

from src.infra.analytics.date_range import CST, today_cst
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
    """真实走查询→冻结→删除底层数据→再次查询流程。"""
    from src.infra.analytics import snapshot as snapshot_module
    from src.infra.analytics.snapshot import read_or_freeze

    history_date = (datetime.now(CST) - timedelta(days=2)).strftime("%Y-%m-%d")
    frozen_docs: list[dict[str, Any]] = []

    class _SnapshotCollection:
        """Minimal fake that aggregates over really-stored rows.

        Returning canned values here would make the test pass even when the
        freeze path writes nothing, which is what the previous version did.
        """

        def find(self, query, projection=None):
            matched = [doc for doc in frozen_docs if doc["date"] in query["date"]["$in"]]
            user_filter = query.get("user_id")
            if isinstance(user_filter, str):
                matched = [doc for doc in matched if doc.get("user_id") == user_filter]
            elif isinstance(user_filter, dict) and "$ne" in user_filter:
                matched = [doc for doc in matched if doc.get("user_id") != user_filter["$ne"]]
            return _FakeCursor(matched)

        def _upsert(self, key: dict[str, Any], doc: dict[str, Any]) -> None:
            if not any(
                all(existing.get(field) == value for field, value in key.items())
                for existing in frozen_docs
            ):
                frozen_docs.append(dict(doc))

        async def bulk_write(self, operations, ordered=False):
            for operation in operations:
                update = operation["updateOne"]
                self._upsert(update["filter"], update["update"]["$setOnInsert"])

        async def update_one(self, query, update, upsert=False):
            self._upsert(query, update["$setOnInsert"])

        def aggregate(self, pipeline):
            match = pipeline[0]["$match"]
            rows = [doc for doc in frozen_docs if doc["date"] in match["date"]["$in"]]
            excluded = match.get("user_id", {}).get("$ne")
            rows = [doc for doc in rows if doc.get("user_id") != excluded]

            group = next(stage["$group"] for stage in reversed(pipeline) if "$group" in stage)
            group_id = group["_id"]
            if "user_ids" in group:
                return _FakeCursor(
                    [{"_id": None, "user_ids": sorted({doc["user_id"] for doc in rows})}]
                )

            def key_of(doc: dict[str, Any]) -> Any:
                if group_id is None:
                    return None
                if isinstance(group_id, dict):
                    return {
                        name: doc.get(expr.lstrip("$")) for name, expr in group_id.items()
                    }
                return doc.get(group_id.lstrip("$"))

            buckets: dict[Any, dict[str, Any]] = {}
            for doc in rows:
                key = key_of(doc)
                bucket = buckets.setdefault(
                    repr(key),
                    {
                        "_id": key,
                        "new_sessions": 0,
                        "active_sessions": 0,
                        "user_messages": 0,
                        "tokens": 0,
                        "new_session_ids": [],
                        "active_session_ids": [],
                    },
                )
                for field in ("new_sessions", "active_sessions", "user_messages", "tokens"):
                    bucket[field] += int(doc.get(field, 0) or 0)
                for field in ("new_session_ids", "active_session_ids"):
                    if field in doc:
                        bucket[field].append(doc[field])
            return _FakeCursor(list(buckets.values()))

    class _Redis:
        async def set(self, *args, **kwargs):
            return True

        async def eval(self, *args, **kwargs):
            return 1

        async def aclose(self):
            return None

    storage = MagicMock(spec=AnalyticsStorage)
    storage.snapshot = _SnapshotCollection()
    storage.traces = MagicMock()
    storage.traces.aggregate = MagicMock(
        return_value=_FakeCursor([
            {
                "_id": {"user_id": "u_test_invariant", "persona_preset_id": None, "agent_id": "a_test_agent"},
                "user_messages": 9,
                "tokens": 6000,
                "active_session_ids": ["s_active_1", "s_active_2", "s_active_3"],
                "last_active_at": datetime.now(timezone.utc),
            }
        ])
    )
    storage.sessions = MagicMock()
    storage.sessions.aggregate = MagicMock(return_value=_FakeCursor([]))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(snapshot_module, "create_redis_client", lambda **kwargs: _Redis())
    monkeypatch.setattr(
        "src.infra.analytics.activity_storage.ActivityStorage.distinct_users",
        AsyncMock(return_value=[]),
    )
    try:
        start = datetime.strptime(history_date, "%Y-%m-%d").replace(tzinfo=CST)
        end = start + timedelta(days=1)
        filters = UsageFilters(start=start, end=end)

        result1 = await read_or_freeze(filters, storage=storage)
        frozen_docs_before_delete = [dict(doc) for doc in frozen_docs]
        storage.traces.aggregate = MagicMock(return_value=_FakeCursor([]))
        result2 = await read_or_freeze(filters, storage=storage)
    finally:
        monkeypatch.undo()

    assert frozen_docs_before_delete
    assert frozen_docs == frozen_docs_before_delete
    # Guard against a vacuous pass: the frozen numbers must be real.
    assert result1["total"]["user_messages"] == 9
    assert result1["total"]["tokens"] == 6000
    assert result1["total"]["active_sessions"] == 3
    assert result1["total"] == result2["total"]


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


@pytest.mark.asyncio
async def test_cross_day_session_ids_are_unioned_and_legacy_rows_are_summed() -> None:
    """现代快照跨日按 ID 去重，旧快照缺字段则保留旧求和语义。"""
    from src.infra.analytics.snapshot import _merge_results

    d1 = (datetime.now(CST) - timedelta(days=4)).strftime("%Y-%m-%d")
    d2 = (datetime.now(CST) - timedelta(days=3)).strftime("%Y-%m-%d")
    storage = MagicMock(spec=AnalyticsStorage)
    storage.traces.aggregate = MagicMock(return_value=_FakeCursor([]))
    storage.sessions.aggregate = MagicMock(return_value=_FakeCursor([]))

    legacy_mode = [False]

    def aggregate(pipeline):
        group = next(stage["$group"] for stage in reversed(pipeline) if "$group" in stage)
        identifier = group["_id"]
        if identifier == "$date":
            if legacy_mode[0]:
                return _FakeCursor([
                    {"_id": d1, "active_sessions": 1, "new_sessions": 1},
                    {"_id": d2, "active_sessions": 1, "new_sessions": 1},
                ])
            return _FakeCursor([
                {"_id": d1, "active_sessions": 1, "new_sessions": 1, "active_session_ids": [["s1"]], "new_session_ids": [["n1"]]},
                {"_id": d2, "active_sessions": 1, "new_sessions": 1, "active_session_ids": [["s1"]], "new_session_ids": [["n1"]]},
            ])
        return _FakeCursor([])

    storage.snapshot.aggregate = MagicMock(side_effect=aggregate)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("src.infra.analytics.activity_storage.ActivityStorage.distinct_users", AsyncMock(return_value=[]))
    try:
        filters = UsageFilters(
            start=datetime.strptime(d1, "%Y-%m-%d").replace(tzinfo=CST),
            end=datetime.strptime(d2, "%Y-%m-%d").replace(tzinfo=CST) + timedelta(days=1),
        )
        modern = await _merge_results(storage, filters, [d1, d2])
    finally:
        monkeypatch.undo()

    assert modern["total"]["active_sessions"] == 1
    assert modern["total"]["new_sessions"] == 1

    legacy_mode[0] = True
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("src.infra.analytics.activity_storage.ActivityStorage.distinct_users", AsyncMock(return_value=[]))
    try:
        legacy = await _merge_results(storage, filters, [d1, d2])
    finally:
        monkeypatch.undo()
    assert legacy["total"]["active_sessions"] == 2
    assert legacy["total"]["new_sessions"] == 2


@pytest.mark.asyncio
async def test_freeze_releases_each_daily_lock_after_cancellation() -> None:
    """每个日期独占锁，续租 task 取消后仍会执行 release。"""
    from src.infra.analytics.snapshot import _freeze_dates

    class _Redis:
        def __init__(self):
            self.set_keys: list[str] = []
            self.release_keys: list[str] = []

        async def set(self, key, value, **kwargs):
            self.set_keys.append(key)
            return key.endswith("2026-08-01")

        async def eval(self, lua, count, key, value, *args):
            if "del" in lua:
                self.release_keys.append(key)
            return 1

    redis = _Redis()
    storage = MagicMock()
    storage.traces.aggregate = MagicMock(return_value=_FakeCursor([]))
    storage.sessions.aggregate = MagicMock(return_value=_FakeCursor([]))
    storage.snapshot.bulk_write = AsyncMock()
    storage.snapshot.update_one = AsyncMock()

    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=CST),
        end=datetime(2026, 8, 3, tzinfo=CST),
    )
    await _freeze_dates(storage, redis, ["2026-08-01", "2026-08-02"], filters)

    assert redis.set_keys == [
        "analytics:snapshot:freeze:2026-08-01",
        "analytics:snapshot:freeze:2026-08-02",
    ]
    assert redis.release_keys == ["analytics:snapshot:freeze:2026-08-01"]
    storage.snapshot.update_one.assert_awaited_once()
    marker_query, marker_update = storage.snapshot.update_one.call_args.args[:2]
    assert marker_query == {"date": "2026-08-01", "user_id": "__snapshot_complete__"}
    assert marker_update["$setOnInsert"]["user_id"] == "__snapshot_complete__"
    assert marker_update["$setOnInsert"]["date"] == "2026-08-01"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["redis_unavailable", "lock_competition", "freeze_failure"])
async def test_read_or_freeze_marks_incomplete_fallbacks(failure_mode, monkeypatch) -> None:
    from src.infra.analytics import snapshot as snapshot_module
    from src.infra.analytics.snapshot import read_or_freeze

    class _Redis:
        async def set(self, *args, **kwargs):
            return failure_mode != "lock_competition"

        async def eval(self, *args, **kwargs):
            return 1

        async def aclose(self):
            return None

    storage = MagicMock()
    storage.snapshot.find.return_value = _FakeCursor([])
    if failure_mode == "redis_unavailable":
        def unavailable(**kwargs):
            raise RuntimeError("redis unavailable")
        monkeypatch.setattr(snapshot_module, "create_redis_client", unavailable)
    else:
        monkeypatch.setattr(snapshot_module, "create_redis_client", lambda **kwargs: _Redis())
        if failure_mode == "freeze_failure":
            storage.traces.aggregate.side_effect = RuntimeError("aggregate failed")

    monkeypatch.setattr(
        snapshot_module,
        "_merge_results",
        AsyncMock(return_value={"by_user_persona": []}),
    )
    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=CST),
        end=datetime(2026, 8, 2, tzinfo=CST),
    )

    result = await read_or_freeze(filters, storage=storage)

    assert result["snapshot_complete"] is False


@pytest.mark.asyncio
async def test_realtime_fallback_keeps_dates_and_unions_cross_day_sessions() -> None:
    from src.infra.analytics.snapshot import _compute_realtime_aggregate

    d1 = "2026-08-01"
    d2 = "2026-08-02"
    storage = MagicMock()
    storage.traces.aggregate.return_value = _FakeCursor(
        [
            {"_id": d1, "user_messages": 1, "tokens": 2, "active_session_ids": ["s1"], "active_user_ids": ["u1"]},
            {"_id": d2, "user_messages": 1, "tokens": 3, "active_session_ids": ["s1"], "active_user_ids": ["u1"]},
        ]
    )
    storage.sessions.aggregate.return_value = _FakeCursor(
        [
            {"_id": d1, "new_sessions": 1, "new_session_ids": ["s1"]},
            {"_id": d2, "new_sessions": 1, "new_session_ids": ["s1"]},
        ]
    )
    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=CST),
        end=datetime(2026, 8, 3, tzinfo=CST),
    )

    result = await _compute_realtime_aggregate(storage, filters, [d1, d2])

    assert [point["date"] for point in result["trend"]] == [d1, d2]
    assert result["total"]["active_sessions"] == 1
    assert result["total"]["new_sessions"] == 1


@pytest.mark.asyncio
async def test_snapshot_aggregate_failure_marks_result_incomplete(monkeypatch) -> None:
    from src.infra.analytics import snapshot as snapshot_module
    from src.infra.analytics.snapshot import _merge_results

    storage = MagicMock()
    storage.snapshot.aggregate.side_effect = RuntimeError("snapshot read failed")
    storage.traces.aggregate.return_value = _FakeCursor([])
    monkeypatch.setattr(
        snapshot_module,
        "_compute_realtime_aggregate",
        AsyncMock(return_value={"trend": [], "active_user_ids": set()}),
    )
    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=CST),
        end=datetime(2026, 8, 2, tzinfo=CST),
    )

    result = await _merge_results(storage, filters, ["2026-08-01"])

    assert result["snapshot_complete"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])