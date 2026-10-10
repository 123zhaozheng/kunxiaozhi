"""P6 service layer: administrator pause / resume / delete and the owner-side guards."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from src.infra.scheduler import runner as runner_mod
from src.infra.scheduler import service as service_mod
from src.infra.scheduler.schedule_spec import ScheduleValidationError
from src.infra.scheduler.service import (
    ScheduledTaskAdminPausedError,
    ScheduledTaskDisabledError,
    job_id_for,
)
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    ScheduledTaskCreate,
    ScheduledTaskStatus,
    ScheduledTaskUpdate,
    TaskRunRecord,
)

ADMIN_MESSAGE = "该任务已被管理员暂停（原因：{reason}），如需恢复请联系管理员"


def _create(**kw) -> ScheduledTaskCreate:
    base = {
        "name": "晨报",
        "prompt": "整理今天的重点",
        "schedule": {"kind": "weekdays", "time": "08:30"},
    }
    base.update(kw)
    return ScheduledTaskCreate(**base)


async def _admin_paused(fakes, reason: str = "内容不合规"):
    task = await fakes.service.create_task(_create(), "u1")
    await fakes.service.admin_pause(task.id, reason, "admin")
    return await fakes.storage.get_task(task.id)


# ── admin_pause ────────────────────────────────


@pytest.mark.asyncio
async def test_admin_pause_marks_task_and_unregisters(fakes) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    assert fakes.scheduler.has_job(job_id_for(task.id))

    paused = await fakes.service.admin_pause(task.id, "  内容不合规  ", "admin")

    assert paused.paused_by == "admin"
    assert paused.pause_reason == "管理员已暂停：内容不合规"
    assert paused.enabled is False and paused.status == ScheduledTaskStatus.PAUSED
    assert not fakes.scheduler.has_job(job_id_for(task.id))


@pytest.mark.asyncio
async def test_admin_pause_validates_reason(fakes) -> None:
    task = await fakes.service.create_task(_create(), "u1")

    with pytest.raises(ValueError, match="请填写暂停原因"):
        await fakes.service.admin_pause(task.id, "   ", "admin")
    with pytest.raises(ValueError):
        await fakes.service.admin_pause(task.id, "x" * 201, "admin")
    assert (await fakes.storage.get_task(task.id)).enabled is True
    assert await fakes.service.admin_pause(task.id, "x" * 200, "admin") is not None


@pytest.mark.asyncio
async def test_admin_pause_unknown_task_returns_none(fakes) -> None:
    assert await fakes.service.admin_pause("missing", "原因", "admin") is None


@pytest.mark.asyncio
async def test_admin_actions_write_structured_log(fakes, caplog) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    caplog.set_level(logging.INFO)

    await fakes.service.admin_pause(task.id, "原因A", "root")
    await fakes.service.admin_resume(task.id, "root")
    await fakes.service.admin_delete(task.id, "root")

    lines = [r.getMessage() for r in caplog.records if "[ScheduledTaskAdmin]" in r.getMessage()]
    assert lines == [
        f"[ScheduledTaskAdmin] action=pause task={task.id} owner=u1 actor=root reason=原因A",
        f"[ScheduledTaskAdmin] action=resume task={task.id} owner=u1 actor=root reason=",
        f"[ScheduledTaskAdmin] action=delete task={task.id} owner=u1 actor=root reason=",
    ]


# ── admin_resume / admin_delete ────────────────


@pytest.mark.asyncio
async def test_admin_resume_clears_hold_and_reregisters(fakes) -> None:
    paused = await _admin_paused(fakes)
    await fakes.storage.update_task(paused.id, {"consecutive_failures": 3})

    resumed = await fakes.service.admin_resume(paused.id, "admin")

    assert resumed.enabled is True and resumed.status == ScheduledTaskStatus.ACTIVE
    assert resumed.paused_by is None and resumed.pause_reason is None
    assert resumed.consecutive_failures == 0
    assert fakes.scheduler.has_job(job_id_for(paused.id))


@pytest.mark.asyncio
async def test_admin_resume_when_feature_off_raises(fakes, monkeypatch) -> None:
    paused = await _admin_paused(fakes)
    monkeypatch.setattr(service_mod, "settings_enabled", lambda: False)
    monkeypatch.setattr(runner_mod, "settings_enabled", lambda: False)

    with pytest.raises(ScheduledTaskDisabledError):
        await fakes.service.admin_resume(paused.id, "admin")
    assert (await fakes.storage.get_task(paused.id)).paused_by == "admin"


@pytest.mark.asyncio
async def test_admin_resume_rejects_past_one_shot(fakes) -> None:
    task = await fakes.service.create_task(
        _create(schedule={"kind": "once", "run_at": utc_now() + timedelta(days=1)}), "u1"
    )
    await fakes.service.admin_pause(task.id, "原因", "admin")
    past = (utc_now() - timedelta(days=1)).replace(tzinfo=None)
    await fakes.storage.update_task(task.id, {"schedule": {"kind": "once", "run_at": past}})

    with pytest.raises(ScheduleValidationError):
        await fakes.service.admin_resume(task.id, "admin")
    assert (await fakes.storage.get_task(task.id)).enabled is False


@pytest.mark.asyncio
async def test_admin_resume_unknown_task_returns_none(fakes) -> None:
    assert await fakes.service.admin_resume("missing", "admin") is None


@pytest.mark.asyncio
async def test_admin_delete_removes_task_runs_and_job(fakes) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    await fakes.storage.create_run(
        TaskRunRecord(_id="r1", task_id=task.id, owner_id="u1", status=RunStatus.SUCCESS)
    )

    assert await fakes.service.admin_delete(task.id, "admin") is True

    assert await fakes.storage.get_task(task.id) is None
    assert fakes.storage.runs == {}
    assert not fakes.scheduler.has_job(job_id_for(task.id))
    assert await fakes.service.admin_delete(task.id, "admin") is False


# ── owner-side guards ──────────────────────────


@pytest.mark.asyncio
async def test_owner_cannot_resume_admin_paused_task(fakes) -> None:
    paused = await _admin_paused(fakes, "内容不合规")

    with pytest.raises(ScheduledTaskAdminPausedError) as excinfo:
        await fakes.service.set_enabled(paused.id, "u1", True)

    assert str(excinfo.value) == ADMIN_MESSAGE.format(reason="内容不合规")
    assert isinstance(excinfo.value, PermissionError)
    stored = await fakes.storage.get_task(paused.id)
    assert stored.enabled is False and stored.paused_by == "admin"


@pytest.mark.asyncio
async def test_owner_update_enabled_true_is_rejected(fakes) -> None:
    paused = await _admin_paused(fakes)

    with pytest.raises(ScheduledTaskAdminPausedError):
        await fakes.service.update_task(paused.id, "u1", ScheduledTaskUpdate(enabled=True))
    # Even combined with another edit, nothing is applied.
    with pytest.raises(ScheduledTaskAdminPausedError):
        await fakes.service.update_task(
            paused.id, "u1", ScheduledTaskUpdate(name="新名字", enabled=True)
        )
    assert (await fakes.storage.get_task(paused.id)).name == "晨报"


@pytest.mark.asyncio
async def test_owner_reschedule_of_completed_admin_paused_task_is_rejected(fakes) -> None:
    paused = await _admin_paused(fakes)
    await fakes.storage.update_task(paused.id, {"status": ScheduledTaskStatus.COMPLETED.value})

    with pytest.raises(ScheduledTaskAdminPausedError):
        await fakes.service.update_task(
            paused.id,
            "u1",
            ScheduledTaskUpdate(schedule={"kind": "daily", "time": "09:00"}),
        )
    assert (await fakes.storage.get_task(paused.id)).paused_by == "admin"


@pytest.mark.asyncio
async def test_owner_run_now_is_rejected(fakes) -> None:
    paused = await _admin_paused(fakes, "停用整改")

    with pytest.raises(ScheduledTaskAdminPausedError, match="停用整改"):
        await fakes.service.run_now(paused.id, "u1")


@pytest.mark.asyncio
async def test_owner_pause_keeps_admin_hold(fakes) -> None:
    paused = await _admin_paused(fakes, "内容不合规")

    again = await fakes.service.set_enabled(paused.id, "u1", False)

    assert again.paused_by == "admin"
    assert again.pause_reason == "管理员已暂停：内容不合规"
    assert again.enabled is False


@pytest.mark.asyncio
async def test_owner_edits_without_enabling_are_allowed(fakes) -> None:
    paused = await _admin_paused(fakes)

    updated = await fakes.service.update_task(
        paused.id,
        "u1",
        ScheduledTaskUpdate(
            name="新名字", prompt="新内容", schedule={"kind": "daily", "time": "09:00"}
        ),
    )

    assert updated.name == "新名字" and updated.prompt == "新内容"
    assert updated.enabled is False and updated.paused_by == "admin"
    assert not fakes.scheduler.has_job(job_id_for(paused.id))


@pytest.mark.asyncio
async def test_user_paused_task_can_still_be_resumed(fakes) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    await fakes.service.set_enabled(task.id, "u1", False)

    resumed = await fakes.service.set_enabled(task.id, "u1", True)

    assert resumed.enabled is True and resumed.paused_by is None
