"""Tests for atomic hourly usage writes and snapshot backfill."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

from src.infra.analytics.usage_hourly import UsageHourlyStorage
from src.infra.analytics.usage_hourly_backfill import UsageHourlyBackfillWorker

UTC = timezone.utc
BUCKET = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [deepcopy(document) for document in self._documents]


class _Collection:
    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = documents or []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        self.bulk_calls: list[tuple[list[Any], bool]] = []
        self.index_calls: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.find_calls = 0

    @staticmethod
    def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
        for field, expected in query.items():
            actual = document.get(field)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                    return False
                if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                    return False
            elif actual != expected:
                return False
        return True

    async def create_index(self, keys, **kwargs):
        self.index_calls.append((keys, kwargs))
        return kwargs.get("name", "index")

    async def update_one(self, query, update, **kwargs):
        self.update_calls.append((query, update, kwargs))
        existing = next((doc for doc in self.documents if self._matches(doc, query)), None)
        if existing is None:
            if not kwargs.get("upsert"):
                return None
            existing = deepcopy(query)
            self.documents.append(existing)
            self._apply(existing, update, insert=True)
        else:
            self._apply(existing, update, insert=False)
        return None

    async def bulk_write(self, operations, ordered=True):
        operations = list(operations)
        self.bulk_calls.append((operations, ordered))
        for operation in operations:
            await self.update_one(operation._filter, operation._doc, upsert=operation._upsert)
        return None

    def find(self, query):
        self.find_calls += 1
        return _Cursor([doc for doc in self.documents if self._matches(doc, query)])

    async def find_one(self, query, sort=None, projection=None):
        matches = [doc for doc in self.documents if self._matches(doc, query)]
        if sort and matches:
            field, direction = sort[0]
            matches.sort(key=lambda doc: doc.get(field, ""), reverse=direction < 0)
        if not matches:
            return None
        return deepcopy(matches[0])

    @staticmethod
    def _apply(document: dict[str, Any], update: dict[str, Any], *, insert: bool) -> None:
        if insert:
            for field, value in update.get("$setOnInsert", {}).items():
                document[field] = deepcopy(value)
        for field, value in update.get("$inc", {}).items():
            document[field] = document.get(field, 0) + value
        for field, value in update.get("$set", {}).items():
            document[field] = deepcopy(value)


class _Redis:
    async def set(self, *args, **kwargs):
        return True

    async def eval(self, *args, **kwargs):
        return 1

    async def aclose(self):
        return None


class _Database:
    def __init__(self, snapshot: _Collection, state: _Collection, usage: _Collection) -> None:
        self.collections = {
            "analytics_daily_snapshot": snapshot,
            "analytics_usage_hourly_backfill_state": state,
            "usage_hourly": usage,
        }
        self.traces_accessed = False

    def __getitem__(self, name: str) -> _Collection:
        if name == "traces":
            self.traces_accessed = True
            raise AssertionError("usage hourly backfill must not access traces")
        return self.collections[name]


def _snapshot(date: str, *, user_id: str = "user-1", tokens: int = 10) -> dict[str, Any]:
    return {
        "date": date,
        "user_id": user_id,
        "persona_preset_id": "persona-1",
        "agent_id": "agent-1",
        "tokens": tokens,
        "user_messages": 2,
        "new_sessions": 1,
        "active_sessions": 1,
    }


@pytest.mark.asyncio
async def test_accumulate_uses_atomic_inc_and_upsert() -> None:
    collection = _Collection()
    storage = UsageHourlyStorage(collection)

    await storage.accumulate(BUCKET, "user-1", "model-1", "persona-1", "agent-1", tokens=4)
    await storage.accumulate(
        BUCKET.replace(minute=59),
        "user-1",
        "model-1",
        "persona-1",
        "agent-1",
        tokens=6,
        user_messages=2,
        runs=1,
    )

    assert collection.documents[0]["tokens"] == 10
    assert collection.documents[0]["user_messages"] == 2
    assert collection.documents[0]["runs"] == 1
    assert collection.find_calls == 0
    assert all(call[2]["upsert"] is True for call in collection.update_calls)
    assert all("$inc" in call[1] for call in collection.update_calls)


@pytest.mark.asyncio
async def test_accumulate_many_uses_unordered_bulk_inc() -> None:
    collection = _Collection()
    storage = UsageHourlyStorage(collection)

    await storage.accumulate_many(
        [
            {"bucket": BUCKET, "user_id": "u", "model": "m", "tokens": 3},
            {"bucket": BUCKET, "user_id": "u", "model": "m", "tokens": 7},
        ]
    )

    assert collection.documents[0]["tokens"] == 10
    assert collection.bulk_calls[0][1] is False
    assert all("$inc" in operation._doc for operation in collection.bulk_calls[0][0])


@pytest.mark.asyncio
async def test_backfill_is_idempotent_when_the_same_date_is_processed_twice() -> None:
    snapshot = _Collection([_snapshot("2026-09-17", tokens=12)])
    usage = _Collection()
    state = _Collection()
    worker = UsageHourlyBackfillWorker(
        redis_client=_Redis(),
        snapshot_collection=snapshot,
        usage_collection=usage,
        state_collection=state,
        enabled=True,
        batch_days=7,
    )

    assert await worker.run_once() == 1
    state.documents.clear()
    second_worker = UsageHourlyBackfillWorker(
        redis_client=_Redis(),
        snapshot_collection=snapshot,
        usage_collection=usage,
        state_collection=state,
        enabled=True,
        batch_days=7,
    )
    assert await second_worker.run_once() == 1

    assert len(usage.documents) == 1
    assert usage.documents[0]["tokens"] == 12
    assert usage.documents[0]["user_messages"] == 2
    assert usage.documents[0]["source"] == "snapshot"
    assert usage.documents[0]["model"] is None
    assert all(
        "$setOnInsert" in operation._doc
        for operations, _ in usage.bulk_calls
        for operation in operations
    )


@pytest.mark.asyncio
async def test_snapshot_and_live_sources_are_isolated() -> None:
    collection = _Collection()
    storage = UsageHourlyStorage(collection)

    await storage.accumulate(BUCKET, "u", None, "p", "a", tokens=5)
    await storage.upsert_snapshot_rows(
        [
            {
                "bucket": BUCKET,
                "user_id": "u",
                "model": None,
                "persona_preset_id": "p",
                "agent_id": "a",
                "source": "snapshot",
                "tokens": 11,
                "user_messages": 3,
                "runs": 0,
            }
        ]
    )

    assert len(collection.documents) == 2
    assert {document["source"] for document in collection.documents} == {"live", "snapshot"}
    assert {document["tokens"] for document in collection.documents} == {5, 11}


@pytest.mark.asyncio
async def test_snapshot_complete_sentinel_is_skipped() -> None:
    snapshot = _Collection(
        [
            _snapshot("2026-09-17", user_id="__snapshot_complete__", tokens=999),
        ]
    )
    usage = _Collection()
    state = _Collection()
    worker = UsageHourlyBackfillWorker(
        redis_client=_Redis(),
        snapshot_collection=snapshot,
        usage_collection=usage,
        state_collection=state,
        enabled=True,
    )

    assert await worker.run_once() == 0
    assert usage.documents == []


@pytest.mark.asyncio
async def test_backfill_uses_snapshot_collection_and_never_traces(monkeypatch) -> None:
    snapshot = _Collection([_snapshot("2026-09-17")])
    state = _Collection()
    usage = _Collection()
    database = _Database(snapshot, state, usage)

    monkeypatch.setattr(
        "src.infra.analytics.usage_hourly_backfill.get_mongo_client",
        lambda: {"bench": database},
    )
    monkeypatch.setattr(
        "src.infra.analytics.usage_hourly_backfill.settings.MONGODB_DB",
        "bench",
        raising=False,
    )
    worker = UsageHourlyBackfillWorker(
        redis_client=_Redis(),
        usage_collection=usage,
        state_collection=state,
        enabled=True,
    )

    assert await worker.run_once() == 1
    assert database.traces_accessed is False
    assert usage.documents[0]["source"] == "snapshot"
