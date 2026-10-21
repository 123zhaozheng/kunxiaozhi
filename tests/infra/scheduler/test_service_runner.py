from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from src.infra.scheduler import runner as runner_mod
from src.infra.scheduler import service as service_mod
from src.infra.scheduler.runner import ScheduledTaskRunner, build_slot_id
from src.infra.scheduler.schedule_spec import ScheduleValidationError
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    RunTrigger,
    ScheduledTaskCreate,
    ScheduledTaskStatus,
    ScheduledTaskUpdate,
)


def _create(**kw) -> ScheduledTaskCreate:
    base = {
        "name": "晨报",
        "prompt": "整理今天的重点",
        "schedule": {"kind": "weekdays", "time": "08:30"},
    }
    base.update(kw)
    return ScheduledTaskCreate(**base)


@pytest.mark.asyncio
async def test_create_registers_job_and_update_reregisters(fakes) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    job_id = service_mod.job_id_for(task.id)
    assert fakes.scheduler.has_job(job_id)
    resp = fakes.service.to_response(task)
    assert resp.schedule_text.startswith("每个工作日")
    assert resp.next_run_at is not None

    paused = await fakes.service.set_enabled(task.id, "u1", False)
    assert paused.status == ScheduledTaskStatus.PAUSED
    assert not fakes.scheduler.has_job(job_id)

    resumed = await fakes.service.update_task(
        task.id,
        "u1",
        ScheduledTaskUpdate(enabled=True, schedule={"kind": "daily", "time": "07:00"}),
    )
    assert resumed.status == ScheduledTaskStatus.ACTIVE
    assert fakes.scheduler.has_job(job_id)


@pytest.mark.asyncio
async def test_owner_isolation(fakes) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    assert await fakes.service.update_task(task.id, "u2", ScheduledTaskUpdate(name="x")) is None
    assert await fakes.service.delete_task(task.id, "u2") is False
    assert await fakes.service.run_now(task.id, "u2") is None


@pytest.mark.asyncio
async def test_quota_and_validation(fakes, monkeypatch) -> None:
    monkeypatch.setattr(service_mod, "max_tasks_per_user", lambda: 1)
    await fakes.service.create_task(_create(), "u1")
    with pytest.raises(service_mod.ScheduledTaskQuotaError, match="最多创建 1 个"):
        await fakes.service.create_task(_create(), "u1")
    with pytest.raises(ScheduleValidationError):
        await fakes.service.create_task(
            _create(schedule={"kind": "interval", "every_hours": 0}), "u2"
        )


@pytest.mark.asyncio
async def test_load_all_is_idempotent_and_drops_removed(fakes) -> None:
    t1 = await fakes.service.create_task(_create(), "u1")
    t2 = await fakes.service.create_task(_create(name="周报"), "u1")
    fresh = service_mod.ScheduledTaskService()
    assert await fresh.load_all(force=True) == 2
    before = set(fakes.scheduler.job_ids())
    assert await fresh.load_all() == 2
    assert set(fakes.scheduler.job_ids()) == before
    await fakes.storage.update_task(t2.id, {"enabled": False, "status": "paused"})
    await fresh.load_all()
    assert not fakes.scheduler.has_job(service_mod.job_id_for(t2.id))
    assert fakes.scheduler.has_job(service_mod.job_id_for(t1.id))


def _patch_execute(monkeypatch, outcomes):
    calls: list[str] = []

    async def fake_execute(self, task, run_id, session_id, attempt, started_at):
        calls.append(session_id)
        return outcomes[min(len(calls) - 1, len(outcomes) - 1)]

    async def fake_preview(self, session_id, run_id):
        return "结果摘要"

    async def fake_notify(self, *args, **kwargs):
        return None

    monkeypatch.setattr(ScheduledTaskRunner, "_execute", fake_execute)
    monkeypatch.setattr(ScheduledTaskRunner, "_load_preview", fake_preview)
    monkeypatch.setattr(ScheduledTaskRunner, "_notify", fake_notify)
    return calls


async def _drain() -> None:
    await asyncio.sleep(0)
    await runner_mod.drain_monitors(timeout=5)


@pytest.mark.asyncio
async def test_same_slot_runs_once_across_replicas(fakes, monkeypatch) -> None:
    calls = _patch_execute(monkeypatch, [(RunStatus.SUCCESS, None)])
    task = await fakes.service.create_task(_create(), "u1")
    fired = utc_now()
    a, b = ScheduledTaskRunner(), ScheduledTaskRunner()
    r1, r2 = await asyncio.gather(a.run(task.id, fired_at=fired), b.run(task.id, fired_at=fired))
    await _drain()
    assert sorted([bool(r1.get("skipped")), bool(r2.get("skipped"))]) == [False, True]
    assert len(calls) == 1
    stored = fakes.storage.tasks[task.id]
    assert stored.total_runs == 1 and stored.last_run_status == RunStatus.SUCCESS
    run = next(iter(fakes.storage.runs.values()))
    assert run["result_preview"] == "结果摘要"
    # lock released -> manual run possible
    assert not any(k.startswith("scheduler:task_lock:") for k in fakes.redis.data)


@pytest.mark.asyncio
async def test_retry_then_success(fakes, monkeypatch) -> None:
    calls = _patch_execute(monkeypatch, [(RunStatus.FAILED, "boom"), (RunStatus.SUCCESS, None)])
    task = await fakes.service.create_task(_create(max_retries=2), "u1")
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()
    assert len(calls) == 2 and calls[1].endswith("_r1")
    assert fakes.storage.tasks[task.id].consecutive_failures == 0


@pytest.mark.asyncio
async def test_auto_pause_after_consecutive_failures(fakes, monkeypatch) -> None:
    _patch_execute(monkeypatch, [(RunStatus.FAILED, "boom")])
    task = await fakes.service.create_task(_create(max_retries=0), "u1")
    for _ in range(runner_mod.AUTO_PAUSE_AFTER_FAILURES):
        await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
        await _drain()
    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.PAUSED
    assert "自动暂停" in (stored.pause_reason or "")
    assert not fakes.scheduler.has_job(service_mod.job_id_for(task.id))


@pytest.mark.asyncio
async def test_one_shot_completes_after_scheduled_run(fakes, monkeypatch) -> None:
    _patch_execute(monkeypatch, [(RunStatus.SUCCESS, None)])
    run_at = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    task = await fakes.service.create_task(
        _create(schedule={"kind": "once", "run_at": run_at.isoformat()}), "u1"
    )
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.SCHEDULE)
    await _drain()
    assert fakes.storage.tasks[task.id].status == ScheduledTaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_paused_task_skips_scheduled_but_allows_manual(fakes, monkeypatch) -> None:
    calls = _patch_execute(monkeypatch, [(RunStatus.SUCCESS, None)])
    task = await fakes.service.create_task(_create(enabled=False), "u1")
    assert (await ScheduledTaskRunner().run(task.id))["reason"] == "disabled"
    assert "run_id" in await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()
    assert len(calls) == 1


def test_slot_ids_are_minute_stable() -> None:
    from src.kernel.schemas.scheduled_task import ScheduledTask

    task = ScheduledTask.model_validate(
        {
            "_id": "t",
            "owner_id": "u",
            "name": "n",
            "prompt": "p",
            "schedule": {"kind": "daily", "time": "09:00"},
        }
    )
    base = datetime(2026, 10, 1, 1, 0, 0)
    assert build_slot_id(task, base)[0] == build_slot_id(task, base.replace(second=40))[0]
