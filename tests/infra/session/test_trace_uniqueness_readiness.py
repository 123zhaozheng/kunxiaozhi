from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.session.trace_storage import (
    TraceIdentityConflictError,
    TraceStorage,
    TraceWriteUnavailableError,
)


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.rows[:length] if length else self.rows


class _IndexCollection:
    def __init__(self, duplicates: list[dict[str, Any]] | None = None) -> None:
        self.indexes: list[str] = []
        self.duplicate_rows = duplicates or []
        self.fail_once: set[str] = set()

    async def create_index(self, _keys, *, name: str, **_kwargs) -> str:
        if name in self.fail_once:
            self.fail_once.remove(name)
            raise RuntimeError("temporary mongo failure")
        self.indexes.append(name)
        return name

    def aggregate(self, _pipeline):
        return _Cursor(self.duplicate_rows)


class _UpsertCollection:
    def __init__(self, doc: dict[str, Any] | None = None) -> None:
        self.doc = doc
        self.update_calls: list[dict[str, Any]] = []

    async def update_one(self, query, update, *, upsert: bool):
        self.update_calls.append({"query": query, "update": update, "upsert": upsert})
        if self.doc is None:
            self.doc = dict(update["$setOnInsert"])
            return SimpleNamespace(upserted_id="new")
        return SimpleNamespace(upserted_id=None)

    async def find_one(self, query, *_args):
        if self.doc and self.doc.get("trace_id") == query.get("trace_id"):
            return self.doc
        return None

    async def update_one_metadata(self, *_args, **_kwargs):
        return SimpleNamespace(modified_count=1)


@pytest.mark.asyncio
async def test_index_readiness_preflights_duplicates_and_blocks_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.kernel.config.settings.ENABLE_EVENT_MERGER", False)
    storage = TraceStorage()
    storage._collection = _IndexCollection([{"_id": "trace-1"}])

    assert await storage.ensure_indexes_if_needed() is False
    assert storage.index_status["ready"] is False
    assert "trace_id_unique_idx" in storage.index_status["errors"]
    assert "trace_id_unique_idx" not in storage.collection.indexes

    with pytest.raises(TraceWriteUnavailableError):
        await storage.create_trace("trace-1", "session-1")


@pytest.mark.asyncio
async def test_index_failure_is_retryable() -> None:
    storage = TraceStorage()
    collection = _IndexCollection()
    collection.fail_once.add("started_at_idx")
    storage._collection = collection

    assert await storage.ensure_indexes_if_needed() is True
    assert storage.index_status["attempts"] == 1
    assert "started_at_idx" in collection.indexes


@pytest.mark.asyncio
async def test_create_trace_upsert_rejects_identity_conflict() -> None:
    storage = TraceStorage()
    storage._collection = _UpsertCollection(
        {"trace_id": "trace-1", "session_id": "session-old", "run_id": "run-1"}
    )

    with pytest.raises(TraceIdentityConflictError):
        await storage.create_trace("trace-1", "session-new", run_id="run-1")


@pytest.mark.asyncio
async def test_readiness_is_fail_closed_before_trace_indexes_are_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from src.api.routes import health

    storage = TraceStorage()
    async def _not_ready() -> bool:
        return False

    storage.ensure_indexes_if_needed = _not_ready  # type: ignore[method-assign]
    monkeypatch.setattr(
        "src.infra.session.trace_storage.get_trace_storage",
        lambda: storage,
    )

    with pytest.raises(HTTPException) as exc_info:
        await health.readiness_check()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["attempted"] is False
    assert exc_info.value.detail["ready"] is False
