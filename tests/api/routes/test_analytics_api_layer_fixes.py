"""Analytics API/storage regression tests for the non-destructive correctness fixes."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from src.api.routes.analytics import (
    export_usage_csv,
    get_sessions_by_agent,
    get_sessions_by_persona,
    get_tokens_by_model,
)
from src.infra.analytics import storage as storage_module
from src.infra.analytics.storage import AnalyticsStorage
from src.infra.analytics.usage_query import UsageFilters


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for doc in self.docs:
            yield doc

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.docs if length is None else self.docs[:length]


class _RingManager:
    def __init__(self) -> None:
        self.filters: list[UsageFilters] = []
        self.calls: list[tuple[str, UsageFilters | None]] = []

    async def build_usage_filters(self, start, end, **kwargs) -> UsageFilters:
        filters = UsageFilters(
            start=start,
            end=end,
            persona_preset_id=kwargs.get("persona_preset_id"),
            agent_id=kwargs.get("agent_id"),
            role_user_ids=["user-1"] if kwargs.get("role_id") else None,
        )
        self.filters.append(filters)
        return filters

    async def get_sessions_by_agent(self, start, end, limit=10, filters=None):
        self.calls.append(("agent", filters))
        return []

    async def get_sessions_by_persona(self, start, end, limit=10, filters=None):
        self.calls.append(("persona", filters))
        return []

    async def get_tokens_by_model(self, start, end, filters=None):
        self.calls.append(("model", filters))
        return []


@pytest.mark.asyncio
async def test_ring_routes_forward_all_optional_filters() -> None:
    manager = _RingManager()
    kwargs = {
        "start": "2026-08-01",
        "end": "2026-08-03",
        "persona_preset_id": "persona-a",
        "agent_id": "agent-a",
        "role_id": "role-a",
        "_": None,
        "manager": manager,
    }

    await get_sessions_by_agent(limit=10, **kwargs)
    await get_sessions_by_persona(limit=10, **kwargs)
    await get_tokens_by_model(**kwargs)

    assert [name for name, _ in manager.calls] == ["agent", "persona", "model"]
    for _, filters in manager.calls:
        assert filters is not None
        assert filters.persona_preset_id == "persona-a"
        assert filters.agent_id == "agent-a"
        assert filters.role_user_ids == ["user-1"]


@pytest.mark.asyncio
async def test_filtered_ring_pipelines_apply_filters_and_persona_fallback(monkeypatch) -> None:
    from src.infra.analytics import storage as storage_module

    storage = AnalyticsStorage()
    traces = MagicMock()
    traces.aggregate.return_value = _Cursor([{"label": "model-a", "value": 20}])
    storage._traces = traces
    storage._preset_names = AsyncMock(return_value={})
    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 3, tzinfo=timezone.utc),
        persona_preset_id="persona-a",
        agent_id="agent-a",
        role_user_ids=["user-1"],
    )

    seen: list[UsageFilters] = []

    async def fake_read_or_freeze(received, storage=None):
        seen.append(received)
        return {
            "by_agent": [{"agent_id": "agent-a", "active_sessions": 2}],
            "by_persona": [
                {
                    "persona_preset_id": "persona-a",
                    "persona_preset_name": "A",
                    "active_sessions": 2,
                }
            ],
        }

    monkeypatch.setattr(storage_module, "read_or_freeze", fake_read_or_freeze)

    # Both session donuts read the filtered snapshot, so their slices use the
    # same de-duplicated active-session metric as the KPI card and centre.
    agent_items = await storage.get_sessions_by_agent(
        filters.start, filters.end, filters=filters
    )
    assert [(i.label, i.value) for i in agent_items] == [("agent-a", 2.0)]

    persona_items = await storage.get_sessions_by_persona(
        filters.start, filters.end, filters=filters
    )
    assert [(i.label, i.id) for i in persona_items] == [("A", "persona-a")]

    assert len(seen) == 2
    for received in seen:
        assert received.persona_preset_id == "persona-a"
        assert received.agent_id == "agent-a"
        assert received.role_user_ids == ["user-1"]

    await storage.get_tokens_by_model(filters.start, filters.end, filters=filters)
    token_pipeline = traces.aggregate.call_args.args[0]
    assert token_pipeline[0]["$match"]["agent_id"] == "agent-a"
    assert {"$match": {"persona_preset_id": "persona-a"}} in token_pipeline


@pytest.mark.asyncio
async def test_role_members_are_loaded_directly_from_users() -> None:
    storage = AnalyticsStorage()
    sessions = MagicMock()
    sessions.distinct = AsyncMock(side_effect=AssertionError("role lookup must not use sessions"))
    users = MagicMock()
    user_id = ObjectId()
    users.find.return_value = _Cursor([{"_id": user_id}])
    storage._sessions = sessions
    storage._users = users

    result = await storage._session_user_ids_for_role(
        "role-a",
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    assert result == [str(user_id)]
    assert users.find.call_args.args[0] == {"roles": "role-a"}
    sessions.distinct.assert_not_called()


@pytest.mark.asyncio
async def test_peak_pipeline_buckets_user_message_event_timestamp() -> None:
    storage = AnalyticsStorage()
    traces = MagicMock()
    traces.aggregate.return_value = _Cursor(
        [{"_id": {"weekday": 2, "hour": 11}, "user_messages": 3}]
    )
    storage._traces = traces
    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    peak = await storage._insights_peak(filters)

    assert peak is not None
    assert peak.hour == 11
    pipeline = traces.aggregate.call_args.args[0]
    assert {"$unwind": "$events"} in pipeline
    assert {"$match": {"events.event_type": "user:message"}} in pipeline
    group = next(stage["$group"] for stage in pipeline if "$group" in stage)
    assert group["user_messages"] == {"$sum": 1}
    assert group["_id"]["hour"]["$hour"]["date"] == "$events.timestamp"


@pytest.mark.asyncio
async def test_by_user_uses_frozen_by_user_persona_and_paginates_in_memory(monkeypatch) -> None:
    storage = AnalyticsStorage()
    user_id = ObjectId()
    storage._users = MagicMock()
    storage._users.find.return_value = _Cursor(
        [{"_id": user_id, "username": "alice", "display_name": "Alice", "roles": ["role-a"]}]
    )
    storage._traces = MagicMock()
    storage._traces.aggregate = MagicMock(side_effect=AssertionError("must use snapshot rows"))
    frozen = {
        "total": {"user_messages": 5, "tokens": 50},
        "by_user_persona": [
            {
                "user_id": str(user_id),
                "persona_preset_id": "persona-a",
                "persona_preset_name": "A",
                "user_messages": 2,
                "tokens": 20,
                "active_sessions": 1,
                "new_sessions": 1,
                "last_active_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            },
            {
                "user_id": str(user_id),
                "persona_preset_id": "persona-b",
                "persona_preset_name": "B",
                "user_messages": 3,
                "tokens": 30,
                "active_sessions": 2,
                "new_sessions": 2,
                "last_active_at": datetime(2026, 8, 2, tzinfo=timezone.utc),
            },
        ],
    }

    async def fake_read_or_freeze(filters, storage=None):
        return frozen

    monkeypatch.setattr(storage_module, "read_or_freeze", fake_read_or_freeze)
    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    result = await storage.list_usage_by_user(filters, skip=1, limit=1)

    assert result.total == 2
    assert result.skip == 1
    assert result.has_more is False
    assert len(result.items) == 1
    # Rows are ordered by user_messages desc, so persona-b (3) is page 1 and
    # persona-a (2) is page 2. Pagination must not depend on aggregate order.
    assert result.items[0].persona_preset_id == "persona-a"
    assert result.items[0].user_messages == 2
    assert result.items[0].total_tokens == 20
    assert result.items[0].username == "alice"
    assert result.items[0].roles == ["role-a"]

    first_page = await storage.list_usage_by_user(filters, skip=0, limit=1)
    assert first_page.items[0].persona_preset_id == "persona-b"
    assert first_page.has_more is True


@pytest.mark.asyncio
async def test_by_user_snapshot_rows_match_summary_numbers(monkeypatch) -> None:
    storage = AnalyticsStorage()
    frozen = {
        "total": {
            "new_sessions": 3,
            "active_sessions": 3,
            "user_messages": 5,
            "tokens": 50,
        },
        "active_users": 2,
        "using_users": 2,
        "by_user_persona": [
            {
                "user_id": "not-an-object-id",
                "persona_preset_id": "persona-a",
                "user_messages": 2,
                "tokens": 20,
                "active_sessions": 1,
                "new_sessions": 1,
            },
            {
                "user_id": "also-not-an-object-id",
                "persona_preset_id": "persona-b",
                "user_messages": 3,
                "tokens": 30,
                "active_sessions": 2,
                "new_sessions": 2,
            },
        ],
    }

    async def fake_read_or_freeze(filters, storage=None):
        return frozen

    monkeypatch.setattr(storage_module, "read_or_freeze", fake_read_or_freeze)
    filters = UsageFilters(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    summary = await storage._usage_summary_numbers(filters)
    detail = await storage.list_usage_by_user(filters, skip=0, limit=100)

    assert summary["user_messages"] == sum(item.user_messages for item in detail.items)
    assert summary["total_tokens"] == sum(item.total_tokens for item in detail.items)
    assert summary["new_sessions"] == sum(item.new_sessions for item in detail.items)
    assert summary["active_sessions"] == sum(item.active_sessions for item in detail.items)

    class _ExportManager:
        async def build_usage_filters(self, start, end, **kwargs) -> UsageFilters:
            return UsageFilters(start=start, end=end)

        async def list_usage_by_user(self, filters, skip=0, limit=20):
            return await storage.list_usage_by_user(filters, skip=skip, limit=limit)

    response = await export_usage_csv(
        start="2026-08-01",
        end="2026-08-03",
        _=None,
        manager=_ExportManager(),
    )
    rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert sum(int(row["user_messages"]) for row in rows) == summary["user_messages"]
    assert sum(int(row["total_tokens"]) for row in rows) == summary["total_tokens"]
