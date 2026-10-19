"""Late fires still run (misfire grace) and every replica derives the same dedupe slot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.infra.scheduler.runner import build_slot_id
from src.infra.scheduler.schedule_spec import build_trigger
from src.infra.scheduler.service import (
    MISFIRE_GRACE_SECONDS,
    job_id_for,
    scheduled_fire_time,
)
from src.kernel.schemas.scheduled_task import ScheduledTask, ScheduledTaskCreate, ScheduleSpec

DUE = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)  # 09:00 Asia/Shanghai


def _daily_task() -> ScheduledTask:
    return ScheduledTask.model_validate(
        {
            "_id": "t1",
            "owner_id": "u1",
            "name": "n",
            "prompt": "p",
            "schedule": {"kind": "daily", "time": "09:00"},
        }
    )


@pytest.mark.parametrize("delay", [0.2, 45, 90, MISFIRE_GRACE_SECONDS - 1])
def test_fire_time_is_the_trigger_time_even_when_late(delay: float) -> None:
    trigger = build_trigger(ScheduleSpec(kind="daily", time="09:00"), "Asia/Shanghai")
    assert scheduled_fire_time(trigger, now=DUE + timedelta(seconds=delay)) == DUE


def test_replicas_firing_on_both_sides_of_a_minute_share_the_slot() -> None:
    task = _daily_task()
    trigger = build_trigger(task.schedule, task.timezone)
    on_time = scheduled_fire_time(trigger, now=DUE + timedelta(seconds=0.5))
    late = scheduled_fire_time(trigger, now=DUE + timedelta(seconds=90))
    assert build_slot_id(task, on_time) == build_slot_id(task, late)


def test_fire_time_falls_back_to_now_when_nothing_is_due() -> None:
    trigger = build_trigger(ScheduleSpec(kind="daily", time="09:00"), "Asia/Shanghai")
    now = DUE - timedelta(hours=2)
    assert scheduled_fire_time(trigger, now=now) == now


def test_fire_time_for_one_shot() -> None:
    trigger = build_trigger(
        ScheduleSpec(kind="once", run_at=datetime(2099, 1, 1, 8, 0)), "Asia/Shanghai"
    )
    due = datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert scheduled_fire_time(trigger, now=due + timedelta(seconds=30)) == due


@pytest.mark.asyncio
async def test_user_task_jobs_get_misfire_grace(fakes) -> None:
    task = await fakes.service.create_task(
        ScheduledTaskCreate(name="n", prompt="p", schedule={"kind": "daily", "time": "09:00"}),
        "u1",
    )
    assert fakes.scheduler._jobs[job_id_for(task.id)].misfire_grace_time == MISFIRE_GRACE_SECONDS
