"""once.run_at keeps its wall-clock time through the Mongo round-trip (tz_aware=True, UTC)."""

from __future__ import annotations

from datetime import datetime, timezone

import bson
import pytest
from bson.codec_options import CodecOptions

from src.infra.scheduler.schedule_spec import (
    build_trigger,
    describe,
    to_display_spec,
    to_storage_spec,
)
from src.infra.scheduler.storage import _dump
from src.kernel.schemas.scheduled_task import (
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
    ScheduleSpec,
)

# Same codec options as get_mongo_client().
MONGO_CODEC = CodecOptions(tz_aware=True, tzinfo=timezone.utc)
ONCE = {"kind": "once", "run_at": "2099-10-20T09:00:00"}
DUE_UTC = datetime(2099, 10, 20, 1, 0, tzinfo=timezone.utc)


def _mongo_roundtrip(task: ScheduledTask) -> ScheduledTask:
    raw = bson.encode(_dump(task))
    return ScheduledTask.model_validate(bson.decode(raw, codec_options=MONGO_CODEC))


def test_storage_and_display_spec_helpers() -> None:
    spec = ScheduleSpec.model_validate(ONCE)
    stored = to_storage_spec(spec, "Asia/Shanghai")
    assert stored.run_at == DUE_UTC and stored.run_at.tzinfo is not None
    assert to_storage_spec(stored, "Asia/Shanghai") == stored

    shown = to_display_spec(stored, "Asia/Shanghai")
    assert shown.run_at == datetime(2099, 10, 20, 9, 0)
    assert shown.run_at.tzinfo is None

    daily = ScheduleSpec(kind="daily", time="09:00")
    assert to_storage_spec(daily, "Asia/Shanghai") is daily
    assert to_display_spec(daily, "Asia/Shanghai") is daily


@pytest.mark.asyncio
async def test_created_once_task_fires_at_local_time_after_reload(fakes) -> None:
    task = await fakes.service.create_task(
        ScheduledTaskCreate(name="n", prompt="p", schedule=ONCE), "u1"
    )
    reloaded = _mongo_roundtrip(fakes.storage.tasks[task.id])

    assert build_trigger(reloaded.schedule, reloaded.timezone).run_date == DUE_UTC
    assert describe(reloaded.schedule, reloaded.timezone) == "仅一次：2099-10-20 09:00"
    response = fakes.service.to_response(reloaded)
    assert response.model_dump(mode="json")["schedule"]["run_at"] == "2099-10-20T09:00:00"
    assert response.next_run_at == DUE_UTC


@pytest.mark.asyncio
async def test_rescheduled_once_task_is_stored_in_utc(fakes) -> None:
    task = await fakes.service.create_task(
        ScheduledTaskCreate(name="n", prompt="p", schedule={"kind": "daily", "time": "08:00"}),
        "u1",
    )
    updated = await fakes.service.update_task(task.id, "u1", ScheduledTaskUpdate(schedule=ONCE))

    reloaded = _mongo_roundtrip(updated)
    assert build_trigger(reloaded.schedule, reloaded.timezone).run_date == DUE_UTC
