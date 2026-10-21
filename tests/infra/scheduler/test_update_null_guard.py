"""An explicit null in PATCH must never reach Mongo and poison every task listing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.infra.scheduler.schedule_spec import ScheduleValidationError
from src.infra.scheduler.storage import ScheduledTaskStorage, _dump
from src.kernel.schemas.scheduled_task import (
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
)

NON_NULLABLE = (
    "name",
    "prompt",
    "schedule",
    "timezone",
    "agent_id",
    "enabled",
    "max_retries",
    "timeout_seconds",
)


@pytest.mark.parametrize("field", NON_NULLABLE)
def test_update_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        ScheduledTaskUpdate.model_validate({field: None})


def test_update_still_accepts_null_for_clearable_fields() -> None:
    assert ScheduledTaskUpdate.model_validate({"persona_preset_id": None}).persona_preset_id is None
    assert ScheduledTaskUpdate.model_validate({"model_id": None}).model_fields_set == {"model_id"}


@pytest.mark.asyncio
async def test_service_validates_merged_task_before_writing(fakes) -> None:
    task = await fakes.service.create_task(
        ScheduledTaskCreate(name="n", prompt="p", schedule={"kind": "daily", "time": "08:00"}),
        "u1",
    )
    # Bypasses the schema guard to prove the service-level check.
    bad = ScheduledTaskUpdate.model_construct(_fields_set={"timezone"}, timezone=None)

    with pytest.raises(ScheduleValidationError):
        await fakes.service.update_task(task.id, "u1", bad)
    assert fakes.storage.tasks[task.id].timezone == "Asia/Shanghai"


class _Cursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = iter(docs)

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args):
        return self

    def limit(self, *args):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._docs)
        except StopIteration:
            raise StopAsyncIteration from None


class _Collection:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def find(self, *args, **kwargs):
        return _Cursor(self.docs)

    async def count_documents(self, query):
        return len(self.docs)


@pytest.mark.asyncio
async def test_listings_skip_unreadable_documents() -> None:
    good = _dump(
        ScheduledTask.model_validate(
            {
                "_id": "good",
                "owner_id": "u1",
                "name": "n",
                "prompt": "p",
                "schedule": {"kind": "daily", "time": "08:00"},
            }
        )
    )
    bad = {**good, "_id": "bad", "timezone": None}
    storage = ScheduledTaskStorage()
    storage._tasks = _Collection([bad, good])

    assert [t.id for t in await storage.list_active_tasks()] == ["good"]
    assert [t.id for t in await storage.list_tasks("u1")] == ["good"]
    items, _ = await storage.list_tasks_admin()
    assert [t.id for t in items] == ["good"]
