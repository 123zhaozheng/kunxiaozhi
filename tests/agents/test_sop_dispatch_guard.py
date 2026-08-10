"""SopDispatchGuardMiddleware 测试：执行节点前未置 running → 提醒；已置则放行。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from src.agents.team_agent.sop.guard import SopDispatchGuardMiddleware


def _request(name: str, args: dict, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": call_id})


class _FakePlan:
    def __init__(self, status: str, steps: list) -> None:
        self.status = status
        self.steps = steps


class _FakeStep:
    def __init__(self, step_id: str, assignee: str, status: str, title: str = "S") -> None:
        self.step_id = step_id
        self.assignee = assignee
        self.status = status
        self.title = title


@pytest.mark.asyncio
async def test_blocks_task_when_step_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _FakePlan("running", [_FakeStep("s1", "team-researcher", "pending")])
    store = AsyncMock()
    store.get_plan = AsyncMock(return_value=plan)
    monkeypatch.setattr(
        "src.agents.team_agent.sop.guard.SopRunStore", lambda: store
    )
    guard = SopDispatchGuardMiddleware(session_id="s", team_id="t")
    handler = AsyncMock(return_value="dispatched")

    result = await guard.awrap_tool_call(
        _request("task", {"subagent_type": "team-researcher"}), handler
    )

    assert isinstance(result, ToolMessage)
    assert "running" in result.content
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_allows_task_when_step_running(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _FakePlan("running", [_FakeStep("s1", "team-researcher", "running")])
    store = AsyncMock()
    store.get_plan = AsyncMock(return_value=plan)
    monkeypatch.setattr("src.agents.team_agent.sop.guard.SopRunStore", lambda: store)
    guard = SopDispatchGuardMiddleware(session_id="s", team_id="t")
    handler = AsyncMock(return_value="dispatched")

    result = await guard.awrap_tool_call(
        _request("task", {"subagent_type": "team-researcher"}), handler
    )

    assert result == "dispatched"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_blocks_when_no_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AsyncMock()
    store.get_plan = AsyncMock(return_value=None)
    monkeypatch.setattr("src.agents.team_agent.sop.guard.SopRunStore", lambda: store)
    guard = SopDispatchGuardMiddleware(session_id="s", team_id="t")
    handler = AsyncMock(return_value="dispatched")

    result = await guard.awrap_tool_call(
        _request("task", {"subagent_type": "team-researcher"}), handler
    )
    assert isinstance(result, ToolMessage)
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_blocks_when_plan_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _FakePlan("awaiting_confirmation", [_FakeStep("s1", "team-researcher", "pending")])
    store = AsyncMock()
    store.get_plan = AsyncMock(return_value=plan)
    monkeypatch.setattr("src.agents.team_agent.sop.guard.SopRunStore", lambda: store)
    guard = SopDispatchGuardMiddleware(session_id="s", team_id="t")
    handler = AsyncMock(return_value="dispatched")

    result = await guard.awrap_tool_call(
        _request("task", {"subagent_type": "team-researcher"}), handler
    )
    assert isinstance(result, ToolMessage)
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_passes_through_non_task_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    store = AsyncMock()
    store.get_plan = AsyncMock(return_value=None)
    monkeypatch.setattr("src.agents.team_agent.sop.guard.SopRunStore", lambda: store)
    guard = SopDispatchGuardMiddleware(session_id="s", team_id="t")
    handler = AsyncMock(return_value="result")

    result = await guard.awrap_tool_call(_request("read_file", {"path": "/x"}), handler)
    assert result == "result"
    handler.assert_called_once()
