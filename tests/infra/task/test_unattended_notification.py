"""定时运行不再重复发送 ``task:complete``（结果由定时任务自己的通知交付）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infra.logging.context import TraceContext
from src.infra.task.executor import TaskExecutor
from src.infra.task.status import TaskStatus


class _FakeWebSocketManager:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_to_user_with_broadcast(self, user_id: str, message: dict) -> int:
        self.sent.append((user_id, message))
        return 1


class _FakeSessionManager:
    async def get_session(self, session_id: str):
        return None


class _FakeHeartbeat:
    async def start(self, run_id: str, user_id: str | None = None) -> None:
        return None

    async def stop(self, run_id: str) -> None:
        return None


class _FakePresenter:
    def __init__(self, config) -> None:
        self.config = config
        self.trace_id = "trace-1"
        self.run_id = config.run_id

    async def _ensure_trace(self) -> None:
        return None

    async def emit_user_message(self, message: str, attachments=None) -> None:
        return None

    async def save_event(self, event) -> None:
        return None

    async def complete(self, status: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_context():
    TraceContext.clear_request_context()
    yield
    TraceContext.clear_request_context()


@pytest.fixture
def ws_manager(monkeypatch: pytest.MonkeyPatch) -> _FakeWebSocketManager:
    manager = _FakeWebSocketManager()
    monkeypatch.setattr("src.infra.websocket.get_connection_manager", lambda: manager)
    monkeypatch.setattr("src.infra.session.manager.SessionManager", _FakeSessionManager)
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED])
async def test_unattended_run_sends_no_task_complete(
    ws_manager: _FakeWebSocketManager, status: TaskStatus
) -> None:
    executor = TaskExecutor(storage=None, run_info={}, heartbeat_manager=None)  # type: ignore[arg-type]
    TraceContext.set_request_context(unattended=True)

    await executor._send_task_notification("session-1", "run-1", status, "user-1")

    assert ws_manager.sent == []


@pytest.mark.asyncio
async def test_ordinary_run_still_sends_task_complete(
    ws_manager: _FakeWebSocketManager,
) -> None:
    executor = TaskExecutor(storage=None, run_info={}, heartbeat_manager=None)  # type: ignore[arg-type]

    await executor._send_task_notification("session-1", "run-1", TaskStatus.COMPLETED, "user-1")

    assert len(ws_manager.sent) == 1
    user_id, message = ws_manager.sent[0]
    assert user_id == "user-1"
    assert message["type"] == "task:complete"
    assert message["data"]["run_id"] == "run-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(("unattended", "expected_sent"), [(True, 0), (False, 1)])
async def test_run_task_end_to_end_notification(
    monkeypatch: pytest.MonkeyPatch,
    ws_manager: _FakeWebSocketManager,
    unattended: bool,
    expected_sent: int,
) -> None:
    executor = TaskExecutor(
        storage=SimpleNamespace(), run_info={}, heartbeat_manager=_FakeHeartbeat()
    )

    async def _no_op(*args, **kwargs) -> None:
        return None

    async def fake_agent_executor(*args, **kwargs):
        # 真实的 agents/core/base.py 在流结束的 finally 里会 clear_request_context()，
        # 这会复位 ContextVar；其后的 task:complete 判断不能依赖它。
        try:
            if False:
                yield None
        finally:
            TraceContext.clear_request_context()

    monkeypatch.setattr("src.infra.writer.present.Presenter", _FakePresenter)
    monkeypatch.setattr(
        "src.infra.writer.present.PresenterConfig", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(executor, "_update_session_status", _no_op)
    monkeypatch.setattr(executor, "_expire_terminal_stream", _no_op)
    monkeypatch.setattr("src.infra.task.executor.get_dual_writer", lambda: SimpleNamespace())
    monkeypatch.setattr("src.infra.task.cancellation.TaskCancellation.clear_interrupt", _no_op)

    await executor.run_task(
        "session-1",
        "run-1",
        "fast",
        "hello",
        "user-1",
        fake_agent_executor,
        unattended=unattended,
    )

    assert len(ws_manager.sent) == expected_sent
