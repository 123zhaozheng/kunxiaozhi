"""update_sop 工具确认门禁测试。

验证：确认前不 dispatch（返回阻塞语义 + awaiting_confirmation 状态）；approved → running；
rejected+feedback 存反馈；timeout → timed_out；已确认后调用不阻塞；校验失败返回 errors 不落库。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.agents.team_agent.sop import tool as sop_tool_module
from src.agents.team_agent.sop.schemas import SOPPlan, SOPStep, StepStatus
from src.agents.team_agent.sop.store import SopRunStore
from src.agents.team_agent.sop.tool import create_update_sop_tool
from src.infra.storage.mongodb import ApprovalResponse

_ROSTER = ["team-m1-role1", "team-m2-role2"]


def _step(step_id: str, **overrides) -> SOPStep:
    fields = {
        "step_id": step_id,
        "title": f"step {step_id}",
        "assignee": "team-m1-role1",
        "expected_output": f"output of {step_id}",
    }
    fields.update(overrides)
    return SOPStep(**fields)


def _plan(**overrides) -> SOPPlan:
    fields = {
        "plan_id": "plan-1",
        "session_id": "session-1",
        "team_id": "team-1",
        "goal": "build a report",
        "steps": [
            _step("s1"),
            _step("s2", dependencies=["s1"], assignee="team-m2-role2"),
        ],
    }
    fields.update(overrides)
    return SOPPlan(**fields)


class _FakeCollection:
    """内存版 motor collection（对齐 test_sop_store 的 mock 模式）。"""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}

    async def create_index(self, keys, **kwargs) -> None:
        return None

    async def update_one(self, query, update, upsert=False, **kwargs) -> SimpleNamespace:
        key = (query["session_id"], query["team_id"])
        if key not in self.docs:
            if not upsert:
                return SimpleNamespace(modified_count=0, matched_count=0)
            self.docs[key] = {}
        doc = self.docs[key]
        if "plan_id" in query and doc.get("plan_id") != query["plan_id"]:
            return SimpleNamespace(modified_count=0, matched_count=0)
        step_id = query.get("steps.step_id")
        step = next((item for item in doc.get("steps", []) if item.get("step_id") == step_id), None)
        if "steps.step_id" in query and step is None:
            return SimpleNamespace(modified_count=0, matched_count=0)
        for field, value in update.get("$set", {}).items():
            if field.startswith("steps.$."):
                step[field.removeprefix("steps.$.")] = value
            else:
                doc[field] = value
        return SimpleNamespace(modified_count=1, matched_count=1)

    async def find_one(self, query) -> dict | None:
        key = (query["session_id"], query["team_id"])
        doc = self.docs.get(key)
        if doc is None:
            return None
        return {**doc, "_id": "fake-sop-run-id"}


class _FakePresenter:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit_team_event(self, event_type: str, data: dict) -> dict:
        self.emitted.append((event_type, data))
        return {"event": event_type, "data": data}


class _GateHarness:
    """统一夹具：注入 fake store / presenter / 审批调用并构建工具。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = SopRunStore()
        store._collection = _FakeCollection()
        self.store = store
        self.presenter = _FakePresenter()
        self.approval_calls: list[dict] = []
        self.wait_calls: list[tuple[str, float]] = []
        self.approval_response: ApprovalResponse | None = None

        monkeypatch.setattr(sop_tool_module, "SopRunStore", lambda: store)

        async def fake_create_approval(**kwargs):
            self.approval_calls.append(kwargs)
            return SimpleNamespace(id="approval-1")

        async def fake_wait_for_response(approval_id: str, timeout: float = 300):
            self.wait_calls.append((approval_id, timeout))
            return self.approval_response

        monkeypatch.setattr(sop_tool_module, "create_approval", fake_create_approval)
        monkeypatch.setattr(sop_tool_module, "wait_for_response", fake_wait_for_response)

        self.tool = create_update_sop_tool(
            session_id="session-1",
            user_id="user-1",
            roster_subagent_types=_ROSTER,
            presenter=self.presenter,
        )

    async def invoke(self, plan: SOPPlan | None, **extra) -> dict:
        payload = {"action": "create", "direct_answer": False}
        if plan is not None:
            payload["plan"] = plan.model_dump(mode="json")
        payload.update(extra)
        raw = await self.tool.ainvoke(payload)
        return json.loads(raw)


@pytest.mark.asyncio
async def test_validation_failure_returns_errors_without_store_or_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)
    bad_plan = _plan(steps=[_step("s1", assignee="unknown-role")])

    result = await harness.invoke(bad_plan)

    assert result["success"] is False
    assert any("不在团队花名册中" in error for error in result["errors"])
    # 不落库、不发事件、不创建审批
    assert harness.store.collection.docs == {}
    assert harness.presenter.emitted == []
    assert harness.approval_calls == []


@pytest.mark.asyncio
async def test_direct_answer_stores_completed_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)

    result = await harness.invoke(_plan(), direct_answer=True)

    assert result["success"] is True
    assert result["direct_answer"] is True
    assert result["plan_id"] == "plan-1"
    # 不阻塞：未创建审批
    assert harness.approval_calls == []
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert [event_type for event_type, _ in harness.presenter.emitted] == ["sop:updated"]


@pytest.mark.asyncio
async def test_plan_without_dispatch_steps_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)

    result = await harness.invoke(_plan(steps=[]))

    assert result["success"] is True
    assert result["direct_answer"] is True
    assert harness.approval_calls == []
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "completed"


@pytest.mark.asyncio
async def test_dispatch_plan_blocks_then_approved_to_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)
    harness.approval_response = ApprovalResponse(approved=True, response={})

    result = await harness.invoke(_plan())

    assert result == {"approved": True, "plan_id": "plan-1"}
    # 确认前状态为 awaiting_confirmation，确认后 running
    sop_updates = [
        data for event_type, data in harness.presenter.emitted if event_type == "sop:updated"
    ]
    assert sop_updates[0]["status"] == "awaiting_confirmation"
    assert sop_updates[1]["status"] == "running"
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "running"
    # approval_required 事件带 plan_id/plan 全量快照
    approval_event = dict(harness.presenter.emitted)["approval_required"]
    assert approval_event["id"] == "approval-1"
    assert approval_event["type"] == "sop_plan"
    assert approval_event["plan_id"] == "plan-1"
    assert approval_event["plan"]["plan_id"] == "plan-1"
    # 审批字段携带 plan 全量 JSON
    assert harness.approval_calls[0]["approval_type"] == "sop_plan"
    assert harness.approval_calls[0]["fields"][0]["value"]["goal"] == "build a report"
    assert harness.wait_calls[0][0] == "approval-1"


@pytest.mark.asyncio
async def test_rejected_with_feedback_stores_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)
    harness.approval_response = ApprovalResponse(
        approved=False, response={"feedback": "粒度太粗，请重新规划"}
    )

    result = await harness.invoke(_plan())

    assert result["approved"] is False
    assert result["feedback"] == "粒度太粗，请重新规划"
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "rejected"
    assert snapshot.user_feedback == "粒度太粗，请重新规划"


@pytest.mark.asyncio
async def test_rejected_without_feedback_skips_empty_feedback_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)
    harness.approval_response = ApprovalResponse(approved=False, response={})

    result = await harness.invoke(_plan())

    assert result["approved"] is False
    assert result["feedback"] == ""
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "rejected"
    # 空反馈不落库：user_feedback 保持 None（不会写入空字符串）
    assert snapshot.user_feedback is None


@pytest.mark.asyncio
async def test_timeout_marks_plan_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)
    harness.approval_response = None  # wait_for_response 超时返回 None

    result = await harness.invoke(_plan())

    assert result["timed_out"] is True
    assert result["plan_id"] == "plan-1"
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "timed_out"


@pytest.mark.asyncio
async def test_confirmed_plan_updates_steps_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)
    # 预置已确认（running）计划
    await harness.store.upsert_plan(_plan(status="running"))

    updated_plan = _plan(
        status="running",
        steps=[
            _step("s1", status=StepStatus.succeeded, output="done"),
            _step("s2", dependencies=["s1"], assignee="team-m2-role2"),
        ],
    )
    result = await harness.invoke(updated_plan)

    assert result == {"success": True, "status": "updated"}
    # 不阻塞：未创建审批
    assert harness.approval_calls == []
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "running"
    s1 = next(step for step in snapshot.steps if step.step_id == "s1")
    assert s1.status == StepStatus.succeeded
    assert s1.output == "done"
    # 事件只发 sop:updated（无 approval_required）
    assert [event_type for event_type, _ in harness.presenter.emitted] == ["sop:updated"]


@pytest.mark.asyncio
async def test_confirmed_plan_completion_sets_plan_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)
    await harness.store.upsert_plan(_plan(status="running"))

    completed_plan = _plan(
        status="completed",
        steps=[
            _step("s1", status=StepStatus.succeeded, output="done"),
            _step("s2", status=StepStatus.succeeded, dependencies=["s1"], output="done2"),
        ],
    )
    result = await harness.invoke(completed_plan)

    assert result == {"success": True, "status": "updated"}
    assert harness.approval_calls == []
    snapshot = await harness.store.get_plan("session-1", "team-1")
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert all(step.status == StepStatus.succeeded for step in snapshot.steps)
    assert [event_type for event_type, _ in harness.presenter.emitted] == ["sop:updated"]
    emitted_plan = harness.presenter.emitted[0][1]
    assert emitted_plan["status"] == "completed"
    assert all(step["status"] == "succeeded" for step in emitted_plan["steps"])


@pytest.mark.asyncio
async def test_missing_plan_without_direct_answer_returns_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _GateHarness(monkeypatch)

    result = await harness.invoke(None)

    assert result["success"] is False
    assert any("plan 不能为空" in error for error in result["errors"])
    assert harness.store.collection.docs == {}
    assert harness.approval_calls == []
