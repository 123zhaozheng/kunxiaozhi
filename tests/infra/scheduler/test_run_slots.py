"""P4: global concurrent-run slots, queueing, busy-skip semantics."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest

from src.infra.scheduler import locks
from src.infra.scheduler import runner as runner_mod
from src.infra.scheduler.runner import (
    BUSY_ONESHOT_PAUSE_REASON,
    BUSY_SKIPPED_MESSAGE,
    ScheduledTaskRunner,
    max_concurrent_runs,
    queue_timeout_seconds,
)
from src.infra.scheduler.storage import ScheduledTaskStorage
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    RunTrigger,
    ScheduledTaskCreate,
    ScheduledTaskStatus,
)

FAR_FUTURE = 4_000_000_000.0  # an external member that never expires during a test


def _create(**kw) -> ScheduledTaskCreate:
    base = {
        "name": "晨报",
        "prompt": "整理今天的重点",
        "schedule": {"kind": "weekdays", "time": "08:30"},
        "max_retries": 0,
    }
    base.update(kw)
    return ScheduledTaskCreate(**base)


async def _drain() -> None:
    await asyncio.sleep(0)
    await runner_mod.drain_monitors(timeout=5)


# ── locks ──────────────────────────────────────


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    state = {"now": 1000.0}
    monkeypatch.setattr(locks, "_now", lambda: state["now"])
    return state


@pytest.mark.asyncio
async def test_acquire_stops_at_limit_and_release_frees_a_slot(fakes, clock) -> None:
    assert await locks.acquire_run_slot("a", 2, 600) is True
    assert await locks.acquire_run_slot("b", 2, 600) is True
    assert await locks.acquire_run_slot("c", 2, 600) is False

    await locks.release_run_slot("a")
    assert await locks.acquire_run_slot("c", 2, 600) is True
    assert set(fakes.redis.zsets[locks.RUN_SLOTS_KEY]) == {"b", "c"}


@pytest.mark.asyncio
async def test_reacquire_same_member_refreshes_score(fakes, clock) -> None:
    assert await locks.acquire_run_slot("a", 1, 600) is True
    clock["now"] = 1100.0
    assert await locks.acquire_run_slot("a", 1, 600) is True
    assert fakes.redis.zsets[locks.RUN_SLOTS_KEY] == {"a": 1700.0}


@pytest.mark.asyncio
async def test_expired_members_are_reclaimed(fakes, clock) -> None:
    assert await locks.acquire_run_slot("a", 1, 100) is True
    assert await locks.acquire_run_slot("b", 1, 100) is False

    clock["now"] = 1000.0 + 100.0  # a's score (1100) is now <= now
    assert await locks.acquire_run_slot("b", 1, 100) is True
    assert set(fakes.redis.zsets[locks.RUN_SLOTS_KEY]) == {"b"}


@pytest.mark.asyncio
async def test_ttl_has_a_floor_of_sixty_seconds(fakes, clock) -> None:
    await locks.acquire_run_slot("a", 5, 1)
    assert fakes.redis.zsets[locks.RUN_SLOTS_KEY]["a"] == 1060.0


@pytest.mark.asyncio
async def test_release_swallows_redis_errors(monkeypatch) -> None:
    class Broken:
        async def zrem(self, *_a):
            raise RuntimeError("redis down")

    monkeypatch.setattr(locks, "get_redis_client", lambda: Broken())
    await locks.release_run_slot("a")  # must not raise


# ── settings helpers ───────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 10), (0, 1), (-5, 1), (1, 1), (50, 50), (100, 100), (500, 100), ("7", 7), ("x", 10)],
)
def test_max_concurrent_runs_clamps(monkeypatch, raw, expected) -> None:
    monkeypatch.setattr(settings, "SCHEDULED_TASK_MAX_CONCURRENT_RUNS", raw)
    assert max_concurrent_runs() == expected


@pytest.mark.parametrize(
    "raw,minutes",
    [(None, 20), (0, 1), (-1, 1), (5, 5), (120, 120), (999, 120), ("x", 20)],
)
def test_queue_timeout_seconds_clamps(monkeypatch, raw, minutes) -> None:
    monkeypatch.setattr(settings, "SCHEDULED_TASK_QUEUE_TIMEOUT_MINUTES", raw)
    assert queue_timeout_seconds() == minutes * 60


# ── runner ─────────────────────────────────────


@pytest.fixture
def run_env(monkeypatch: pytest.MonkeyPatch):
    """Patch _execute / preview / notify; fast polling; short queue timeout."""
    env = type("Env", (), {})()
    env.calls = []
    env.results = []  # queued (status, error) results; default SUCCESS
    env.notified = []

    async def fake_execute(self, task, run_id, session_id, attempt, started_at):
        env.calls.append((run_id, attempt))
        return env.results.pop(0) if env.results else (RunStatus.SUCCESS, None)

    async def fake_preview(self, session_id, run_id):
        return ""

    async def fake_notify(self, task, run_id, status, session_id, preview, error, paused_reason):
        env.notified.append((status, error, paused_reason))

    monkeypatch.setattr(ScheduledTaskRunner, "_execute", fake_execute)
    monkeypatch.setattr(ScheduledTaskRunner, "_load_preview", fake_preview)
    monkeypatch.setattr(ScheduledTaskRunner, "_notify", fake_notify)
    monkeypatch.setattr(runner_mod, "_slot_poll_delay", lambda: 0)
    monkeypatch.setattr(runner_mod, "queue_timeout_seconds", lambda: 0.05)
    monkeypatch.setattr(settings, "SCHEDULED_TASK_MAX_CONCURRENT_RUNS", 1)
    return env


def _occupy(fakes, member: str = "other") -> None:
    fakes.redis.zsets.setdefault(locks.RUN_SLOTS_KEY, {})[member] = FAR_FUTURE


@pytest.mark.asyncio
async def test_free_slot_runs_once_and_releases(fakes, run_env) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()

    assert len(run_env.calls) == 1
    assert fakes.redis.zsets.get(locks.RUN_SLOTS_KEY, {}) == {}
    run = next(iter(fakes.storage.runs.values()))
    assert run["status"] == RunStatus.SUCCESS
    assert run_env.notified == [(RunStatus.SUCCESS, None, None)]


@pytest.mark.asyncio
async def test_full_slots_skip_after_queue_timeout(fakes, run_env) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    await fakes.storage.update_task(task.id, {"consecutive_failures": 2})
    _occupy(fakes)

    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.SCHEDULE)
    await _drain()

    assert run_env.calls == []
    run = next(iter(fakes.storage.runs.values()))
    assert run["status"] == RunStatus.SKIPPED
    assert run["error_message"] == BUSY_SKIPPED_MESSAGE
    stored = fakes.storage.tasks[task.id]
    assert stored.consecutive_failures == 2
    assert stored.status == ScheduledTaskStatus.ACTIVE  # no auto-pause
    assert stored.total_runs == 1
    assert stored.last_run_status == RunStatus.SKIPPED
    assert not any(k.startswith("scheduler:task_lock:") for k in fakes.redis.data)
    assert set(fakes.redis.zsets[locks.RUN_SLOTS_KEY]) == {"other"}  # untouched
    assert run_env.notified == [(RunStatus.SKIPPED, BUSY_SKIPPED_MESSAGE, None)]


@pytest.mark.asyncio
async def test_run_stays_pending_while_queued(fakes, run_env, monkeypatch) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    _occupy(fakes)
    seen: list[Any] = []

    def delay() -> float:
        seen.append(next(iter(fakes.storage.runs.values()))["status"])
        return 0

    monkeypatch.setattr(runner_mod, "_slot_poll_delay", delay)
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()

    assert seen and all(s == RunStatus.PENDING for s in seen)


@pytest.mark.asyncio
async def test_slot_freed_while_queued_then_runs(fakes, run_env, monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "queue_timeout_seconds", lambda: 30)
    task = await fakes.service.create_task(_create(), "u1")
    _occupy(fakes)
    polls: list[int] = []

    def delay() -> float:
        polls.append(1)
        if len(polls) == 3:
            fakes.redis.zsets[locks.RUN_SLOTS_KEY].pop("other")
        return 0

    monkeypatch.setattr(runner_mod, "_slot_poll_delay", delay)
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()

    assert len(polls) == 3
    assert len(run_env.calls) == 1
    run = next(iter(fakes.storage.runs.values()))
    assert run["status"] == RunStatus.SUCCESS
    assert fakes.redis.zsets[locks.RUN_SLOTS_KEY] == {}


@pytest.mark.asyncio
async def test_retry_queue_timeout_keeps_first_failure(fakes, run_env, monkeypatch) -> None:
    task = await fakes.service.create_task(_create(max_retries=2), "u1")
    run_env.results = [(RunStatus.FAILED, "boom")]
    real_release = runner_mod.release_run_slot

    async def release_then_fill(member: str) -> None:
        await real_release(member)
        _occupy(fakes)  # another run grabs the slot before our retry

    monkeypatch.setattr(runner_mod, "release_run_slot", release_then_fill)
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()

    assert len(run_env.calls) == 1
    run = next(iter(fakes.storage.runs.values()))
    assert run["status"] == RunStatus.FAILED
    assert run["error_message"] == "boom"
    stored = fakes.storage.tasks[task.id]
    assert stored.last_run_status == RunStatus.FAILED
    assert stored.consecutive_failures == 1
    assert stored.last_session_id == run["session_id"]
    assert run_env.notified == [(RunStatus.FAILED, "boom", None)]


@pytest.mark.asyncio
async def test_scheduled_oneshot_skipped_is_paused_not_completed(fakes, run_env) -> None:
    run_at = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    task = await fakes.service.create_task(
        _create(schedule={"kind": "once", "run_at": run_at.isoformat()}), "u1"
    )
    _occupy(fakes)

    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.SCHEDULE)
    await _drain()

    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.PAUSED
    assert stored.status != ScheduledTaskStatus.COMPLETED
    assert stored.enabled is False
    assert (stored.paused_by, stored.pause_reason) == ("system", BUSY_ONESHOT_PAUSE_REASON)
    assert stored.last_run_status == RunStatus.SKIPPED
    assert run_env.notified == [
        (RunStatus.SKIPPED, BUSY_SKIPPED_MESSAGE, BUSY_ONESHOT_PAUSE_REASON)
    ]


@pytest.mark.asyncio
async def test_redis_failure_on_acquire_fails_open(fakes, run_env, monkeypatch) -> None:
    async def boom(member, limit, ttl):
        raise RuntimeError("redis down")

    monkeypatch.setattr(runner_mod, "acquire_run_slot", boom)
    task = await fakes.service.create_task(_create(), "u1")
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()

    assert len(run_env.calls) == 1
    run = next(iter(fakes.storage.runs.values()))
    assert run["status"] == RunStatus.SUCCESS


@pytest.mark.asyncio
async def test_task_lock_ttl_includes_queue_time(fakes, run_env, monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "queue_timeout_seconds", lambda: 600)
    captured: list[int] = []
    real = runner_mod.acquire_task_lock

    async def spy(task_id, run_id, ttl=0):
        captured.append(ttl)
        return await real(task_id, run_id, ttl=ttl)

    monkeypatch.setattr(runner_mod, "acquire_task_lock", spy)
    task = await fakes.service.create_task(_create(max_retries=2, timeout_seconds=300), "u1")
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()

    assert captured == [(task.timeout_seconds + 600) * 3 + 120]


@pytest.mark.asyncio
async def test_limit_is_reread_each_poll(fakes, run_env, monkeypatch) -> None:
    """Raising the limit mid-queue lets the waiting run through (hot update)."""
    monkeypatch.setattr(runner_mod, "queue_timeout_seconds", lambda: 30)
    task = await fakes.service.create_task(_create(), "u1")
    _occupy(fakes)
    polls: list[int] = []

    def delay() -> float:
        polls.append(1)
        if len(polls) == 2:
            monkeypatch.setattr(settings, "SCHEDULED_TASK_MAX_CONCURRENT_RUNS", 2)
        return 0

    monkeypatch.setattr(runner_mod, "_slot_poll_delay", delay)
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()

    assert len(run_env.calls) == 1


# ── storage.record_task_result ─────────────────


class _FakeTasks:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def find_one_and_update(self, query, update, return_document=None):
        self.updates.append(update)
        return None


async def _record(status: RunStatus) -> dict[str, Any]:
    storage = ScheduledTaskStorage()
    tasks = _FakeTasks()
    storage._tasks = tasks
    await storage.record_task_result(
        "t1", run_id="r1", status=status, session_id="s1", finished_at=utc_now()
    )
    return tasks.updates[0]


@pytest.mark.asyncio
async def test_storage_skipped_leaves_consecutive_failures_alone() -> None:
    update = await _record(RunStatus.SKIPPED)

    assert "consecutive_failures" not in update["$inc"]
    assert "consecutive_failures" not in update["$set"]
    assert update["$inc"]["total_runs"] == 1
    assert update["$set"]["last_run_status"] == RunStatus.SKIPPED.value
    assert update["$set"]["last_run_id"] == "r1"
    assert update["$set"]["last_session_id"] == "s1"
    assert "updated_at" in update["$set"]


@pytest.mark.asyncio
async def test_storage_success_resets_consecutive_failures() -> None:
    update = await _record(RunStatus.SUCCESS)

    assert update["$set"]["consecutive_failures"] == 0
    assert update["$inc"] == {"total_runs": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.TIMEOUT])
async def test_storage_failure_increments_consecutive_failures(status: RunStatus) -> None:
    update = await _record(status)

    assert update["$inc"] == {"total_runs": 1, "consecutive_failures": 1}
    assert "consecutive_failures" not in update["$set"]
