"""定时任务运行被服务重启中断后不自动恢复；手动恢复与续聊产生的 run 仍按原逻辑恢复。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.task import recovery as recovery_module
from src.infra.task.status import TaskStatus
from src.kernel.schemas.scheduled_task import RunStatus
from tests.infra.scheduler.fakes import FakeStorage


class _FakeStorage:
    """Merges metadata updates like the real session storage."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.updates: list[dict[str, Any]] = []

    async def update(self, session_id: str, session_update) -> None:
        self.updates.append(dict(session_update.metadata))
        self.metadata.update(session_update.metadata)


class _FakeExecutor:
    def __init__(self, storage: _FakeStorage) -> None:
        self._storage = storage
        self.status_calls: list[tuple[TaskStatus, str | None]] = []

    async def _update_session_status(
        self, session_id: str, status: TaskStatus, error: str | None = None, run_id=None
    ) -> None:
        self.status_calls.append((status, error))
        self._storage.metadata.update({"task_status": status.value, "task_error": error})


class _FakeRedis:
    def __init__(self) -> None:
        self.set_calls: list[str] = []

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        self.set_calls.append(key)
        return True

    async def eval(self, *args):
        return 1


class _Env:
    pass


def _build(monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any]) -> _Env:
    env = _Env()
    env.storage = _FakeStorage()
    env.storage.metadata = dict(metadata)
    env.executor = _FakeExecutor(env.storage)
    env.redis = _FakeRedis()
    env.submit_calls = []
    env.session = SimpleNamespace(id="session-1", user_id="user-1", metadata=dict(metadata))

    async def _mark_failed(run_id: str, reason: str, session) -> None:
        await env.service.mark_run_failed(run_id, reason, session)

    env.service = recovery_module.TaskRecoveryService(
        storage=env.storage,
        run_info={},
        heartbeat=SimpleNamespace(),
        ensure_executor=lambda: env.executor,
        submit_task=lambda **kw: None,  # type: ignore[arg-type,return-value]
        mark_run_failed=_mark_failed,
    )

    async def _fake_submit_recovery_run(session, source_run_id, reason):
        env.submit_calls.append((source_run_id, reason))
        return {
            "success": True,
            "run_id": "run-new",
            "resumed_from_run_id": source_run_id,
            "message": "ok",
        }

    monkeypatch.setattr(env.service, "submit_recovery_run", _fake_submit_recovery_run)
    monkeypatch.setattr(recovery_module, "get_redis_client", lambda: env.redis)
    # mark_run_failed 里的 trace 失败标记走 try/except；这里让它立刻失败，避免连接 Mongo
    monkeypatch.setattr(
        recovery_module,
        "get_trace_storage",
        lambda: (_ for _ in ()).throw(RuntimeError("no trace storage in test")),
    )

    env.scheduler_storage = FakeStorage()
    monkeypatch.setattr(
        "src.infra.scheduler.storage.get_scheduled_task_storage", lambda: env.scheduler_storage
    )
    return env


def _seed_run(env: _Env, status: RunStatus = RunStatus.RUNNING) -> None:
    env.scheduler_storage.runs["sched-run-1"] = {"status": status}


_SCHEDULED_METADATA = {
    "current_run_id": "run-unattended",
    "unattended_run_id": "run-unattended",
    "scheduled_task_run_id": "sched-run-1",
    "task_status": "running",
}


@pytest.mark.asyncio
async def test_server_restart_does_not_resume_unattended_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _build(monkeypatch, _SCHEDULED_METADATA)
    _seed_run(env)

    result = await env.service.resume_interrupted_run(
        env.session, "run-unattended", "server_restart"
    )

    assert env.submit_calls == []
    assert result == {
        "success": False,
        "run_id": None,
        "resumed_from_run_id": "run-unattended",
        "message": "定时任务运行被中断，不自动恢复",
    }
    # 会话被标记为失败；错误码必须被覆盖为 unattended_interrupted，且仍可手动恢复
    assert env.storage.metadata["task_status"] == "failed"
    assert env.storage.metadata["task_error_code"] == "unattended_interrupted"
    assert env.storage.metadata["task_recoverable"] is True
    assert "不自动恢复" in env.storage.metadata["task_error"]
    assert env.redis.set_calls == []  # 没有进入恢复锁流程
    # 定时任务运行记录被标记为中断
    record = env.scheduler_storage.runs["sched-run-1"]
    assert record["status"] == RunStatus.FAILED
    assert record["error_message"] == "服务重启，本次执行被中断"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["server_restart", "instance_unavailable", ""])
async def test_any_non_manual_reason_skips_unattended_run(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    env = _build(monkeypatch, _SCHEDULED_METADATA)
    _seed_run(env)

    result = await env.service.resume_interrupted_run(env.session, "run-unattended", reason)

    assert result["success"] is False
    assert env.submit_calls == []


@pytest.mark.asyncio
async def test_finished_scheduler_run_record_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _build(monkeypatch, _SCHEDULED_METADATA)
    _seed_run(env, RunStatus.SUCCESS)

    await env.service.resume_interrupted_run(env.session, "run-unattended", "server_restart")

    assert env.scheduler_storage.runs["sched-run-1"]["status"] == RunStatus.SUCCESS


@pytest.mark.asyncio
async def test_scheduler_storage_failure_does_not_break_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _build(monkeypatch, _SCHEDULED_METADATA)

    def _boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr("src.infra.scheduler.storage.get_scheduled_task_storage", _boom)

    result = await env.service.resume_interrupted_run(
        env.session, "run-unattended", "server_restart"
    )

    assert result["success"] is False
    assert env.storage.metadata["task_error_code"] == "unattended_interrupted"


@pytest.mark.asyncio
async def test_unattended_run_without_scheduler_record_id_still_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {k: v for k, v in _SCHEDULED_METADATA.items() if k != "scheduled_task_run_id"}
    env = _build(monkeypatch, metadata)

    result = await env.service.resume_interrupted_run(
        env.session, "run-unattended", "server_restart"
    )

    assert result["success"] is False
    assert env.storage.metadata["task_error_code"] == "unattended_interrupted"


@pytest.mark.asyncio
async def test_manual_resume_still_resumes_unattended_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _build(monkeypatch, _SCHEDULED_METADATA)
    _seed_run(env)

    result = await env.service.resume_interrupted_run(
        env.session, "run-unattended", "manual_resume"
    )

    assert result["success"] is True
    assert env.submit_calls == [("run-unattended", "manual_resume")]
    assert env.scheduler_storage.runs["sched-run-1"]["status"] == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_continuation_run_with_different_run_id_resumes_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {**_SCHEDULED_METADATA, "current_run_id": "run-user-followup"}
    env = _build(monkeypatch, metadata)
    _seed_run(env)

    result = await env.service.resume_interrupted_run(
        env.session, "run-user-followup", "server_restart"
    )

    assert result["success"] is True
    assert env.submit_calls == [("run-user-followup", "server_restart")]
    assert env.scheduler_storage.runs["sched-run-1"]["status"] == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_ordinary_session_resumes_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build(monkeypatch, {"current_run_id": "run-1", "task_status": "running"})

    result = await env.service.resume_interrupted_run(env.session, "run-1", "server_restart")

    assert result["success"] is True
    assert env.submit_calls == [("run-1", "server_restart")]
