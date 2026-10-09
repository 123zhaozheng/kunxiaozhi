"""P1 governance: master switch, paused_by, non-retryable failures, owner re-check."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.infra.scheduler import runner as runner_mod
from src.infra.scheduler import service as service_mod
from src.infra.scheduler.runner import (
    NO_MODEL_MESSAGE,
    OWNER_UNAVAILABLE_MESSAGE,
    NonRetryableRunError,
    ScheduledTaskRunner,
)
from src.infra.scheduler.service import ScheduledTaskDisabledError, ScheduledTaskService
from src.kernel.exceptions import AuthorizationError
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    RunTrigger,
    ScheduledTaskCreate,
    ScheduledTaskStatus,
    ScheduledTaskUpdate,
)
from src.kernel.schemas.user import TokenPayload


def _create(**kw) -> ScheduledTaskCreate:
    base = {
        "name": "晨报",
        "prompt": "整理今天的重点",
        "schedule": {"kind": "weekdays", "time": "08:30"},
    }
    base.update(kw)
    return ScheduledTaskCreate(**base)


def _switch(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setattr(service_mod, "settings_enabled", lambda: enabled)
    monkeypatch.setattr(runner_mod, "settings_enabled", lambda: enabled)


def _owner(*permissions: str) -> TokenPayload:
    return TokenPayload(sub="u1", username="alice", roles=["user"], permissions=list(permissions))


FULL_PERMS = ("scheduled_task:use", "chat:write")


async def _drain() -> None:
    await asyncio.sleep(0)
    await runner_mod.drain_monitors(timeout=5)


def _stub_runner_io(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Stub preview + notification; returns the captured notification calls."""
    notified: list[tuple] = []

    async def fake_preview(self, session_id, run_id):
        return ""

    async def fake_notify(self, task, run_id, status, session_id, preview, error, paused_reason):
        notified.append((status, error, paused_reason))

    monkeypatch.setattr(ScheduledTaskRunner, "_load_preview", fake_preview)
    monkeypatch.setattr(ScheduledTaskRunner, "_notify", fake_notify)
    return notified


# ── master switch ──────────────────────────────


@pytest.mark.asyncio
async def test_load_all_re_registers_after_off_on(fakes, monkeypatch) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    job_id = service_mod.job_id_for(task.id)
    fresh = ScheduledTaskService()
    assert await fresh.load_all(force=True) == 1
    assert fakes.scheduler.has_job(job_id)

    _switch(monkeypatch, False)
    assert await fresh.load_all() == 0
    assert not fakes.scheduler.has_job(job_id)
    assert fresh._marker is None

    # Storage is unchanged while off, so only the reset marker forces re-registration.
    _switch(monkeypatch, True)
    assert await fresh.load_all() == 1
    assert fakes.scheduler.has_job(job_id)


@pytest.mark.asyncio
async def test_disabled_switch_blocks_mutations_but_allows_safe_operations(
    fakes, monkeypatch
) -> None:
    svc = fakes.service
    active = await svc.create_task(_create(), "u1")
    paused = await svc.create_task(_create(name="周报", enabled=False), "u1")
    _switch(monkeypatch, False)

    with pytest.raises(ScheduledTaskDisabledError, match="定时任务功能已被管理员关闭"):
        await svc.create_task(_create(name="新"), "u1")
    with pytest.raises(ScheduledTaskDisabledError):
        await svc.update_task(active.id, "u1", ScheduledTaskUpdate(name="改名"))
    with pytest.raises(ScheduledTaskDisabledError):
        await svc.update_task(active.id, "u1", ScheduledTaskUpdate(enabled=True))
    with pytest.raises(ScheduledTaskDisabledError):
        await svc.update_task(active.id, "u1", ScheduledTaskUpdate(name="x", enabled=False))
    with pytest.raises(ScheduledTaskDisabledError):
        await svc.set_enabled(paused.id, "u1", True)
    with pytest.raises(ScheduledTaskDisabledError):
        await svc.run_now(active.id, "u1")

    # pause-only update, set_enabled(False), get/list and delete keep working
    updated = await svc.update_task(active.id, "u1", ScheduledTaskUpdate(enabled=False))
    assert updated is not None and updated.status == ScheduledTaskStatus.PAUSED
    assert (await svc.set_enabled(paused.id, "u1", False)) is not None
    assert await fakes.storage.get_task(active.id, "u1") is not None
    assert len(await fakes.storage.list_tasks("u1")) == 2
    assert await svc.delete_task(paused.id, "u1") is True


@pytest.mark.asyncio
async def test_disabled_switch_still_hides_foreign_tasks_as_missing(fakes, monkeypatch) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    _switch(monkeypatch, False)
    assert await fakes.service.run_now(task.id, "u2") is None
    assert await fakes.service.update_task(task.id, "u2", ScheduledTaskUpdate(name="x")) is None


# ── paused_by ──────────────────────────────────


@pytest.mark.asyncio
async def test_paused_by_follows_user_pause_resume_and_auto_pause(fakes) -> None:
    svc = fakes.service
    task = await svc.create_task(_create(), "u1")
    assert task.paused_by is None and task.created_via == "web"

    paused = await svc.set_enabled(task.id, "u1", False)
    assert paused.paused_by == "user"

    resumed = await svc.set_enabled(task.id, "u1", True)
    assert resumed.paused_by is None and resumed.pause_reason is None

    via_update = await svc.update_task(task.id, "u1", ScheduledTaskUpdate(enabled=False))
    assert via_update.paused_by == "user"
    again = await svc.update_task(task.id, "u1", ScheduledTaskUpdate(enabled=True))
    assert again.paused_by is None

    await svc.auto_pause(task.id, "原因")
    stored = fakes.storage.tasks[task.id]
    assert (stored.paused_by, stored.pause_reason, stored.enabled) == ("system", "原因", False)
    assert fakes.service.to_response(stored).paused_by == "system"

    await svc.auto_pause(task.id, "管理员原因", paused_by="admin")
    assert fakes.storage.tasks[task.id].paused_by == "admin"

    revived = await svc.set_enabled(task.id, "u1", True)
    assert revived.paused_by is None


@pytest.mark.asyncio
async def test_consecutive_failures_pause_as_system(fakes, monkeypatch) -> None:
    async def fake_execute(self, *args):
        return RunStatus.FAILED, "boom"

    monkeypatch.setattr(ScheduledTaskRunner, "_execute", fake_execute)
    _stub_runner_io(monkeypatch)
    task = await fakes.service.create_task(_create(max_retries=0), "u1")
    for _ in range(runner_mod.AUTO_PAUSE_AFTER_FAILURES):
        await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
        await _drain()
    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.PAUSED
    assert stored.paused_by == "system"


# ── non-retryable failures ─────────────────────


def test_non_retryable_error_carries_message_and_pause_reason() -> None:
    err = NonRetryableRunError("失败", pause_reason="暂停")
    assert (err.message, err.pause_reason) == ("失败", "暂停")
    assert NonRetryableRunError("同文案").pause_reason == "同文案"


@pytest.mark.asyncio
async def test_monitor_does_not_retry_non_retryable_and_pauses_even_one_shot(
    fakes, monkeypatch
) -> None:
    calls: list[str] = []

    async def fake_execute(self, task, run_id, session_id, attempt, started_at):
        calls.append(session_id)
        raise NonRetryableRunError("专家已失效", pause_reason="请更换专家")

    monkeypatch.setattr(ScheduledTaskRunner, "_execute", fake_execute)
    notified = _stub_runner_io(monkeypatch)
    run_at = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    task = await fakes.service.create_task(
        _create(schedule={"kind": "once", "run_at": run_at.isoformat()}, max_retries=2), "u1"
    )
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.SCHEDULE)
    await _drain()

    assert len(calls) == 1
    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.PAUSED  # not COMPLETED
    assert stored.enabled is False
    assert (stored.paused_by, stored.pause_reason) == ("system", "请更换专家")
    assert stored.last_run_status == RunStatus.FAILED and stored.total_runs == 1
    assert not fakes.scheduler.has_job(service_mod.job_id_for(task.id))
    run = next(iter(fakes.storage.runs.values()))
    assert run["status"] == RunStatus.FAILED and run["error_message"] == "专家已失效"
    assert not any(k.startswith("scheduler:task_lock:") for k in fakes.redis.data)
    assert notified == [(RunStatus.FAILED, "专家已失效", "请更换专家")]


# ── runtime owner re-check ─────────────────────


async def _run_with_owner(fakes, monkeypatch, owner, **create_kw):
    resolved: list[str] = []

    async def fake_resolve_owner(user_id):
        resolved.append(user_id)
        return owner

    monkeypatch.setattr(runner_mod, "_resolve_owner", fake_resolve_owner)
    notified = _stub_runner_io(monkeypatch)
    task = await fakes.service.create_task(_create(max_retries=2, **create_kw), "u1")
    await ScheduledTaskRunner().run(task.id, trigger=RunTrigger.MANUAL)
    await _drain()
    return task, resolved, notified


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner",
    [
        None,  # missing or deactivated account (_resolve_owner returns None)
        _owner("chat:write"),  # lost scheduled_task:use
        _owner("scheduled_task:use"),  # lost chat:write
    ],
    ids=["no-owner", "no-use-permission", "no-chat-write"],
)
async def test_owner_recheck_pauses_without_retry(fakes, monkeypatch, owner) -> None:
    task, resolved, notified = await _run_with_owner(fakes, monkeypatch, owner)

    assert resolved == ["u1"]  # a single attempt despite max_retries=2
    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.PAUSED and stored.enabled is False
    assert stored.paused_by == "system"
    assert stored.pause_reason == OWNER_UNAVAILABLE_MESSAGE
    assert stored.last_run_status == RunStatus.FAILED
    assert notified == [(RunStatus.FAILED, OWNER_UNAVAILABLE_MESSAGE, OWNER_UNAVAILABLE_MESSAGE)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id,code,expected",
    [
        ("m1", "model_disabled", "所选模型已被停用，请编辑任务更换模型"),
        ("m1", "model_not_allowed", "你没有使用所选模型的权限，请编辑任务更换模型"),
        (None, "model_disabled", NO_MODEL_MESSAGE),
    ],
)
async def test_model_access_errors_are_non_retryable(
    fakes, monkeypatch, model_id, code, expected
) -> None:
    import src.api.routes.chat as chat_routes

    attempts: list[dict] = []

    async def deny(agent_options, user):
        attempts.append(dict(agent_options))
        raise AuthorizationError(code)

    monkeypatch.setattr(chat_routes, "validate_agent_model_access", deny)
    task, _, notified = await _run_with_owner(
        fakes, monkeypatch, _owner(*FULL_PERMS), model_id=model_id
    )

    assert len(attempts) == 1
    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.PAUSED and stored.paused_by == "system"
    assert stored.pause_reason == expected
    assert notified == [(RunStatus.FAILED, expected, expected)]


@pytest.mark.asyncio
async def test_resolve_owner_returns_none_for_missing_or_inactive_user(monkeypatch) -> None:
    from src.infra.role.storage import RoleStorage
    from src.infra.user.storage import UserStorage

    users = {
        "gone": None,
        "off": SimpleNamespace(id="off", username="o", roles=["user"], is_active=False),
        "on": SimpleNamespace(id="on", username="n", roles=["user"], is_active=True),
    }

    async def get_by_id(self, user_id):
        return users[user_id]

    async def get_by_names(self, names):
        return [SimpleNamespace(name="user", permissions=["chat:write", "scheduled_task:use"])]

    monkeypatch.setattr(UserStorage, "get_by_id", get_by_id)
    monkeypatch.setattr(RoleStorage, "get_by_names", get_by_names)

    assert await runner_mod._resolve_owner("gone") is None
    assert await runner_mod._resolve_owner("off") is None
    owner = await runner_mod._resolve_owner("on")
    assert owner is not None
    assert set(owner.permissions) == {"chat:write", "scheduled_task:use"}
