"""checkpoint 清理：定时任务会话（``sch_*``，隐藏于会话列表）与普通会话一样参与清理。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.infra.session.storage import SessionStorage

CUTOFF = datetime(2026, 9, 1, tzinfo=timezone.utc)


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, *args, **kwargs) -> "_FakeCursor":
        return self

    def limit(self, n: int) -> "_FakeCursor":
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        return list(self._docs)


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.queries: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any], projection: dict[str, Any] | None = None):
        self.queries.append(query)
        return _FakeCursor(self.docs)


@pytest.mark.asyncio
async def test_inactive_session_query_does_not_exclude_hidden_scheduled_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = SessionStorage()
    collection = _FakeCollection(
        [
            {"_id": "a", "session_id": "regular-1"},
            {
                "_id": "b",
                "session_id": "sch_task-1_run-1",
                "metadata": {"hidden_from_conversation_list": True},
            },
        ]
    )
    storage._collection = collection

    async def _noop() -> None:
        return None

    monkeypatch.setattr(storage, "ensure_indexes_if_needed", _noop)

    ids = await storage.list_inactive_session_ids(cutoff=CUTOFF, limit=10)

    assert ids == ["regular-1", "sch_task-1_run-1"]
    query_text = repr(collection.queries[0])
    # 清理查询不得按隐藏标记 / 来源过滤，否则定时会话的 checkpoint 永远不会被清理
    assert "hidden_from_conversation_list" not in query_text
    assert "scheduled_task" not in query_text
    assert "source" not in query_text
