"""Reversible, provider-neutral harness compression."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.tools import BaseTool

from src.kernel.config import (
    HarnessMode,
    get_active_harness_mode,
)

VENDOR_AVAILABLE_AGENTS_HEADING = "Available subagent types:"


def select_harness_text(*, legacy: str, compact_en: str, compact_zh: str) -> str:
    mode = get_active_harness_mode()
    return legacy if mode == "legacy" else compact_en if mode == "compact_en" else compact_zh


COMPACT_EN_BEHAVIOR_GUIDE = """You can use tools; the user sees messages and tool activity.

## Behavior
- Be concise; skip preambles and empty praise. Act directly.
- Accuracy first. Disagree respectfully when evidence conflicts.
- Ask only for missing information that blocks the next useful step; otherwise use safe defaults.
- Work: inspect enough context, act, then verify against the request. Iterate until done.
- Do not stop at a plan. On repeated failure, diagnose and change approach.
- State blockers plainly. Give brief progress updates only for longer work."""

COMPACT_ZH_BEHAVIOR_GUIDE = """你可调用工具；用户能看到消息与工具活动。

## 行为
- 简洁直达，省略客套、复述和空泛赞美。
- 准确优先；证据冲突时礼貌指出。
- 仅询问会阻断下一步的缺失信息，否则采用安全默认值。
- 工作闭环：读足上下文→执行→按需求验证；迭代至完成。
- 不以计划代替交付；重复失败时先诊断再换方法。
- 明确说明阻塞；仅长任务给出简短进度。"""


@dataclass(frozen=True)
class HarnessCatalog:
    behavior_guide: str
    write_todos_system: str
    memory_guide: str
    tool_descriptions: Mapping[str, str]
    schema_fields: Mapping[str, Mapping[str, str]]
    filesystem_system: str
    execute_system: str
    task_system: str
    available_agents_heading: str


_EN_WRITE_TODOS_TOOL = (
    "Only for complex multi-step work. Keep exactly one item in_progress; "
    "complete promptly; never call in parallel. Deliver the answer after the final update."
)
_ZH_WRITE_TODOS_TOOL = (
    "仅复杂多步工作使用。保持恰好一项 in_progress，完成即标 completed；"
    "禁止并行调用。最后一次更新后另行交付答案。"
)

_EN_TOOLS = {
    "task": "Run one isolated complex assignment.\n\nAvailable agents:\n{available_agents}\n\nInclude context, expected output, and `Current task start time: YYYY-MM-DD HH:mm:ss ±HH:MM Timezone`. Parallelize independent calls; verify and synthesize results.",
    "ls": "List an absolute directory path.",
    "read_file": "Read a file; defaults to 100 lines. Paginate with offset/limit. Media/PDF returns multimodal content.",
    "write_file": "Create a file. Prefer edit_file for an existing file.",
    "edit_file": "Exact text replacement. Read first; old_string must be unique unless replace_all=true.",
    "glob": "Find paths by glob (`*`, `**`, `?`).",
    "grep": "Literal text search; filter by glob and choose output_mode.",
    "execute": "Run a sandbox shell command; returns output and exit code. timeout is seconds.",
    "search_tools": "Load full schemas for deferred MCP tools by exact name or capability keywords. It does not search sandbox tools; use execute with mcporter for those.",
    "write_todos": _EN_WRITE_TODOS_TOOL,
}

_ZH_TOOLS = {
    "task": "执行一个隔离的复杂任务。\n\n可用代理：\n{available_agents}\n\n提供完整上下文、期望输出及 `Current task start time: YYYY-MM-DD HH:mm:ss ±HH:MM Timezone`。独立任务并行调用；结果由主代理核验整合。",
    "ls": "列出绝对目录路径。",
    "read_file": "读取文件；默认100行，以 offset/limit 分页。媒体/PDF 返回多模态内容。",
    "write_file": "新建文件；已有文件优先 edit_file。",
    "edit_file": "精确替换文本。先读取；除非 replace_all=true，old_string 必须唯一。",
    "glob": "按 glob（`*`、`**`、`?`）查找路径。",
    "grep": "字面文本搜索；可用 glob 过滤并指定 output_mode。",
    "execute": "在沙箱运行 shell 命令；返回输出与退出码，timeout 单位秒。",
    "search_tools": "按完整名称或能力关键词加载延迟 MCP 工具的完整 schema。不搜索沙箱工具；沙箱工具用 execute + mcporter。",
    "write_todos": _ZH_WRITE_TODOS_TOOL,
}

_EN_FIELDS = {
    "ls": {"path": "Absolute directory path."},
    "read_file": {"file_path": "Absolute file path.", "offset": "First line, zero-based.", "limit": "Maximum lines."},
    "write_file": {"file_path": "Absolute destination path.", "content": "Text to write."},
    "edit_file": {"file_path": "Absolute file path.", "old_string": "Exact text to replace.", "new_string": "Replacement text.", "replace_all": "Replace every match."},
    "glob": {"pattern": "Glob pattern.", "path": "Search root."},
    "grep": {"pattern": "Literal text.", "path": "Search directory.", "glob": "File filter.", "output_mode": "files_with_matches, content, or count."},
    "execute": {"command": "Shell command.", "timeout": "Timeout seconds; 0 may disable it."},
    "search_tools": {"query": "Exact deferred tool name or capability keywords."},
    "task": {"description": "Complete autonomous assignment.", "subagent_type": "Available agent type."},
    "write_todos": {"todos": "Complete task list.", "content": "Actionable item.", "status": "pending, in_progress, or completed."},
}

_ZH_FIELDS = {
    "ls": {"path": "绝对目录路径。"},
    "read_file": {"file_path": "绝对文件路径。", "offset": "起始行，0基。", "limit": "最大行数。"},
    "write_file": {"file_path": "绝对目标路径。", "content": "写入文本。"},
    "edit_file": {"file_path": "绝对文件路径。", "old_string": "待替换的精确文本。", "new_string": "替换文本。", "replace_all": "替换全部匹配。"},
    "glob": {"pattern": "glob 模式。", "path": "搜索根目录。"},
    "grep": {"pattern": "字面文本。", "path": "搜索目录。", "glob": "文件过滤模式。", "output_mode": "files_with_matches、content 或 count。"},
    "execute": {"command": "shell 命令。", "timeout": "超时秒数；0 可表示不限时。"},
    "search_tools": {"query": "延迟工具完整名称或能力关键词。"},
    "task": {"description": "可独立完成的任务。", "subagent_type": "可用代理类型。"},
    "write_todos": {"todos": "完整任务列表。", "content": "可执行事项。", "status": "pending、in_progress 或 completed。"},
}


def catalog_for_mode(mode: HarnessMode) -> HarnessCatalog:
    if mode == "legacy":
        raise ValueError("legacy uses native vendor middleware")
    zh = mode == "compact_zh"
    return HarnessCatalog(
        behavior_guide=COMPACT_ZH_BEHAVIOR_GUIDE if zh else COMPACT_EN_BEHAVIOR_GUIDE,
        write_todos_system=(
            "## `write_todos`\n仅复杂多步工作使用；保持恰好一项 in_progress，完成即更新，"
            "禁止并行调用；简单任务跳过，最后一次调用后再交付答案。"
            if zh
            else "## `write_todos`\nOnly for complex multi-step work. Keep exactly one item "
            "in_progress and update it promptly; never call in parallel. Skip simple work "
            "and answer after the last call."
        ),
        memory_guide=(
            """## 跨会话记忆
`<memory_index>` 仅是线索；相关时用 `memory_recall` 取详情，不得视为事实。

`memory_retain` 仅保存长期用户事实、偏好、项目约束、非显然决策、外部链接及明确反馈；不存代码、Git 历史、临时状态或活动日志。优先更新而非重复。相对日期转为绝对日期。

`memory_delete` 删除错误或过时记忆。超过 30 天、路径、函数及开关均须重新核验；当前证据优先。用户要求忽略/忘记时不得再引用。仅用上述记忆工具，不使用 `/memories/` 路径。"""
            if zh
            else """## Cross-session memory
`<memory_index>` is a hint only. Use `memory_recall` for relevant details; never treat it as ground truth.

Use `memory_retain` only for durable user facts, preferences, project constraints, non-obvious decisions, external links, and explicit feedback. Skip code, Git history, temporary state, and activity logs; update instead of duplicating. Convert relative dates to absolute dates.

Use `memory_delete` for inaccurate or stale entries. Recheck memories older than 30 days and verify paths, functions, and flags; current evidence wins. Do not reference memories the user asked to forget. Use these tools, not `/memories/` paths."""
        ),
        tool_descriptions=_ZH_TOOLS if zh else _EN_TOOLS,
        schema_fields=_ZH_FIELDS if zh else _EN_FIELDS,
        filesystem_system=(
            "## 文件规则\n编辑前先读，遵循现有风格；路径必须为绝对路径，大文件分页读取。超大结果存于 `/large_tool_results/<tool_call_id>`，用 `read_file` 或 `grep` 查看。"
            if zh
            else "## File rules\nRead before editing; follow existing style. Use absolute paths and paginate large reads. Oversized results are under `/large_tool_results/<tool_call_id>`; inspect with `read_file` or `grep`."
        ),
        execute_system=(
            "## `execute`\n在沙箱运行命令、脚本、测试和构建，返回输出与退出码。"
            if zh
            else "## `execute`\nRuns sandbox commands, scripts, tests, and builds; returns output and exit code."
        ),
        task_system=(
            "## `task`\n仅委派隔离且复杂的工作；提供完整上下文与期望输出。独立任务可并行，简单任务勿委派。返回内容由调用者核验整合。"
            if zh
            else "## `task`\nDelegate only isolated complex work with complete context and expected output. Parallelize independent assignments; verify and synthesize returned evidence."
        ),
        available_agents_heading="可用代理类型：" if zh else "Available subagent types:",
    )


_ShortTodoListMiddleware: type[Any] | None = None


def _short_todo_class() -> type[Any] | None:
    global _ShortTodoListMiddleware
    if _ShortTodoListMiddleware is not None:
        return _ShortTodoListMiddleware
    try:
        from langchain.agents.middleware import TodoListMiddleware
    except ImportError:  # pragma: no cover
        return None

    class ShortTodoListMiddleware(TodoListMiddleware):
        """Exact-type exclusion-safe compact TodoList middleware."""

    _ShortTodoListMiddleware = ShortTodoListMiddleware
    return _ShortTodoListMiddleware


def build_short_todo_middleware(mode: HarnessMode | None = None) -> list[Any]:
    selected = mode or get_active_harness_mode()
    if selected == "legacy" or (cls := _short_todo_class()) is None:
        return []
    catalog = catalog_for_mode(selected)
    return [
        cls(
            system_prompt=catalog.write_todos_system,
            tool_description=catalog.tool_descriptions["write_todos"],
        )
    ]


def _compact_schema(node: Any, descriptions: Mapping[str, str]) -> Any:
    if isinstance(node, list):
        return [_compact_schema(item, descriptions) for item in node]
    if not isinstance(node, dict):
        return node
    result = {
        key: _compact_schema(value, descriptions)
        for key, value in node.items()
        if key not in {"description", "title"}
    }
    if isinstance(properties := result.get("properties"), dict):
        for name, schema in properties.items():
            if isinstance(schema, dict) and name in descriptions:
                schema["description"] = descriptions[name]
    return result


def localize_tool_for_model(tool: Any, catalog: HarnessCatalog) -> Any:
    """Copy only known BaseTools; originals remain execution/validation truth."""
    if not isinstance(tool, BaseTool) or tool.name not in catalog.schema_fields:
        return tool
    source = deepcopy(tool.args_schema) if isinstance(tool.args_schema, dict) else tool.get_input_schema().model_json_schema()
    # SubAgentMiddleware has already expanded task's {available_agents}; never
    # replace that rendered description with the catalog template. Other known
    # descriptions are safe to normalize here, including middleware-injected
    # write_todos because it shares this exact catalog value.
    description = (
        tool.description
        if tool.name == "task"
        else catalog.tool_descriptions.get(tool.name, tool.description)
    )
    return tool.model_copy(
        update={
            "args_schema": _compact_schema(source, catalog.schema_fields[tool.name]),
            "description": description,
        }
    )


def _replacements(catalog: HarnessCatalog) -> tuple[tuple[str, str], ...]:
    try:
        from deepagents.middleware.filesystem import (
            EXECUTION_SYSTEM_PROMPT,
            FILESYSTEM_SYSTEM_PROMPT,
        )
        from deepagents.middleware.subagents import TASK_SYSTEM_PROMPT
    except ImportError:  # pragma: no cover
        return ()
    return (
        (FILESYSTEM_SYSTEM_PROMPT, catalog.filesystem_system),
        (EXECUTION_SYSTEM_PROMPT, catalog.execute_system),
        (TASK_SYSTEM_PROMPT, catalog.task_system),
        (VENDOR_AVAILABLE_AGENTS_HEADING, catalog.available_agents_heading),
    )


def _localize_system(message: Any, catalog: HarnessCatalog) -> Any:
    if message is None:
        return None

    def replace(text: str) -> str:
        for source, target in _replacements(catalog):
            text = text.replace(source, target)
        return text

    content = message.content
    if isinstance(content, str):
        localized: Any = replace(content)
    elif isinstance(content, list):
        localized = [
            {**block, "text": replace(block["text"])}
            if isinstance(block, dict) and isinstance(block.get("text"), str)
            else block
            for block in content
        ]
    else:
        return message
    return message if localized == content else message.model_copy(update={"content": localized})


class HarnessLocalizationMiddleware(AgentMiddleware):
    """Localize model-visible copies while preserving runtime tools."""

    def __init__(self, catalog: HarnessCatalog) -> None:
        super().__init__()
        self._catalog = catalog

    def _override(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        system = _localize_system(request.system_message, self._catalog)
        tools = [localize_tool_for_model(tool, self._catalog) for tool in request.tools]
        updates: dict[str, Any] = {}
        if system is not request.system_message:
            updates["system_message"] = system
        if any(new is not old for new, old in zip(tools, request.tools, strict=True)):
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


def build_harness_extra_middleware(mode: HarnessMode | None = None) -> Sequence[AgentMiddleware]:
    selected = mode or get_active_harness_mode()
    if selected == "legacy":
        return ()
    catalog = catalog_for_mode(selected)
    return (*build_short_todo_middleware(selected), HarnessLocalizationMiddleware(catalog))


_mode = get_active_harness_mode()
if _mode == "legacy":
    TOOL_DESCRIPTION_OVERRIDES: Mapping[str, str] = {}
    SHORT_WRITE_TODOS_TOOL = ""
    SHORT_WRITE_TODOS_SYSTEM = ""
else:
    _catalog = catalog_for_mode(_mode)
    TOOL_DESCRIPTION_OVERRIDES = _catalog.tool_descriptions
    SHORT_WRITE_TODOS_TOOL = _catalog.tool_descriptions["write_todos"]
    SHORT_WRITE_TODOS_SYSTEM = _catalog.write_todos_system

SHORT_TASK_TOOL = TOOL_DESCRIPTION_OVERRIDES.get("task", "")
SHORT_READ_FILE = TOOL_DESCRIPTION_OVERRIDES.get("read_file", "")
SHORT_EXECUTE = TOOL_DESCRIPTION_OVERRIDES.get("execute", "")
build_todo_middleware: Callable[[], Sequence[Any]] = build_short_todo_middleware
