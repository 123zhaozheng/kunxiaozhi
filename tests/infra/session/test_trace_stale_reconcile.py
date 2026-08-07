"""Heartbeat/task-aware stale trace recovery tests."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.infra.session.trace_storage import TraceStorage
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings


class _Cursor:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count: int):
        self.docs = self.docs[:count]
        return self

    async def to_list(self, length: int | None = None):
        return self.docs[:length] if length else self.docs


class _TraceCollection:
    def __init__(self, docs: list[dict], modified_count: int = 1) -> None:
        self.docs = docs
        self.modified_count = modified_count
        self.find_query = None
        self.update_query = None
        self.update = None

    def find(self, query, *_args, **_kwargs):
        self.find_query = query
        return _Cursor(self.docs)

    async def update_one(self, query, update):
        self.update_query = query
        self.update = update
        return SimpleNamespace(modified_count=self.modified_count)


class _SessionCollection:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata
        self.queries = []

    async def find_one(self, query, projection=None):
        self.queries.append((query, projection))
        return {"metadata": self.metadata}


def _trace(**overrides) -> dict:
    doc = {
        "session_id": "session-1",
        "trace_id": "trace-1",
        "run_id": "run-1",
        "status": "running",
        "started_at": utc_now() - timedelta(minutes=10),
        "updated_at": utc_now() - timedelta(minutes=10),
        "events": [{"event_type": "done"}],
    }
    doc.update(overrides)
    return doc


async def _reconcile(storage, trace_collection, session_metadata, heartbeat=False):
    storage._collection = trace_collection
    return await storage.reconcile_stale_running_traces(
        session_collection=_SessionCollection(session_metadata),
        heartbeat_check=lambda _run_id: _heartbeat(heartbeat),
        grace_seconds=60,
    )


async def _heartbeat(value: bool) -> bool:
    return value


@pytest.mark.asyncio
async def test_reconcile_requires_grace_and_uses_running_cas() -> None:
    storage = TraceStorage()
    collection = _TraceCollection([])
    assert await _reconcile(storage, collection, {"task_status": "completed", "current_run_id": "run-1"}) == 0
    assert collection.find_query["status"] == "running"

    collection = _TraceCollection([_trace()])
    assert await _reconcile(storage, collection, {"task_status": "completed", "current_run_id": "run-1"}) == 1
    assert collection.update_query["status"] == "running"
    assert collection.update_query["trace_id"] == "trace-1"
    assert collection.update["$set"]["reconciled_reason"] == "terminal_task_status"
    assert "reconciled_observed_state" in collection.update["$set"]


@pytest.mark.asyncio
async def test_active_heartbeat_never_flips_even_with_done_event() -> None:
    storage = TraceStorage()
    collection = _TraceCollection([_trace()])
    count = await _reconcile(storage, collection, {"task_status": "completed", "current_run_id": "run-1"}, heartbeat=True)
    assert count == 0
    assert collection.update_query is None
    assert storage.stale_recovery_metrics["skipped_active_heartbeat"] == 1


@pytest.mark.asyncio
async def test_running_task_without_terminal_evidence_is_preserved() -> None:
    storage = TraceStorage()
    collection = _TraceCollection([_trace(events=[])])
    count = await _reconcile(storage, collection, {"task_status": "running", "current_run_id": "run-1"})
    assert count == 0
    assert collection.update_query is None


@pytest.mark.asyncio
async def test_terminal_event_after_heartbeat_timeout_can_reconcile() -> None:
    storage = TraceStorage()
    collection = _TraceCollection([_trace(events=[{"event_type": "error"}])])
    count = await _reconcile(storage, collection, {"task_status": "running", "current_run_id": "run-1"})
    assert count == 1
    assert collection.update["$set"]["reconciled_reason"] == "heartbeat_timeout_with_terminal_event"
    assert collection.update["$set"]["status"] == "error"


@pytest.mark.asyncio
async def test_heartbeat_lookup_failure_fails_closed() -> None:
    storage = TraceStorage()
    collection = _TraceCollection([_trace()])

    async def _heartbeat_failure(_run_id: str) -> bool:
        raise ConnectionError("redis unavailable")

    storage._collection = collection
    count = await storage.reconcile_stale_running_traces(
        session_collection=_SessionCollection({"task_status": "running", "current_run_id": "run-1"}),
        heartbeat_check=_heartbeat_failure,
        grace_seconds=60,
    )
    assert count == 0
    assert collection.update_query is None
    assert storage.stale_recovery_metrics["failures"] == 1


@pytest.mark.asyncio
async def test_cas_race_is_visible_and_does_not_report_reconciled() -> None:
    storage = TraceStorage()
    collection = _TraceCollection([_trace()], modified_count=0)
    count = await _reconcile(storage, collection, {"task_status": "completed", "current_run_id": "run-1"})
    assert count == 0
    assert storage.stale_recovery_metrics["cas_conflicts"] == 1


@pytest.mark.asyncio
async def test_recovery_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRACE_STALE_RECOVERY_ENABLED", False)
    storage = TraceStorage()
    collection = _TraceCollection([_trace()])
    count = await _reconcile(storage, collection, {"task_status": "completed", "current_run_id": "run-1"})
    assert count == 0
    assert collection.find_query is None
