"""定时任务自动执行（unattended）支持。

定时任务运行时用户不在场，因此需要：
- 在模型请求层去掉 `ask_human`、`update_sop` 以及定时任务聊天工具，模型看不到它们；
- 模型仍然误调用时，在工具调用层直接返回提示，不执行工具（不创建审批、不会卡住等待）；
- 在系统提示中追加一段「定时任务自动执行」说明，约束模型保守执行。

标记来自 `TraceContext.get_request_context().unattended`，由 `TaskExecutor.run_task`
按次运行显式设置；续聊、企业微信、启动恢复等入口均为 False，行为与原来完全一致。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import ToolMessage

from src.infra.logging.context import TraceContext

# 定时任务聊天工具（草稿/查询），定时运行中不允许再创建定时任务
SCHEDULED_TASK_CHAT_TOOL_NAMES = frozenset({"list_scheduled_tasks", "draft_scheduled_task"})

# 保存到知识库需要用户在卡片上确认，定时运行中无人确认
SCHEDULED_RUN_EXCLUDED_TOOLS = (
    frozenset({"ask_human", "update_sop", "save_to_knowledge_base"})
    | SCHEDULED_TASK_CHAT_TOOL_NAMES
)

SCHEDULED_RUN_PROMPT = (
    "## 定时任务自动执行\n"
    "这是定时任务自动执行，用户此刻不在场：不能向用户提问或请求确认；"
    "不执行不可逆或对外有副作用的操作；"
    "需要用户确认的事项，在回答末尾以「需要你确认的事项」列出。"
)

SCHEDULED_RUN_BLOCKED_MESSAGE = (
    "定时任务自动执行中，用户不在场，不能提问或请求确认。"
    "请按保守方式继续，把需要用户确认的事项写在回答末尾。"
)


def is_scheduled_run() -> bool:
    """当前是否处于定时任务自动执行（用户不在场）。"""
    return bool(TraceContext.get_request_context().unattended)


def scheduled_run_prompt_sections() -> list[str]:
    """定时运行时返回需追加的系统提示段，否则为空。"""
    return [SCHEDULED_RUN_PROMPT] if is_scheduled_run() else []


def scheduled_run_middleware() -> list[AgentMiddleware]:
    """定时运行时返回需挂载的中间件，否则为空。"""
    return [ScheduledRunMiddleware()] if is_scheduled_run() else []


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


class ScheduledRunMiddleware(AgentMiddleware):
    """定时运行：模型请求层过滤用户交互类工具，并拦截误调用。"""

    _excluded_tool_names = SCHEDULED_RUN_EXCLUDED_TOOLS

    def _override(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        tools = [t for t in request.tools if _tool_name(t) not in self._excluded_tool_names]
        if len(tools) == len(request.tools):
            return request
        return request.override(tools=tools)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(self._override(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self._override(request))

    def _blocked_message(self, request: Any) -> ToolMessage | None:
        tool_call = request.tool_call
        name = str(tool_call.get("name", "") or "")
        if name not in self._excluded_tool_names:
            return None
        return ToolMessage(
            content=json.dumps(
                {"status": "unavailable", "message": SCHEDULED_RUN_BLOCKED_MESSAGE},
                ensure_ascii=False,
            ),
            tool_call_id=tool_call.get("id", ""),
            name=name,
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        blocked = self._blocked_message(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        blocked = self._blocked_message(request)
        if blocked is not None:
            return blocked
        return await handler(request)
