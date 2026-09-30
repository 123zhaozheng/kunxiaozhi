"""MongoDB persistence for scheduled tasks and their run records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pymongo import DESCENDING, ReturnDocument

from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    ScheduledTask,
    ScheduledTaskStatus,
    TaskRunRecord,
)

logger = get_logger(__name__)

TASKS_COLLECTION = "scheduled_tasks"
RUNS_COLLECTION = "scheduled_task_runs"
RUN_LIST_LIMIT_MAX = 100


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(by_alias=True, mode="python")


class ScheduledTaskStorage:
    def __init__(self) -> None:
        self._tasks = None
        self._runs = None

    @property
    def tasks(self):
        if self._tasks is None:
            self._tasks = get_mongo_client()[settings.MONGODB_DB][TASKS_COLLECTION]
        return self._tasks

    @property
    def runs(self):
        if self._runs is None:
            self._runs = get_mongo_client()[settings.MONGODB_DB][RUNS_COLLECTION]
        return self._runs

    async def ensure_indexes(self) -> None:
        await self.tasks.create_index([("owner_id", 1), ("updated_at", DESCENDING)])
        await self.tasks.create_index([("enabled", 1), ("status", 1)])
        await self.runs.create_index([("task_id", 1), ("started_at", DESCENDING)])
        await self.runs.create_index([("owner_id", 1), ("started_at", DESCENDING)])
        logger.info("[ScheduledTaskStorage] indexes ensured")

    # ── tasks ──────────────────────────────────────

    async def create_task(self, task: ScheduledTask) -> None:
        await self.tasks.insert_one(_dump(task))

    async def get_task(
        self, task_id: str, owner_id: Optional[str] = None
    ) -> Optional[ScheduledTask]:
        query: dict[str, Any] = {"_id": task_id}
        if owner_id is not None:
            query["owner_id"] = owner_id
        doc = await self.tasks.find_one(query)
        return ScheduledTask.model_validate(doc) if doc else None

    async def update_task(self, task_id: str, updates: dict[str, Any]) -> Optional[ScheduledTask]:
        updates = {**updates, "updated_at": utc_now()}
        doc = await self.tasks.find_one_and_update(
            {"_id": task_id}, {"$set": updates}, return_document=ReturnDocument.AFTER
        )
        return ScheduledTask.model_validate(doc) if doc else None

    async def delete_task(self, task_id: str, owner_id: str) -> bool:
        result = await self.tasks.delete_one({"_id": task_id, "owner_id": owner_id})
        if result.deleted_count:
            await self.runs.delete_many({"task_id": task_id})
        return bool(result.deleted_count)

    async def count_tasks(self, owner_id: str) -> int:
        return await self.tasks.count_documents({"owner_id": owner_id})

    async def list_tasks(self, owner_id: str) -> list[ScheduledTask]:
        cursor = self.tasks.find({"owner_id": owner_id}).sort("created_at", DESCENDING)
        return [ScheduledTask.model_validate(d) async for d in cursor]

    async def list_active_tasks(self) -> list[ScheduledTask]:
        cursor = self.tasks.find({"enabled": True, "status": ScheduledTaskStatus.ACTIVE.value})
        return [ScheduledTask.model_validate(d) async for d in cursor]

    async def get_active_marker(self) -> str:
        """Cheap change marker: active count + newest updated_at across all tasks."""
        count = await self.tasks.count_documents(
            {"enabled": True, "status": ScheduledTaskStatus.ACTIVE.value}
        )
        latest = await self.tasks.find_one(
            {}, sort=[("updated_at", DESCENDING)], projection={"updated_at": 1}
        )
        stamp = latest.get("updated_at") if latest else None
        return f"{count}:{stamp.isoformat() if isinstance(stamp, datetime) else stamp}"

    async def record_task_result(
        self,
        task_id: str,
        *,
        run_id: str,
        status: RunStatus,
        session_id: Optional[str],
        finished_at: datetime,
    ) -> Optional[ScheduledTask]:
        """Atomically bump run stats and the consecutive-failure counter."""
        succeeded = status == RunStatus.SUCCESS
        update: dict[str, Any] = {
            "$set": {
                "last_run_at": finished_at,
                "last_run_status": status.value,
                "last_run_id": run_id,
                "last_session_id": session_id,
                "updated_at": utc_now(),
                **({"consecutive_failures": 0} if succeeded else {}),
            },
            "$inc": {"total_runs": 1, **({} if succeeded else {"consecutive_failures": 1})},
        }
        doc = await self.tasks.find_one_and_update(
            {"_id": task_id}, update, return_document=ReturnDocument.AFTER
        )
        return ScheduledTask.model_validate(doc) if doc else None

    # ── runs ───────────────────────────────────────

    async def create_run(self, record: TaskRunRecord) -> None:
        await self.runs.insert_one(_dump(record))

    async def update_run(self, run_id: str, updates: dict[str, Any]) -> None:
        clean = {k: (v.value if hasattr(v, "value") else v) for k, v in updates.items()}
        await self.runs.update_one({"_id": run_id}, {"$set": clean})

    async def list_runs(
        self, task_id: str, owner_id: str, skip: int = 0, limit: int = 20
    ) -> tuple[list[TaskRunRecord], int]:
        limit = min(max(int(limit), 1), RUN_LIST_LIMIT_MAX)
        query = {"task_id": task_id, "owner_id": owner_id}
        total = await self.runs.count_documents(query)
        cursor = (
            self.runs.find(query).sort("started_at", DESCENDING).skip(max(0, skip)).limit(limit)
        )
        return [TaskRunRecord.model_validate(d) async for d in cursor], total

    async def mark_stale_runs_failed(self, older_than: datetime) -> int:
        """Runs left RUNNING/PENDING by a crashed replica are closed on startup."""
        result = await self.runs.update_many(
            {
                "status": {"$in": [RunStatus.PENDING.value, RunStatus.RUNNING.value]},
                "started_at": {"$lt": older_than},
            },
            {
                "$set": {
                    "status": RunStatus.FAILED.value,
                    "error_message": "服务重启，本次执行被中断",
                    "finished_at": utc_now(),
                }
            },
        )
        return int(result.modified_count or 0)


_storage: Optional[ScheduledTaskStorage] = None


def get_scheduled_task_storage() -> ScheduledTaskStorage:
    global _storage
    if _storage is None:
        _storage = ScheduledTaskStorage()
    return _storage
