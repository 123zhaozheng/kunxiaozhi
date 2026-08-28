"""使用情况查询层口径测试。

锁住三件事：
1. 「用户消息数」只数 user:message，工具调用/流式分片/token 事件不计入
2. persona 归属以会话为准，trace 缺失时回落
3. persona 的 $match 必须排在解析 persona 的 $addFields 之后
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.infra.analytics.usage_query import (
    UsageFilters,
    new_sessions_match,
    usage_facts_stages,
)


def _range() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 17, tzinfo=timezone.utc),
    )


def _trace_doc() -> dict[str, Any]:
    """一个真实形态的 trace：用户只发了 3 条消息，但事件总数是 30。"""
    events: list[dict[str, Any]] = []
    for i in range(3):
        events.append({"event_type": "user:message", "data": {"content": f"q{i}"}})
    for _ in range(20):
        events.append({"event_type": "message:chunk", "data": {"delta": "x"}})
    for _ in range(5):
        events.append({"event_type": "tool:call", "data": {"name": "search"}})
    events.append({"event_type": "token:usage", "data": {"total_tokens": 1200}})
    events.append({"event_type": "token:usage", "data": {"total_tokens": 800}})
    return {
        "trace_id": "t1",
        "session_id": "s1",
        "user_id": "u1",
        "started_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "event_count": len(events),
        "events": events,
        "metadata": {},
    }


# ── Minimal evaluator for the expression shapes usage_facts_stages emits ──
# Proves the emitted pipeline actually computes the intended numbers; a
# regression that swaps the filtered event type changes the result here.


def _resolve_path(doc: dict[str, Any], path: str) -> Any:
    current: Any = doc
    for i, part in enumerate(path.split(".")):
        if isinstance(current, list):
            # Mongo maps a field path over an array field, as with $lookup output.
            remaining = ".".join(path.split(".")[i:])
            return [_resolve_path(item, remaining) for item in current]
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _eval(expr: Any, doc: dict[str, Any], var: Any = None) -> Any:
    if isinstance(expr, str):
        if expr.startswith("$$"):
            return _resolve_path(var, expr[2:].split(".", 1)[1]) if var else None
        if expr.startswith("$"):
            return _resolve_path(doc, expr[1:])
        return expr
    if not isinstance(expr, dict):
        return expr

    if "$filter" in expr:
        spec = expr["$filter"]
        items = _eval(spec["input"], doc, var) or []
        return [item for item in items if _eval(spec["cond"], doc, item)]
    if "$size" in expr:
        return len(_eval(expr["$size"], doc, var) or [])
    if "$map" in expr:
        spec = expr["$map"]
        items = _eval(spec["input"], doc, var) or []
        return [_eval(spec["in"], doc, item) for item in items]
    if "$sum" in expr:
        values = _eval(expr["$sum"], doc, var)
        if not isinstance(values, list):
            return values or 0
        return sum(v or 0 for v in values)
    if "$arrayElemAt" in expr:
        array_expr, index = expr["$arrayElemAt"]
        values = _eval(array_expr, doc, var) or []
        if not isinstance(values, list) or index >= len(values):
            return None
        return values[index]
    if "$eq" in expr:
        left, right = expr["$eq"]
        return _eval(left, doc, var) == _eval(right, doc, var)
    if "$ifNull" in expr:
        candidates = expr["$ifNull"]
        for candidate in candidates[:-1]:
            value = _eval(candidate, doc, var)
            if value is not None:
                return value
        return _eval(candidates[-1], doc, var)
    raise AssertionError(f"unsupported expression in pipeline: {expr}")


def _metric_stage(stages: list[dict[str, Any]]) -> dict[str, Any]:
    """取计算 user_messages / tokens 的那个 $addFields。"""
    for stage in stages:
        fields = stage.get("$addFields") or {}
        if "user_messages" in fields:
            return fields
    raise AssertionError("no $addFields stage computing user_messages")


def test_user_messages_counts_only_user_message_events():
    start, end = _range()
    stages = usage_facts_stages(UsageFilters(start=start, end=end))
    fields = _metric_stage(stages)
    doc = _trace_doc()

    assert _eval(fields["user_messages"], doc) == 3
    # 事件总数是 30，若误用 event_count 会得到 30
    assert doc["event_count"] == 30


def test_tokens_sums_only_token_usage_events():
    start, end = _range()
    stages = usage_facts_stages(UsageFilters(start=start, end=end))
    fields = _metric_stage(stages)

    assert _eval(fields["tokens"], _trace_doc()) == 2000


def test_metric_filters_pin_the_event_types():
    """显式锁住被筛的事件类型，防止改成别的类型后仍然"能跑"。"""
    start, end = _range()
    fields = _metric_stage(usage_facts_stages(UsageFilters(start=start, end=end)))

    assert fields["user_messages"]["$size"]["$filter"]["cond"] == {
        "$eq": ["$$event.event_type", "user:message"]
    }
    token_filter = fields["tokens"]["$sum"]["$map"]["input"]["$filter"]
    assert token_filter["cond"] == {"$eq": ["$$event.event_type", "token:usage"]}


def test_match_narrows_to_relevant_event_types():
    start, end = _range()
    stages = usage_facts_stages(UsageFilters(start=start, end=end))
    match = stages[0]["$match"]

    assert match["started_at"] == {"$gte": start, "$lte": end}
    assert sorted(match["events.event_type"]["$in"]) == ["token:usage", "user:message"]


def test_persona_falls_back_from_session_to_trace():
    start, end = _range()
    stages = usage_facts_stages(UsageFilters(start=start, end=end))
    persona_expr = next(
        stage["$addFields"]["persona_preset_id"]
        for stage in stages
        if "persona_preset_id" in (stage.get("$addFields") or {})
    )

    # 会话有 persona、trace 没有：归到会话的 persona（历史数据的常见形态）
    session_wins = {
        "_session": [{"metadata": {"persona_preset_id": "from-session"}}],
        "metadata": {},
    }
    assert _eval(persona_expr, session_wins) == "from-session"

    # 会话缺失时回落到 trace 自己的 metadata
    trace_fallback = {
        "_session": [],
        "metadata": {"persona_preset_id": "from-trace"},
    }
    assert _eval(persona_expr, trace_fallback) == "from-trace"


def test_persona_match_comes_after_persona_resolution():
    start, end = _range()
    stages = usage_facts_stages(
        UsageFilters(start=start, end=end, persona_preset_id="p1")
    )

    addfields_index = next(
        i
        for i, stage in enumerate(stages)
        if "persona_preset_id" in (stage.get("$addFields") or {})
    )
    match_index = next(
        i
        for i, stage in enumerate(stages)
        if stage.get("$match", {}).get("persona_preset_id") == "p1"
    )
    assert match_index > addfields_index


def test_persona_match_absent_without_filter():
    start, end = _range()
    stages = usage_facts_stages(UsageFilters(start=start, end=end))

    assert not [
        stage
        for stage in stages
        if "persona_preset_id" in (stage.get("$match") or {})
    ]


def test_agent_and_role_filters_land_in_first_match():
    start, end = _range()
    stages = usage_facts_stages(
        UsageFilters(
            start=start,
            end=end,
            agent_id="fast",
            role_user_ids=["u1", "u2"],
        )
    )
    match = stages[0]["$match"]

    assert match["agent_id"] == "fast"
    assert match["user_id"] == {"$in": ["u1", "u2"]}


def test_empty_role_user_ids_matches_nothing():
    """角色下没有任何用户时必须筛出空集，而不是退化成不过滤。"""
    start, end = _range()
    stages = usage_facts_stages(
        UsageFilters(start=start, end=end, role_user_ids=[])
    )

    assert stages[0]["$match"]["user_id"] == {"$in": []}


def test_new_sessions_match_uses_created_at_and_same_filters():
    start, end = _range()
    match = new_sessions_match(
        UsageFilters(
            start=start,
            end=end,
            persona_preset_id="p1",
            agent_id="fast",
            role_user_ids=["u1"],
        )
    )

    assert match["created_at"] == {"$gte": start, "$lte": end}
    assert match["metadata.persona_preset_id"] == "p1"
    assert match["agent_id"] == "fast"
    assert match["user_id"] == {"$in": ["u1"]}
