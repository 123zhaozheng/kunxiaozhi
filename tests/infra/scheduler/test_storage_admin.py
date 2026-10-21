"""ScheduledTaskStorage P6 queries: the real storage's Mongo filters (the fake storage mirrors them)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.scheduler.storage import ScheduledTaskStorage


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def sort(self, key, direction=None):
        self.calls.append(("sort", (key, direction)))
        return self

    def skip(self, n):
        self.calls.append(("skip", n))
        return self

    def limit(self, n):
        self.calls.append(("limit", n))
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _Collection:
    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self.cursor = _Cursor()
        self.deleted_count = 1
        self.indexes: list[list[tuple[str, int]]] = []

    async def count_documents(self, query):
        self.queries.append(("count", query))
        return 0

    def find(self, query):
        self.queries.append(("find", query))
        return self.cursor

    async def delete_one(self, query):
        self.deleted.append(query)
        return SimpleNamespace(deleted_count=self.deleted_count)

    async def delete_many(self, query):
        self.deleted.append(query)
        return SimpleNamespace(deleted_count=0)

    async def create_index(self, keys, **_kwargs):
        self.indexes.append(list(keys))


def _storage() -> tuple[ScheduledTaskStorage, _Collection, _Collection]:
    storage = ScheduledTaskStorage()
    tasks, runs = _Collection(), _Collection()
    storage._tasks, storage._runs = tasks, runs
    return storage, tasks, runs


@pytest.mark.asyncio
async def test_list_tasks_admin_builds_escaped_case_insensitive_filter() -> None:
    storage, tasks, _ = _storage()

    await storage.list_tasks_admin(
        skip=-3,
        limit=500,
        name_query=" a.b* ",
        owner_ids=["u1"],
        status="paused",
        last_run_status="failed",
    )

    expected = {
        "name": {"$regex": r"a\.b\*", "$options": "i"},
        "owner_id": {"$in": ["u1"]},
        "status": "paused",
        "last_run_status": "failed",
    }
    assert ("count", expected) in tasks.queries and ("find", expected) in tasks.queries
    assert tasks.cursor.calls == [("sort", ("updated_at", -1)), ("skip", 0), ("limit", 100)]


@pytest.mark.asyncio
async def test_list_tasks_admin_with_empty_owner_ids_skips_the_database() -> None:
    storage, tasks, _ = _storage()

    assert await storage.list_tasks_admin(owner_ids=[]) == ([], 0)
    assert tasks.queries == []


@pytest.mark.asyncio
async def test_list_tasks_admin_without_filters_matches_everything() -> None:
    storage, tasks, _ = _storage()

    await storage.list_tasks_admin(limit=0)

    assert ("find", {}) in tasks.queries
    assert ("limit", 1) in tasks.cursor.calls


@pytest.mark.asyncio
async def test_delete_task_owner_is_optional_and_runs_follow() -> None:
    storage, tasks, runs = _storage()

    assert await storage.delete_task("t1") is True
    assert tasks.deleted == [{"_id": "t1"}] and runs.deleted == [{"task_id": "t1"}]

    tasks.deleted.clear()
    runs.deleted.clear()
    assert await storage.delete_task("t1", "u1") is True
    assert tasks.deleted == [{"_id": "t1", "owner_id": "u1"}]

    tasks.deleted_count = 0
    runs.deleted.clear()
    assert await storage.delete_task("t1") is False
    assert runs.deleted == []


@pytest.mark.asyncio
async def test_list_runs_filters_by_owner_only_when_given() -> None:
    storage, _, runs = _storage()

    await storage.list_runs("t1")
    await storage.list_runs("t1", "u1")

    queries = [q for kind, q in runs.queries if kind == "find"]
    assert queries == [{"task_id": "t1"}, {"task_id": "t1", "owner_id": "u1"}]


@pytest.mark.asyncio
async def test_ensure_indexes_adds_admin_filter_indexes() -> None:
    storage, tasks, _ = _storage()

    await storage.ensure_indexes()

    assert [("status", 1), ("updated_at", -1)] in tasks.indexes
    assert [("last_run_status", 1), ("updated_at", -1)] in tasks.indexes
