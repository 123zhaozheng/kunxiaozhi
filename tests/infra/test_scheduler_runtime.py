from __future__ import annotations

from typing import Any

import pytest

from src.infra.scheduler.runtime import RuntimeScheduler, ScheduledJob


class _FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id


class _FakeAsyncIOScheduler:
    instances: list["_FakeAsyncIOScheduler"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.added_jobs: list[dict[str, Any]] = []
        self.rescheduled_jobs: list[dict[str, Any]] = []
        self.started = False
        self.shutdown_wait: bool | None = None
        self.running = False
        _FakeAsyncIOScheduler.instances.append(self)

    def add_job(self, func: Any, trigger: Any, **kwargs: Any) -> _FakeJob:
        self.added_jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})
        return _FakeJob(str(kwargs["id"]))

    def reschedule_job(self, job_id: str, trigger: Any) -> None:
        self.rescheduled_jobs.append({"id": job_id, "trigger": trigger})

    def start(self) -> None:
        self.started = True
        self.running = True

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_wait = wait
        self.running = False


@pytest.fixture(autouse=True)
def reset_fake_scheduler() -> None:
    _FakeAsyncIOScheduler.instances.clear()


def test_start_registers_interval_job_with_apscheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.scheduler import runtime as scheduler_module

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _FakeAsyncIOScheduler)
    runtime_scheduler = RuntimeScheduler()

    async def handler() -> None:
        return None

    runtime_scheduler.register_interval_job(
        ScheduledJob(id="memory.compaction", interval_seconds=lambda: 30, handler=handler)
    )

    runtime_scheduler.start()

    fake = _FakeAsyncIOScheduler.instances[-1]
    assert fake.started is True
    assert len(fake.added_jobs) == 1
    added = fake.added_jobs[0]
    assert added["kwargs"]["id"] == "memory.compaction"
    assert added["kwargs"]["replace_existing"] is True
    assert added["kwargs"]["coalesce"] is True
    assert added["kwargs"]["max_instances"] == 1
    assert added["trigger"].interval.total_seconds() == 30
    assert "next_run_time" not in added["kwargs"]


def test_start_can_register_job_to_run_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.scheduler import runtime as scheduler_module

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _FakeAsyncIOScheduler)
    runtime_scheduler = RuntimeScheduler()

    async def handler() -> None:
        return None

    runtime_scheduler.register_interval_job(
        ScheduledJob(
            id="memory.compaction",
            interval_seconds=30,
            handler=handler,
            run_on_start=True,
        )
    )

    runtime_scheduler.start()

    added = _FakeAsyncIOScheduler.instances[-1].added_jobs[0]
    assert added["kwargs"]["id"] == "memory.compaction"
    assert added["kwargs"]["next_run_time"] is not None


@pytest.mark.asyncio
async def test_run_job_now_skips_disabled_jobs() -> None:
    calls = 0
    runtime_scheduler = RuntimeScheduler()

    async def handler() -> None:
        nonlocal calls
        calls += 1

    runtime_scheduler.register_interval_job(
        ScheduledJob(
            id="disabled.job",
            interval_seconds=60,
            handler=handler,
            enabled=lambda: False,
        )
    )

    result = await runtime_scheduler.run_job_now("disabled.job")

    assert result == {"skipped": True, "reason": "disabled"}
    assert calls == 0


@pytest.mark.asyncio
async def test_job_refreshes_interval_when_config_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.scheduler import runtime as scheduler_module

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _FakeAsyncIOScheduler)
    seconds = 60
    runtime_scheduler = RuntimeScheduler()

    async def handler() -> None:
        return None

    runtime_scheduler.register_interval_job(
        ScheduledJob(
            id="dynamic.job",
            interval_seconds=lambda: seconds,
            handler=handler,
        )
    )
    runtime_scheduler.start()
    seconds = 120

    await runtime_scheduler.run_job_now("dynamic.job")

    fake = _FakeAsyncIOScheduler.instances[-1]
    assert fake.rescheduled_jobs[-1]["id"] == "dynamic.job"
    assert fake.rescheduled_jobs[-1]["trigger"].interval.total_seconds() == 120


@pytest.mark.asyncio
async def test_stop_shuts_down_apscheduler_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.scheduler import runtime as scheduler_module

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _FakeAsyncIOScheduler)
    runtime_scheduler = RuntimeScheduler()
    runtime_scheduler.start()

    await runtime_scheduler.stop()

    fake = _FakeAsyncIOScheduler.instances[-1]
    assert fake.shutdown_wait is False


def test_misfire_grace_is_passed_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from apscheduler.triggers.date import DateTrigger

    import src.infra.scheduler.runtime as scheduler_module

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", _FakeAsyncIOScheduler)

    async def handler() -> None:
        return None

    trigger = DateTrigger(run_date="2099-01-01 00:00:00", timezone="UTC")
    scheduler = RuntimeScheduler()
    scheduler.register_job(
        ScheduledJob(id="graceful", handler=handler, trigger=trigger, misfire_grace_time=300)
    )
    scheduler.register_job(ScheduledJob(id="default", handler=handler, trigger=trigger))
    scheduler.start()

    jobs = _FakeAsyncIOScheduler.instances[-1].added_jobs
    added = {job["kwargs"]["id"]: job["kwargs"] for job in jobs}
    assert added["graceful"]["misfire_grace_time"] == 300
    assert "misfire_grace_time" not in added["default"]


@pytest.mark.asyncio
async def test_late_trigger_job_still_runs_within_misfire_grace() -> None:
    import asyncio
    import time
    from datetime import timedelta

    from apscheduler.triggers.date import DateTrigger

    from src.infra.utils.datetime import utc_now

    fired: list[bool] = []

    async def handler() -> None:
        fired.append(True)

    scheduler = RuntimeScheduler()
    scheduler.start()
    try:
        scheduler.register_job(
            ScheduledJob(
                id="late",
                handler=handler,
                trigger=DateTrigger(run_date=utc_now() + timedelta(seconds=0.3)),
                misfire_grace_time=60,
            )
        )
        await asyncio.sleep(0.05)
        time.sleep(1.5)  # stall the loop past APScheduler's default 1s grace
        for _ in range(30):
            if fired:
                break
            await asyncio.sleep(0.1)
    finally:
        await scheduler.stop()
    assert fired
