"""活跃用户一致性：概览卡片与钻取列表必须同源。

改造前的缺陷：概览卡按 users.updated_at（改头像也算活跃），钻取列表按
sessions.created_at，点卡片钻进去总数与卡片对不上。现在两者都走
usage_facts_stages，因此在同一份数据上必须相等。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from src.infra.analytics.storage import AnalyticsStorage


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


# Three users sent messages in range; a fourth user only had its document
# touched (the old active-user definition would have counted it).
_ACTIVE_USER_OIDS = [ObjectId(), ObjectId(), ObjectId()]
_ACTIVE_USER_IDS = [str(oid) for oid in _ACTIVE_USER_OIDS]


def _summary_facet_doc() -> dict[str, Any]:
    return {
        "_id": None,
        "user_messages": 42,
        "total_tokens": 9001,
        "active_user_ids": list(_ACTIVE_USER_IDS),
        "active_session_ids": ["s1", "s2", "s3", "s4"],
    }


def _users_facet_doc() -> dict[str, Any]:
    return {
        "total": [{"count": len(_ACTIVE_USER_IDS)}],
        "items": [
            {
                "_id": uid,
                "session_count": 2,
                "last_active_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
            }
            for uid in _ACTIVE_USER_IDS
        ],
    }


def _storage_for_overview() -> AnalyticsStorage:
    storage = AnalyticsStorage()
    traces = MagicMock()
    # get_usage_summary goes through read_or_freeze then falls back to direct
    # aggregation; multiple aggregate calls are expected. Return the summary
    # doc for every call so each pipeline gets valid data.
    traces.aggregate = MagicMock(
        side_effect=lambda *a, **kw: _FakeCursor([_summary_facet_doc()])
    )
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=7)
    sessions.aggregate = MagicMock(return_value=_FakeCursor([{"value": 7}]))
    feedback = MagicMock()
    feedback.aggregate = MagicMock(
        return_value=_FakeCursor([{"_id": None, "up": 8, "down": 2}])
    )
    storage._traces = traces
    storage._sessions = sessions
    storage._feedback = feedback
    return storage


def _storage_for_users_list() -> AnalyticsStorage:
    storage = AnalyticsStorage()
    traces = MagicMock()
    traces.aggregate = MagicMock(return_value=_FakeCursor([_users_facet_doc()]))
    users = MagicMock()
    users.find = MagicMock(
        return_value=_FakeCursor(
            [
                {"_id": oid, "username": f"emp{i}", "display_name": None, "roles": []}
                for i, oid in enumerate(_ACTIVE_USER_OIDS)
            ]
        )
    )
    storage._traces = traces
    storage._users = users
    return storage


@pytest.mark.asyncio
async def test_overview_active_users_equals_users_list_total():
    start, end = _range()

    overview = await _storage_for_overview().get_overview(start, end)
    users = await _storage_for_users_list().list_active_users(start, end, limit=20)

    assert overview.active_users == len(_ACTIVE_USER_IDS)
    assert users.total == overview.active_users


@pytest.mark.asyncio
async def test_overview_active_users_no_longer_reads_users_collection():
    """回归防护：活跃用户不得再回到 users.updated_at 口径。"""
    start, end = _range()
    storage = _storage_for_overview()
    users = MagicMock()
    users.aggregate = MagicMock(side_effect=AssertionError("must not aggregate users"))
    storage._users = users

    result = await storage.get_overview(start, end)

    assert result.active_users == len(_ACTIVE_USER_IDS)
    users.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_active_user_ids_ignore_traces_without_user_messages():
    """只有发过用户消息的 trace 才计入活跃集合（null 由 $cond 产出后被过滤）。"""
    storage = AnalyticsStorage()
    traces = MagicMock()
    _doc = {
        "_id": None,
        "user_messages": 1,
        "total_tokens": 10,
        # null 来自没有用户消息的 trace
        "active_user_ids": [_ACTIVE_USER_IDS[0], None],
        "active_session_ids": ["s1", None],
    }
    traces.aggregate = MagicMock(
        side_effect=lambda *a, **kw: _FakeCursor([_doc])
    )
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=0)
    storage._traces = traces
    storage._sessions = sessions

    start, end = _range()
    summary = await storage.get_usage_summary(
        await storage.build_usage_filters(start, end)
    )

    assert summary.active_users == 1
    assert summary.active_sessions == 1
