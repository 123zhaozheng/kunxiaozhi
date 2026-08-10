"""SOP 计划数据模型与确定性校验。

SOP（Standard Operating Procedure）DAG 由主代理通过 update_sop 工具动态生成：
steps 为节点、dependencies 为依赖边，assignee 指向团队 persona 子代理
（build_team_member_subagent_type 产出的稳定 subagent_type）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from src.infra.utils.datetime import utc_now


class StepStatus(str, Enum):
    """SOP 步骤状态。"""

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class SOPStep(BaseModel):
    """SOP 计划中的单个步骤（DAG 节点）。"""

    step_id: str = Field(description="步骤稳定标识，如 s1/s2")
    title: str = Field(description="步骤标题")
    description: str = Field(default="", description="步骤说明")
    dependencies: list[str] = Field(default_factory=list, description="上游依赖的 step_id（前端据此建边）")
    assignee: str = Field(description="负责角色 subagent_type（build_team_member_subagent_type 产出）")
    stage: str | None = Field(default=None, description="阶段分组（v1 仅展示）")
    expected_output: str = Field(description="本步可验证的交付物说明")
    acceptance_criteria: list[str] = Field(default_factory=list, description="验收标准")
    status: StepStatus = Field(default=StepStatus.pending, description="步骤状态")
    attempts: int = Field(default=0, description="已尝试次数")
    output: str | None = Field(default=None, description="步骤完成摘要（主代理回填）")
    error: str | None = Field(default=None, description="步骤失败原因")


SOPPlanStatus = Literal[
    "draft",
    "awaiting_confirmation",
    "running",
    "completed",
    "failed",
    "cancelled",
    "rejected",
    "timed_out",
]


class SOPPlan(BaseModel):
    """完整的 SOP 计划（含状态与进度），由主代理通过 update_sop 全量替换。"""

    plan_id: str = Field(description="计划 ID")
    session_id: str = Field(description="会话 ID")
    team_id: str = Field(description="团队 ID")
    goal: str = Field(description="用户目标")
    summary: str = Field(default="", description="计划摘要")
    steps: list[SOPStep] = Field(description="步骤列表（DAG 节点）")
    status: SOPPlanStatus = Field(default="draft", description="计划整体状态")
    user_feedback: str | None = Field(default=None, description="用户反馈（拒绝/重新规划时写入）")
    approval_id: str | None = Field(default=None, description="待处理的 SOP approval ID")
    created_at: datetime = Field(default_factory=utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=utc_now, description="最近更新时间")


def _find_cycle(steps_by_id: dict[str, SOPStep]) -> list[str] | None:
    """DFS 检测依赖环，返回环路径（形如 [s1, s3, s1]），无环返回 None。"""
    _white, _gray, _black = 0, 1, 2
    color: dict[str, int] = {step_id: _white for step_id in steps_by_id}
    stack: list[str] = []

    def _dfs(node: str) -> list[str] | None:
        color[node] = _gray
        stack.append(node)
        for dep in steps_by_id[node].dependencies:
            if dep not in steps_by_id:
                continue  # 不存在的依赖单独报错，不参与环检测
            if color[dep] == _gray:
                idx = stack.index(dep)
                return stack[idx:] + [dep]
            if color[dep] == _white:
                cycle = _dfs(dep)
                if cycle is not None:
                    return cycle
        stack.pop()
        color[node] = _black
        return None

    for step_id in steps_by_id:
        if color[step_id] == _white:
            cycle = _dfs(step_id)
            if cycle is not None:
                return cycle
    return None


def validate_sop_plan(
    plan: SOPPlan,
    roster_subagent_types: list[str],
    max_steps: int,
    min_steps: int,
) -> list[str]:
    """对 SOP 计划做确定性校验，返回结构化错误列表（空列表 = 通过）。

    校验项：步骤非空、步骤数 ≤ max_steps、依赖引用存在且无自依赖、
    DFS 环检测、assignee ∈ 花名册、expected_output 非空。

    min_steps 仅用于粒度提示（步数低于下限不视为错误，工具层可据此提示直接回答）。
    """
    errors: list[str] = []
    if not plan.plan_id.strip():
        errors.append("plan_id 不能为空")
    if not plan.session_id.strip():
        errors.append("session_id 不能为空")
    if not plan.team_id.strip():
        errors.append("team_id 不能为空")
    if not plan.goal.strip():
        errors.append("goal 不能为空")
    step_ids = [step.step_id.strip() for step in plan.steps]
    if any(not step_id for step_id in step_ids):
        errors.append("step_id 不能为空")
    duplicates = sorted({step_id for step_id in step_ids if step_id and step_ids.count(step_id) > 1})
    if duplicates:
        errors.append(f"step_id 不能重复: {', '.join(duplicates)}")
    steps_by_id: dict[str, SOPStep] = {step.step_id: step for step in plan.steps}
    roster = set(roster_subagent_types)

    if not plan.steps:
        errors.append("steps 不能为空，至少需要 1 个步骤")

    effective_max_steps = min(max_steps, 12)
    if len(plan.steps) > effective_max_steps:
        errors.append(f"步骤数 {len(plan.steps)} 超过上限 {effective_max_steps}，请合并过细步骤")

    for step in plan.steps:
        if not (step.expected_output or "").strip():
            errors.append(f"步骤 {step.step_id} 的 expected_output 不能为空")
        if step.assignee not in roster:
            errors.append(f"步骤 {step.step_id} 的 assignee '{step.assignee}' 不在团队花名册中")
        for dep in step.dependencies:
            if dep == step.step_id:
                errors.append(f"步骤 {step.step_id} 不能依赖自身")
            elif dep not in steps_by_id:
                errors.append(f"步骤 {step.step_id} 依赖不存在的步骤 '{dep}'")

    cycle = _find_cycle(steps_by_id)
    if cycle is not None:
        errors.append(f"步骤之间存在循环依赖: {' -> '.join(cycle)}")

    return errors
