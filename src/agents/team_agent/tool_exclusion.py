"""TeamAgent 工具排除中间件。

团队模式下主代理的 `write_todos` 已由 `team_harness_profile`
（`src/agents/team_agent/harness_profile.py`）在 deepagents **组装期**按精确类型
根除：`TodoListMiddleware` 在
`excluded_middleware` 里，工具与引导文案从主代理栈中整体消失，无需请求时序裁剪。
团队成员子代理与主代理同模型（spec 未显式指定 model），继承同一 model 级 profile，
同样在组装期被根除。

本中间件保留为全链路兜底：
- 主代理模型键无法解析（provider/identifier 不可得）的罕见回退场景；
- 子代理未来指定了与主代理不同模型、未叠加 Team profile 的场景；
- deepagents 组装路径变化的防御性保险。

在模型请求层过滤掉 `write_todos` 工具并移除对应系统提示段，保证模型既看不到
工具也看不到引导文案。SOP 工具（`update_sop`）是它的正式替代。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)

from src.agents.core.harness_prompt_overrides import (
    WRITE_TODOS_SECTION_HEADINGS,
    WRITE_TODOS_TOOL_NAME,
)


def _strip_write_todos_section(text: str) -> str:
    """移除 `## write_todos`（含反引号变体）标题段，保留其余提示内容。"""
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading in WRITE_TODOS_SECTION_HEADINGS:
                skipping = True
                continue
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out)


def _strip_system_message(message: Any) -> Any:
    """对 SystemMessage 的 str 或 block 内容做 write_todos 段剥离。"""
    if message is None:
        return None
    content = message.content
    if isinstance(content, str):
        stripped = _strip_write_todos_section(content)
        return message if stripped == content else message.model_copy(update={"content": stripped})
    if isinstance(content, list):
        changed = False
        new_blocks: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                stripped = _strip_write_todos_section(text)
                if stripped != text:
                    changed = True
                new_blocks.append({**block, "text": stripped})
            else:
                new_blocks.append(block)
        return message.model_copy(update={"content": new_blocks}) if changed else message
    return message


class TeamToolExclusionMiddleware(AgentMiddleware):
    """团队模式：模型请求层过滤 write_todos 工具及其提示段。"""

    _excluded_tool_names = frozenset({WRITE_TODOS_TOOL_NAME})

    def _override(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        system = _strip_system_message(request.system_message)
        tools = [
            tool
            for tool in request.tools
            if getattr(tool, "name", "") not in self._excluded_tool_names
        ]
        updates: dict[str, Any] = {}
        if system is not request.system_message:
            updates["system_message"] = system
        if len(tools) != len(request.tools):
            updates["tools"] = tools
        return request.override(**updates) if updates else request

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
