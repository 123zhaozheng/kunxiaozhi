"""定时运行标记 ``unattended``：local / arq 两条路径都要传到 TraceContext，且按次运行、不泄漏。"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.infra.logging.context import TraceContext
from src.infra.task import arq_worker
from src.infra.task.executor import TaskExecutor
from src.infra.task.manager import BackgroundTaskManager


class _FakeHeartbeat:
    async def start(self, run_id: str, user_id: str | None = None) -> None:
        return None

    async def stop(self, run_id: str) -> None:
        return None


class _FakePresenter:
    def __init__(self, config) -> None:
        self.config = config
        self.trace_id = config.trace_id or "trace-1"
        self.run_id = config.run_id or "run-1"

    async def _ensure_trace(self) -> None:
        return None

    async def emit_user_message(self, message: str, attachments=None) -> None:
        return None

    async def save_event(self, event) -> None:
        return None

    async def complete(self, status: str) -> None:
        return None


async def _no_op(*args, **kwargs) -> None:
    return None


def _make_executor(monkeypatch: pytest.MonkeyPatch) -> TaskExecutor:
    executor = TaskExecutor(
        storage=SimpleNamespace(),
        run_info={},
        heartbeat_manager=_FakeHeartbeat(),
    )
    monkeypatch.setattr("src.infra.writer.present.Presenter", _FakePresenter)
    monkeypatch.setattr(
        "src.infra.writer.present.PresenterConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(executor, "_update_session_status", _no_op)
    monkeypatch.setattr(executor, "_send_task_notification", _no_op)
    monkeypatch.setattr("src.infra.task.executor.get_dual_writer", lambda: SimpleNamespace())
    monkeypatch.setattr("src.infra.task.cancellation.TaskCancellation.clear_interrupt", _no_op)
    return executor


@pytest.fixture(autouse=True)
def _reset_context():
    TraceContext.clear()
    TraceContext.clear_request_context()
    yield
    TraceContext.clear()
    TraceContext.clear_request_context()


def test_unattended_defaults_to_false_and_none_does_not_override() -> None:
    assert TraceContext.get_request_context().unattended is False
    TraceContext.set_request_context(session_id="s", unattended=True)
    assert TraceContext.get_request_context().unattended is True
    # agents/core/base.py 再次调用 set_request_context 时不传该参数，不应覆盖标记
    TraceContext.set_request_context(session_id="s", run_id="r")
    assert TraceContext.get_request_context().unattended is True
    TraceContext.clear_request_context()
    assert TraceContext.get_request_context().unattended is False


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, False])
async def test_run_task_sets_unattended_for_the_run_and_clears_after(
    monkeypatch: pytest.MonkeyPatch, flag: bool
) -> None:
    executor = _make_executor(monkeypatch)
    seen: list[bool] = []

    async def fake_agent_executor(*args, **kwargs):
        seen.append(TraceContext.get_request_context().unattended)
        # 标记不得以任何形式进入 agent_options
        assert "unattended" not in (kwargs.get("agent_options") or {})
        if False:
            yield None

    await executor.run_task(
        session_id="s1",
        run_id="r1",
        agent_id="fast",
        message="hi",
        user_id="u1",
        executor=fake_agent_executor,
        agent_options={"model_id": "m"},
        unattended=flag,
    )

    assert seen == [flag]
    assert TraceContext.get_request_context().unattended is False


@pytest.mark.asyncio
async def test_run_task_without_argument_is_not_unattended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _make_executor(monkeypatch)
    seen: list[bool] = []

    async def fake_agent_executor(*args, **kwargs):
        seen.append(TraceContext.get_request_context().unattended)
        if False:
            yield None

    await executor.run_task("s1", "r1", "fast", "hi", "u1", fake_agent_executor)

    assert seen == [False]


@pytest.mark.asyncio
async def test_run_task_resets_flag_inherited_from_a_copied_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """复制出的上下文带着 True 时，普通运行必须显式覆盖为 False。"""
    executor = _make_executor(monkeypatch)
    seen: list[bool] = []

    async def fake_agent_executor(*args, **kwargs):
        seen.append(TraceContext.get_request_context().unattended)
        if False:
            yield None

    TraceContext.set_request_context(unattended=True)
    await asyncio.create_task(
        executor.run_task("s1", "r1", "fast", "hi", "u1", fake_agent_executor)
    )

    assert seen == [False]


@pytest.mark.asyncio
async def test_unattended_flag_reaches_nested_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent 图在 run_task 协程内派生的子任务（复制上下文）同样能读到标记。"""
    executor = _make_executor(monkeypatch)
    seen: list[bool] = []

    async def fake_agent_executor(*args, **kwargs):
        async def node() -> bool:
            return TraceContext.get_request_context().unattended

        seen.append(await asyncio.create_task(node()))
        if False:
            yield None

    await executor.run_task("s1", "r1", "fast", "hi", "u1", fake_agent_executor, unattended=True)

    assert seen == [True]


# ── TaskManager.submit (local) ───────────────────────────────────────────────


class _RecordingExecutor:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    async def ensure_session(self, *args, **kwargs) -> None:
        return None

    async def _update_session_status(self, *args, **kwargs) -> None:
        return None

    async def run_task(self, *args, **kwargs) -> None:
        self.run_calls.append({"args": args, **kwargs})


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, False])
async def test_manager_submit_passes_unattended_to_run_task(flag: bool) -> None:
    manager = BackgroundTaskManager()
    recorder = _RecordingExecutor()
    manager._executor = cast(Any, recorder)
    manager._release_concurrency = _no_op  # type: ignore[method-assign]

    kwargs: dict[str, Any] = {"unattended": True} if flag else {}
    run_id, _ = await manager.submit(
        session_id="s1",
        agent_id="fast",
        message="hi",
        user_id="u1",
        executor=lambda *a, **k: None,
        run_id="r1",
        agent_options={"model_id": "m"},
        **kwargs,
    )
    await asyncio.gather(*list(manager._tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)

    assert recorder.run_calls[0]["unattended"] is flag
    # run_task(session_id, run_id, agent_id, message, user_id, executor, disabled_tools, agent_options, ...)
    submitted_options = recorder.run_calls[0]["args"][7]
    assert submitted_options == {"model_id": "m"}
    assert "unattended" not in submitted_options


def test_submit_signatures_default_to_false() -> None:
    for fn in (BackgroundTaskManager.submit, BackgroundTaskManager.submit_arq):
        assert inspect.signature(fn).parameters["unattended"].default is False
    assert inspect.signature(TaskExecutor.run_task).parameters["unattended"].default is False


# ── arq path ─────────────────────────────────────────────────────────────────


class _FakePayloadStore:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload
        self.saved: list[tuple[str, dict]] = []

    async def save(self, run_id: str, payload: dict) -> None:
        self.saved.append((run_id, payload))

    async def load(self, run_id: str):
        return self.payload

    async def delete(self, run_id: str) -> bool:
        return True


class _FakeArqPool:
    async def enqueue_job(self, function: str, *args, **kwargs):
        return SimpleNamespace(job_id="job-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, False])
async def test_submit_arq_writes_unattended_to_payload(flag: bool) -> None:
    manager = BackgroundTaskManager()
    manager._executor = cast(Any, _RecordingExecutor())
    store = _FakePayloadStore()

    kwargs: dict[str, Any] = {"unattended": True} if flag else {}
    await manager.submit_arq(
        session_id="s1",
        agent_id="fast",
        message="hi",
        user_id="u1",
        executor_key="agent_stream",
        payload_store=cast(Any, store),
        arq_pool=_FakeArqPool(),
        run_id="r1",
        agent_options={"model_id": "m"},
        **kwargs,
    )

    payload = store.saved[0][1]
    assert payload["unattended"] is flag
    assert "unattended" not in payload["agent_options"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload_value", "expected"),
    [(True, True), (False, False), (None, False)],
)
async def test_run_agent_task_passes_payload_unattended_to_run_task(
    monkeypatch: pytest.MonkeyPatch, payload_value: bool | None, expected: bool
) -> None:
    payload: dict[str, Any] = {
        "session_id": "s1",
        "run_id": "r1",
        "trace_id": "t1",
        "agent_id": "fast",
        "message": "hi",
        "user_id": "u1",
        "executor_key": "agent_stream",
        "agent_options": {"model_id": "m"},
    }
    if payload_value is not None:
        payload["unattended"] = payload_value
    recorder = _RecordingExecutor()
    task_manager = SimpleNamespace(_run_info={}, _ensure_executor=lambda: recorder)

    async def _executor_fn(*args, **kwargs):
        if False:
            yield None

    monkeypatch.setattr(arq_worker, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(arq_worker, "get_registered_executor", lambda key: _executor_fn)

    await arq_worker.run_agent_task({"payload_store": _FakePayloadStore(payload)}, "r1")

    assert recorder.run_calls[0]["unattended"] is expected
    assert "unattended" not in recorder.run_calls[0]["agent_options"]
