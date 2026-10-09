"""ScheduledTaskStorage.mark_run_interrupted：条件更新，仅处理仍为 pending/running 的运行记录。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.scheduler.storage import ScheduledTaskStorage
from src.kernel.schemas.scheduled_task import RunStatus


class _FakeRuns:
    def __init__(self, docs: dict[str, dict[str, Any]]) -> None:
        self.docs = docs
        self.filters: list[dict[str, Any]] = []

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]):
        self.filters.append(query)
        doc = self.docs.get(query["_id"])
        if doc is None or doc["status"] not in query["status"]["$in"]:
            return SimpleNamespace(modified_count=0)
        doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)


def _storage(docs: dict[str, dict[str, Any]]) -> tuple[ScheduledTaskStorage, _FakeRuns]:
    storage = ScheduledTaskStorage()
    runs = _FakeRuns(docs)
    storage._runs = runs
    return storage, runs


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.PENDING, RunStatus.RUNNING])
async def test_in_flight_run_is_marked_failed(status: RunStatus) -> None:
    storage, runs = _storage({"r1": {"status": status.value}})

    assert await storage.mark_run_interrupted("r1") is True

    assert runs.docs["r1"]["status"] == RunStatus.FAILED.value
    assert runs.docs["r1"]["error_message"] == "服务重启，本次执行被中断"
    assert runs.docs["r1"]["finished_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.TIMEOUT])
async def test_finished_run_is_left_alone(status: RunStatus) -> None:
    storage, runs = _storage({"r1": {"status": status.value}})

    assert await storage.mark_run_interrupted("r1") is False

    assert runs.docs["r1"] == {"status": status.value}


@pytest.mark.asyncio
async def test_unknown_run_returns_false() -> None:
    storage, _ = _storage({})

    assert await storage.mark_run_interrupted("missing") is False
