"""In-memory fakes for scheduled task tests (importable from other test packages)."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from src.infra.scheduler import locks, runner, service, storage
from src.infra.scheduler.runtime import RuntimeScheduler
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    ScheduledTask,
    ScheduledTaskStatus,
    TaskRunRecord,
)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def eval(self, _script: str, _n: int, key: str, token: str) -> int:
        if self.data.get(key) == token:
            del self.data[key]
            return 1
        return 0


class FakeStorage:
    def __init__(self) -> None:
        self.tasks: dict[str, ScheduledTask] = {}
        self.runs: dict[str, dict[str, Any]] = {}

    async def ensure_indexes(self) -> None:
        return None

    async def create_task(self, task: ScheduledTask) -> None:
        self.tasks[task.id] = task

    async def get_task(
        self, task_id: str, owner_id: Optional[str] = None
    ) -> Optional[ScheduledTask]:
        task = self.tasks.get(task_id)
        if task is None or (owner_id is not None and task.owner_id != owner_id):
            return None
        return task

    async def update_task(self, task_id: str, updates: dict[str, Any]) -> Optional[ScheduledTask]:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        data = task.model_dump(by_alias=True)
        data.update(updates)
        data["updated_at"] = utc_now()
        self.tasks[task_id] = ScheduledTask.model_validate(data)
        return self.tasks[task_id]

    async def delete_task(self, task_id: str, owner_id: str) -> bool:
        if await self.get_task(task_id, owner_id) is None:
            return False
        del self.tasks[task_id]
        return True

    async def count_tasks(self, owner_id: str) -> int:
        return sum(1 for t in self.tasks.values() if t.owner_id == owner_id)

    async def list_tasks(self, owner_id: str) -> list[ScheduledTask]:
        return [t for t in self.tasks.values() if t.owner_id == owner_id]

    async def list_active_tasks(self) -> list[ScheduledTask]:
        return [
            t for t in self.tasks.values() if t.enabled and t.status == ScheduledTaskStatus.ACTIVE
        ]

    async def get_active_marker(self) -> str:
        active = await self.list_active_tasks()
        stamps = sorted(str(t.updated_at) for t in self.tasks.values())
        return f"{len(active)}:{stamps[-1] if stamps else None}"

    async def record_task_result(self, task_id, *, run_id, status, session_id, finished_at):
        task = self.tasks[task_id]
        failures = 0 if status == RunStatus.SUCCESS else task.consecutive_failures + 1
        return await self.update_task(
            task_id,
            {
                "last_run_at": finished_at,
                "last_run_status": status,
                "last_run_id": run_id,
                "last_session_id": session_id,
                "total_runs": task.total_runs + 1,
                "consecutive_failures": failures,
            },
        )

    async def create_run(self, record: TaskRunRecord) -> None:
        self.runs[record.id] = record.model_dump(by_alias=True)

    async def update_run(self, run_id: str, updates: dict[str, Any]) -> None:
        self.runs[run_id].update(updates)

    async def list_runs(self, task_id, owner_id, skip=0, limit=20):
        items = [
            TaskRunRecord.model_validate(r)
            for r in self.runs.values()
            if r["task_id"] == task_id and r["owner_id"] == owner_id
        ]
        return items, len(items)

    async def mark_stale_runs_failed(self, older_than) -> int:
        return 0


def install_fakes(monkeypatch: pytest.MonkeyPatch):
    """Patch storage / redis / scheduler singletons and return the fake handles."""
    fake_storage = FakeStorage()
    fake_redis = FakeRedis()
    scheduler = RuntimeScheduler()
    for mod in (service, runner, storage):
        monkeypatch.setattr(mod, "get_scheduled_task_storage", lambda: fake_storage, raising=False)
    monkeypatch.setattr(locks, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(service, "get_runtime_scheduler", lambda: scheduler)
    svc = service.ScheduledTaskService()
    monkeypatch.setattr(service, "_service", svc)
    return type(
        "Fakes",
        (),
        {"storage": fake_storage, "redis": fake_redis, "scheduler": scheduler, "service": svc},
    )
