"""SOP dispatch 守卫中间件：执行节点前未置 running → 注入提醒 ToolMessage。

主代理 SOP 引导文案已要求"执行节点前必须先 `update_sop` 置 running"，但 LLM
可能忽略。本中间件作为运行时兜底：拦截 `task` 工具调用，若存在已确认（status=
running）的 SOP 计划，且被 dispatch 的 `subagent_type` 对应步骤状态不是
`running`/`succeeded`/`failed`（即未启动），则 **不 dispatch**，返回提醒
ToolMessage 让主代理自我修正。

保持 agent 驱动：不硬阻断报错，仅提示"请先 update_sop 置 running"。
仅挂载在主代理（在 nodes.py TEAM_SOP_MODE 开启时）；子代理不挂。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from src.agents.team_agent.sop.store import SopRunStore
from src.infra.logging import get_logger

logger = get_logger(__name__)

_TASK_TOOL_NAME = "task"
# 这些状态的步骤视为已启动，允许 dispatch（running=进行中，succeeded/failed=已结）。
_DISPATCH_OK_STATUSES = frozenset({"running", "succeeded", "failed"})


class SopDispatchGuardMiddleware(AgentMiddleware):
    """SOP dispatch 守卫：执行节点前未置 running 则提醒主代理。"""

    def __init__(self, *, session_id: str, team_id: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._team_id = team_id
        self._store = SopRunStore()

    async def awrap_tool_call(  # type: ignore[override]
        self,
        request: ToolCallRequest,
        handler: Any,
    ) -> Any:
        tool_call = request.tool_call
        if tool_call.get("name") != _TASK_TOOL_NAME:
            return await handler(request)

        args = tool_call.get("args", {}) or {}
        subagent_type = args.get("subagent_type")

        # 无 subagent_type 的 task 调用（异常）放行，交由工具自身处理。
        if not subagent_type:
            return await handler(request)

        plan = await self._store.get_plan(self._session_id, self._team_id)
        # 无计划或未确认（draft/awaiting_confirmation/rejected）：SOP 尚未进入执行，
        # dispatch 由既有引导约束；守卫不干预。
        if plan is None or plan.status != "running":
            return self._blocked_message(
                tool_call.get("id", "") or "",
                "SOP 未获得用户确认，禁止 dispatch",
            )

        # 查找目标步骤：assignee 匹配且状态未启动。
        pending_step = next(
            (
                step
                for step in plan.steps
                if step.assignee == subagent_type and step.status not in _DISPATCH_OK_STATUSES
            ),
            None,
        )
        if pending_step is None:
            running_step = next(
                (
                    step
                    for step in plan.steps
                    if step.assignee == subagent_type and step.status == "running"
                ),
                None,
            )
            if running_step is not None:
                return await handler(request)
            return self._blocked_message(
                tool_call.get("id", "") or "",
                "SOP 中没有可 dispatch 的运行中步骤",
            )

        tool_call_id = tool_call.get("id", "")
        reminder = (
            f"步骤「{pending_step.title}」（step_id={pending_step.step_id}）尚未置 running。"
            "执行节点前必须先调 `update_sop`（action=update）把该步骤 status 置为 running，"
            "然后再 dispatch 子代理。"
        )
        logger.info(
            "[SopDispatchGuard] Blocked task(subagent_type=%s): step %s pending; "
            "reminding to set running first",
            subagent_type,
            pending_step.step_id,
        )
        return ToolMessage(content=reminder, tool_call_id=tool_call_id)

    @staticmethod
    def _blocked_message(tool_call_id: str, content: str) -> ToolMessage:
        return ToolMessage(
            content=content + "，请先调用 `update_sop` 更新状态。",
            tool_call_id=tool_call_id,
        )
