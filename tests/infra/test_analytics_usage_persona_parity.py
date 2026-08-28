"""persona 平价：指定 persona 时 /usage/summary 必须与 /presets/{id} 一致。

两个出口现在都走 usage_facts_stages，因此在同一份数据上
user_messages / total_tokens / active_users 三项必须严格相等。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infra.analytics.storage import AnalyticsStorage

_PRESET_ID = "preset-abc"


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


def _range() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 17, tzinfo=timezone.utc),
    )


def _usage_doc() -> dict[str, Any]:
    return {
        "_id": None,
        "user_messages": 17,
        "total_tokens": 4321,
        "active_user_ids": ["u1", "u2"],
        "active_session_ids": ["s1", "s2", "s3"],
    }


def _storage(*, with_feedback: bool = False) -> AnalyticsStorage:
    storage = AnalyticsStorage()
    traces = MagicMock()
    traces.aggregate = MagicMock(side_effect=lambda *a, **kw: _FakeCursor([_usage_doc()]))
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=5)
    sessions.distinct = AsyncMock(return_value=["s1", "s2"] if with_feedback else [])
    feedback = MagicMock()
    feedback.aggregate = MagicMock(
        return_value=_FakeCursor(
            [{"_id": None, "up": 3, "down": 1, "reasons": ["wrong", "slow"]}]
        )
    )
    storage._traces = traces
    storage._sessions = sessions
    storage._feedback = feedback
    return storage


@pytest.mark.asyncio
async def test_usage_summary_and_preset_metrics_agree():
    start, end = _range()

    summary_storage = _storage()
    summary = await summary_storage.get_usage_summary(
        await summary_storage.build_usage_filters(
            start, end, persona_preset_id=_PRESET_ID
        )
    )
    preset = await _storage().get_preset_metrics(_PRESET_ID, start, end)

    assert preset.total_messages == summary.user_messages
    assert preset.total_tokens == summary.total_tokens
    assert preset.active_users == summary.active_users
    assert preset.total_sessions == summary.new_sessions


@pytest.mark.asyncio
async def test_preset_metrics_filters_traces_by_resolved_persona():
    start, end = _range()
    storage = _storage()

    await storage.get_preset_metrics(_PRESET_ID, start, end)

    pipeline = storage._traces.aggregate.call_args[0][0]
    persona_matches = [
        stage["$match"]
        for stage in pipeline
        if stage.get("$match", {}).get("persona_preset_id") == _PRESET_ID
    ]
    assert persona_matches, "preset metrics must filter on the resolved persona"


@pytest.mark.asyncio
async def test_preset_metrics_keeps_feedback_two_step_lookup():
    """反馈仍走会话两步法，不受使用情况层改造影响。"""
    start, end = _range()
    storage = _storage(with_feedback=True)

    result = await storage.get_preset_metrics(_PRESET_ID, start, end)

    storage._sessions.distinct.assert_awaited()
    assert result.up_vote_rate == 75.0
    assert [item.label for item in result.down_reasons] == ["slow", "wrong"] or [
        item.label for item in result.down_reasons
    ] == ["wrong", "slow"]
