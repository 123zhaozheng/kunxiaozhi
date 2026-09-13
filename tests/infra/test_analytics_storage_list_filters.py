"""AnalyticsStorage list/filter/by-agent unit tests."""

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

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length: int | None = None):
        if length is None:
            return list(self._docs)
        return list(self._docs)[:length]


def _range() -> tuple[datetime, datetime]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 17, tzinfo=timezone.utc)
    return start, end


@pytest.mark.asyncio
async def test_get_sessions_by_agent_uses_active_sessions_from_snapshot(monkeypatch):
    """环图切片与 KPI 卡/环图中心同源，均为去重后的活跃会话数。"""
    from src.infra.analytics import storage as storage_module

    storage = AnalyticsStorage()

    async def fake_read_or_freeze(filters, storage=None):
        return {
            "by_agent": [
                {"agent_id": "team", "active_sessions": 2, "new_sessions": 9},
                {"agent_id": "fast", "active_sessions": 5, "new_sessions": 1},
                {"agent_id": "idle", "active_sessions": 0, "new_sessions": 7},
            ]
        }

    monkeypatch.setattr(storage_module, "read_or_freeze", fake_read_or_freeze)

    start, end = _range()
    items = await storage.get_sessions_by_agent(start, end, limit=10)

    # Sorted by active sessions desc; zero-activity agents are not charted.
    assert [i.label for i in items] == ["fast", "team"]
    assert items[0].value == 5.0


@pytest.mark.asyncio
async def test_get_sessions_by_persona_includes_stable_id(monkeypatch):
    from src.infra.analytics import storage as storage_module

    storage = AnalyticsStorage()

    async def fake_read_or_freeze(filters, storage=None):
        return {
            "by_persona": [
                {
                    "persona_preset_id": "preset-1",
                    "persona_preset_name": "Friendly Bot",
                    "active_sessions": 4,
                },
                {
                    "persona_preset_id": "preset-2",
                    "persona_preset_name": None,
                    "active_sessions": 1,
                },
            ]
        }

    monkeypatch.setattr(storage_module, "read_or_freeze", fake_read_or_freeze)
    storage._preset_names = AsyncMock(return_value={})

    start, end = _range()
    items = await storage.get_sessions_by_persona(start, end, limit=10)

    assert items[0].label == "Friendly Bot"
    assert items[0].id == "preset-1"
    # Name lookup miss falls back to the preset id as the label.
    assert items[1].label == "preset-2"
    assert items[1].id == "preset-2"


@pytest.mark.asyncio
async def test_list_sessions_passes_agent_and_persona_filters():
    storage = AnalyticsStorage()
    user_oid = ObjectId()
    user_id = str(user_oid)
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=1)
    find_cursor = _FakeCursor(
        [
            {
                "session_id": "s1",
                "name": "n1",
                "user_id": user_id,
                "agent_id": "fast",
                "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
                "is_active": True,
                "task_status": None,
                "unread_count": 0,
                "metadata": {
                    "persona_preset_id": "p1",
                    "persona_preset_name": "P1",
                },
            }
        ]
    )
    sessions.find = MagicMock(return_value=find_cursor)
    users = MagicMock()
    users.find = MagicMock(
        return_value=_FakeCursor(
            [
                {"_id": user_oid, "username": "emp001"},
            ]
        )
    )
    storage._sessions = sessions
    storage._users = users

    start, end = _range()
    result = await storage.list_sessions(
        start,
        end,
        agent_id="fast",
        persona_preset_id="p1",
        sort="recent",
        skip=0,
        limit=20,
    )

    assert result.total == 1
    assert result.items[0].agent_id == "fast"
    assert result.items[0].persona_preset_id == "p1"
    assert result.items[0].username == "emp001"
    query = sessions.find.call_args[0][0]
    assert query["agent_id"] == "fast"
    assert query["metadata.persona_preset_id"] == "p1"
    assert "created_at" in query
    users.find.assert_called_once()
    user_query = users.find.call_args[0][0]
    assert "_id" in user_query
    assert "$in" in user_query["_id"]
    assert user_query["_id"]["$in"] == [user_oid]


@pytest.mark.asyncio
async def test_list_sessions_without_filters_only_time_range():
    storage = AnalyticsStorage()
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=0)
    sessions.find = MagicMock(return_value=_FakeCursor([]))
    users = MagicMock()
    users.find = MagicMock(return_value=_FakeCursor([]))
    storage._sessions = sessions
    storage._users = users

    start, end = _range()
    result = await storage.list_sessions(start, end, sort="recent")

    assert result.total == 0
    assert result.items == []
    query = sessions.find.call_args[0][0]
    assert set(query.keys()) == {"created_at"}
    users.find.assert_not_called()


@pytest.mark.asyncio
async def test_list_sessions_enriches_username_from_users_collection():
    storage = AnalyticsStorage()
    user_oid = ObjectId()
    user_id = str(user_oid)
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=2)
    sessions.find = MagicMock(
        return_value=_FakeCursor(
            [
                {
                    "session_id": "s1",
                    "name": "a",
                    "user_id": user_id,
                    "agent_id": "fast",
                    "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
                    "is_active": True,
                    "task_status": None,
                    "unread_count": 0,
                    "metadata": {},
                },
                {
                    "session_id": "s2",
                    "name": "b",
                    "user_id": "missing",
                    "agent_id": "fast",
                    "created_at": datetime(2026, 7, 3, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 7, 3, tzinfo=timezone.utc),
                    "is_active": True,
                    "task_status": None,
                    "unread_count": 0,
                    "metadata": {},
                },
            ]
        )
    )
    users = MagicMock()
    users.find = MagicMock(
        return_value=_FakeCursor([{"_id": user_oid, "username": "zhangsan"}])
    )
    storage._sessions = sessions
    storage._users = users

    start, end = _range()
    result = await storage.list_sessions(start, end, sort="recent", limit=20)

    assert result.items[0].username == "zhangsan"
    assert result.items[1].username is None
    assert result.items[1].user_id == "missing"
    user_query = users.find.call_args[0][0]
    assert user_query["_id"]["$in"] == [user_oid]


@pytest.mark.asyncio
async def test_list_sessions_frequency_sort_uses_legal_projection():
    """P1-4: frequency sort must not mix inclusion with field exclusion."""
    storage = AnalyticsStorage()
    user_oid = ObjectId()
    user_id = str(user_oid)
    sessions = MagicMock()
    sessions.count_documents = AsyncMock(return_value=1)
    sessions.aggregate = MagicMock(
        return_value=_FakeCursor(
            [
                {
                    "session_id": "s1",
                    "name": "n1",
                    "user_id": user_id,
                    "agent_id": "fast",
                    "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
                    "is_active": True,
                    "task_status": None,
                    "unread_count": 0,
                    "metadata": {},
                }
            ]
        )
    )
    users = MagicMock()
    users.find = MagicMock(
        return_value=_FakeCursor([{"_id": user_oid, "username": "emp001"}])
    )
    storage._sessions = sessions
    storage._users = users

    start, end = _range()
    result = await storage.list_sessions(start, end, sort="frequency", limit=20)

    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].username == "emp001"

    pipeline = sessions.aggregate.call_args[0][0]
    project_stages = [s for s in pipeline if "$project" in s]
    unset_stages = [s for s in pipeline if "$unset" in s]
    assert project_stages, "expected $project include stage"
    project = project_stages[-1]["$project"]
    # inclusion-only: no 0-valued exclusions mixed in
    assert all(v != 0 for v in project.values())
    assert "_freq" not in project
    assert "_user_key" not in project
    assert unset_stages
    assert set(unset_stages[-1]["$unset"]) == {"_freq", "_user_key"}


@pytest.mark.asyncio
async def test_list_active_users_frequency_sort_and_filters():
    """活跃用户改为按 user:message 归因，因此聚合源是 traces 而非 sessions。"""
    storage = AnalyticsStorage()
    user_oid = ObjectId()
    user_id = str(user_oid)
    traces = MagicMock()
    traces.aggregate = MagicMock(
        return_value=_FakeCursor(
            [
                {
                    "total": [{"count": 1}],
                    "items": [
                        {
                            "_id": user_id,
                            "session_count": 3,
                            "last_active_at": datetime(
                                2026, 7, 5, tzinfo=timezone.utc
                            ),
                        }
                    ],
                }
            ]
        )
    )
    users = MagicMock()
    users.find = MagicMock(
        return_value=_FakeCursor(
            [
                {
                    "_id": user_oid,
                    "username": "alice",
                    "display_name": "Alice",
                    "roles": ["role-a"],
                }
            ]
        )
    )
    storage._traces = traces
    storage._users = users

    start, end = _range()
    result = await storage.list_active_users(
        start,
        end,
        agent_id="search",
        sort="frequency",
        skip=0,
        limit=20,
    )

    assert result.total == 1
    assert result.items[0].user_id == user_id
    assert result.items[0].username == "alice"
    assert result.items[0].session_count == 3
    assert result.items[0].roles == ["role-a"]

    pipeline = traces.aggregate.call_args[0][0]
    match = pipeline[0]["$match"]
    assert match["agent_id"] == "search"
    # 只有发过用户消息的 trace 参与活跃判定
    assert {"user_messages": {"$gt": 0}, "user_id": {"$nin": [None, ""]}} in [
        stage.get("$match") for stage in pipeline if "$match" in stage
    ]
    facet_stage = next(s["$facet"] for s in pipeline if "$facet" in s)
    sort_stage = next(s for s in facet_stage["items"] if "$sort" in s)
    assert sort_stage["$sort"]["session_count"] == -1
    user_query = users.find.call_args[0][0]
    assert user_query["_id"]["$in"] == [user_oid]


@pytest.mark.asyncio
async def test_session_user_ids_for_role_queries_users_by_role_only():
    """角色成员直接来自 users.roles，不再由区间内新建会话反推。

    旧实现先查 ``sessions.created_at`` 拿 user_id 再回查 users，导致只使用
    更早创建会话的成员被漏掉，按角色筛选时所有指标系统性少算。
    """
    storage = AnalyticsStorage()
    user_oid = ObjectId()
    user_id = str(user_oid)
    sessions = MagicMock()
    sessions.distinct = AsyncMock(return_value=[])
    users = MagicMock()
    users.find = MagicMock(return_value=_FakeCursor([{"_id": user_oid}]))
    storage._sessions = sessions
    storage._users = users

    start, end = _range()
    result = await storage._session_user_ids_for_role("role-a", start, end)

    assert result == [user_id]
    user_query = users.find.call_args[0][0]
    assert user_query == {"roles": "role-a"}
    # 角色成员与会话创建时间无关，不得再查 sessions
    sessions.distinct.assert_not_awaited()
