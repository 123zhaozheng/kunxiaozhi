"""update_sop 工具：SOP DAG 计划 + 进度载体（含确认门禁）。

团队模式下主代理对复杂任务调用 update_sop 建立 SOP DAG（全量替换语义，同
write_todos）。含分派步骤的计划触发确认门禁（create_approval + wait_for_response，
同 ask_human 模式）：确认前不 dispatch 任何子代理；确认后仅更新步骤状态/输出，
不再阻塞。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.agents.team_agent.sop.schemas import SOPPlan, validate_sop_plan
from src.agents.team_agent.sop.store import SopRunStore
from src.api.routes.human import create_approval, wait_for_response
from src.infra.logging import get_logger
from src.kernel.config import settings

logger = get_logger(__name__)

SOP_APPROVAL_TYPE = "sop_plan"
SOP_APPROVAL_TIMEOUT_SECONDS = 300

# 已确认（执行中/已结束）状态：此后 update_sop 只更新步骤状态与输出，不重复阻塞。
_CONFIRMED_STATUSES = {"running", "completed", "failed", "cancelled"}


class UpdateSopInput(BaseModel):
    """update_sop 工具入参（全量替换语义，同 write_todos）。"""

    action: Literal["create", "update"] = Field(
        description="操作类型：create 创建/替换计划，update 更新步骤状态与输出"
    )
    plan: SOPPlan | None = Field(default=None, description="完整 SOP 计划（全量快照）")
    direct_answer: bool = Field(
        default=False,
        description="是否直接回答（简单问题，不建 DAG、不触发确认门禁）",
    )


def _plan_json(plan: SOPPlan) -> dict[str, Any]:
    """SOPPlan 的 JSON 快照（sop:updated 事件 payload）。"""
    return plan.model_dump(mode="json")


async def _emit_sop_updated(presenter: Any | None, plan: SOPPlan) -> None:
    """推送 sop:updated 全量快照事件（present + save 双写）。"""
    if presenter is None:
        return
    try:
        await presenter.emit_team_event("sop:updated", _plan_json(plan))
    except Exception as e:
        logger.warning(f"[update_sop] Failed to emit sop:updated: {e}")


async def _emit_approval_required(presenter: Any | None, approval: Any, plan: SOPPlan) -> None:
    """推送 approval_required(sop_plan) 事件，通知前端弹确认。"""
    if presenter is None:
        return
    try:
        await presenter.emit_team_event(
            "approval_required",
            {
                "id": approval.id,
                "message": f"确认执行该 SOP 计划（{len(plan.steps)} 步）？",
                "type": SOP_APPROVAL_TYPE,
                "plan_id": plan.plan_id,
                "plan": _plan_json(plan),
            },
        )
    except Exception as e:
        logger.warning(f"[update_sop] Failed to emit approval_required: {e}")


def _rejection_feedback(response: Any) -> str:
    """从拒绝响应中提取用户反馈文本。"""
    if response.response and isinstance(response.response, dict):
        feedback = response.response.get("feedback", "")
        if isinstance(feedback, str):
            return feedback
    return ""


async def _update_confirmed_plan(
    store: SopRunStore,
    presenter: Any | None,
    plan: SOPPlan,
) -> dict[str, Any]:
    """已确认计划：应用步骤状态/输出更新，发 sop:updated，不阻塞。"""
    for step in plan.steps:
        await store.set_step_status(
            plan.session_id,
            plan.team_id,
            step.step_id,
            step.status,
            output=step.output,
            error=step.error,
        )
    snapshot = await store.get_plan(plan.session_id, plan.team_id)
    # 计划整体状态变化（如收尾置 completed）仅在显式指定且非默认值时应用
    if (
        snapshot is not None
        and plan.status != "draft"
        and plan.status != snapshot.status
    ):
        snapshot = await store.set_status(plan.session_id, plan.team_id, plan.status)
    if snapshot is not None:
        await _emit_sop_updated(presenter, snapshot)
    return {"success": True, "status": "updated"}


async def _request_confirmation(
    *,
    store: SopRunStore,
    presenter: Any | None,
    plan: SOPPlan,
    session_id: str,
    user_id: str,
) -> dict[str, Any]:
    """含分派步骤且未确认：落库(awaiting_confirmation) → 审批 → 阻塞等用户确认。"""
    plan.status = "awaiting_confirmation"
    plan.user_feedback = None
    snapshot = await store.upsert_plan(plan)
    await _emit_sop_updated(presenter, snapshot)

    plan_json = _plan_json(snapshot)
    approval = await create_approval(
        message=f"确认执行该 SOP 计划（{len(plan.steps)} 步，目标：{plan.goal}）",
        approval_type=SOP_APPROVAL_TYPE,
        fields=[{"name": "plan", "type": "sop_plan", "value": plan_json}],
        session_id=session_id,
        user_id=user_id,
    )
    snapshot.approval_id = approval.id
    snapshot = await store.upsert_plan(snapshot)
    await _emit_approval_required(presenter, approval, snapshot)

    response = await wait_for_response(approval.id, timeout=SOP_APPROVAL_TIMEOUT_SECONDS)

    if response is None:
        expired = await store.set_status_for_plan(
            plan.session_id, plan.team_id, plan.plan_id, "timed_out"
        )
        if expired is not None:
            expired.approval_id = snapshot.approval_id
            await _emit_sop_updated(presenter, expired)
        return {"timed_out": True, "plan_id": plan.plan_id}

    if response.approved:
        updated = await store.set_status_for_plan(
            plan.session_id, plan.team_id, plan.plan_id, "running"
        )
        if updated is not None:
            await _emit_sop_updated(presenter, updated)
        return {"approved": True, "plan_id": plan.plan_id}

    feedback = _rejection_feedback(response)
    if feedback:
        await store.set_feedback(plan.session_id, plan.team_id, feedback)
    rejected = await store.set_status_for_plan(
        plan.session_id, plan.team_id, plan.plan_id, "rejected"
    )
    if rejected is not None:
        await _emit_sop_updated(presenter, rejected)
    return {"approved": False, "feedback": feedback, "plan_id": plan.plan_id}


async def _run_update_sop(
    *,
    action: str,
    plan: SOPPlan | None,
    direct_answer: bool,
    session_id: str,
    user_id: str,
    team_id: str,
    roster_subagent_types: list[str],
    presenter: Any | None,
    store: SopRunStore,
) -> dict[str, Any]:
    """update_sop 核心逻辑，返回结构化 JSON 结果。

    分支：
    1. 校验失败 → 返回 errors，不落库、不发事件；
    2. direct_answer / 无分派步骤 → 落库 + sop:updated，不阻塞；
    3. 含分派步骤且未确认 → 落库(awaiting_confirmation) + 审批阻塞；
    4. 已确认后的调用 → 仅更新步骤状态/输出，不阻塞。
    """
    del action  # create/update 由门禁状态分流，action 仅作意图提示

    # 1. plan 缺失
    if plan is None:
        if direct_answer:
            # 直接回答：无需计划载体，直接返回成功
            return {"success": True, "direct_answer": True, "plan_id": None}
        return {
            "success": False,
            "errors": ["plan 不能为空；如需直接回答请设置 direct_answer=True"],
        }

    if session_id:
        plan.session_id = session_id
    if team_id:
        # 用权威 team_id 覆盖 LLM 提供值，保证 store 键与 dispatch 守卫查找一致。
        plan.team_id = team_id

    # 2. direct_answer / 无分派步骤（steps 为空）：不阻塞，落库 + 发事件
    if direct_answer or not plan.steps:
        plan.status = "completed"
        snapshot = await store.upsert_plan(plan)
        await _emit_sop_updated(presenter, snapshot)
        return {"success": True, "direct_answer": True, "plan_id": plan.plan_id}

    # 3. 确定性校验（失败不落库、不发事件）
    errors = validate_sop_plan(
        plan,
        roster_subagent_types,
        max_steps=settings.TEAM_SOP_MAX_STEPS,
        min_steps=settings.TEAM_SOP_MIN_STEPS,
    )
    if errors:
        return {"success": False, "errors": errors}

    # 4. 已确认计划：只更新步骤状态/输出，不阻塞
    existing = await store.get_plan(plan.session_id, plan.team_id)
    if (
        existing is not None
        and existing.plan_id == plan.plan_id
        and existing.status in _CONFIRMED_STATUSES
    ):
        return await _update_confirmed_plan(store, presenter, plan)

    # 5. 含分派步骤且未确认：阻塞等用户确认
    return await _request_confirmation(
        store=store,
        presenter=presenter,
        plan=plan,
        session_id=plan.session_id,
        user_id=user_id,
    )


def create_update_sop_tool(
    session_id: str,
    user_id: str,
    roster_subagent_types: list[str],
    presenter: Any | None = None,
    team_id: str = "",
) -> StructuredTool:
    """创建 update_sop 工具实例（闭包注入会话、用户与团队花名册上下文）。

    Args:
        session_id: 会话 ID
        user_id: 用户 ID
        roster_subagent_types: 团队成员 subagent_type 集合
            （build_team_member_subagent_type 产出），用于 assignee 校验
        presenter: Presenter 实例，用于 emit_team_event 双写（可选）
        team_id: 团队 ID（权威值，覆盖 plan.team_id 以保证 store 键稳定）
    """

    async def _tool(
        action: str,
        plan: SOPPlan | None,
        direct_answer: bool,
    ) -> str:
        result = await _run_update_sop(
            action=action,
            plan=plan,
            direct_answer=direct_answer,
            session_id=session_id,
            user_id=user_id,
            team_id=team_id,
            roster_subagent_types=roster_subagent_types,
            presenter=presenter,
            store=SopRunStore(),
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=_tool,
        name="update_sop",
        description="""团队模式下规划复杂任务的 SOP DAG（步骤+依赖+角色+状态），并作为执行进度载体。

何时使用：
- 复杂/多角色任务：先用 action=create 提交完整 SOP 计划（全量替换语义），确认后逐步执行；
- 简单问题：直接回答，设置 direct_answer=true（不建 DAG、不触发确认）。

参数：
- action: create 创建/替换计划；update 更新步骤状态与输出（确认后使用）
- plan: 完整 SOPPlan（steps 每项含 step_id/title/dependencies/assignee/expected_output/status/output）
- direct_answer: 直接回答声明（简单问题跳过确认门禁）

注意：
- 含分派步骤的计划会阻塞等待用户确认（最多 300 秒）；确认前不要 dispatch 子代理。
- 确认后逐步执行：每步完成后用 update_sop 更新该步骤 status/output。
- 步骤状态：pending/running/succeeded/failed/cancelled；全部完成后置计划 status=completed。
""",
        args_schema=UpdateSopInput,
    )
