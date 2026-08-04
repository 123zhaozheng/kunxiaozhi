"""Provider-neutral harness compression (compact_zh)."""

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

VENDOR_AVAILABLE_AGENTS_HEADING = "Available subagent types:"


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


_ZH_WRITE_TODOS_TOOL = (
    "仅复杂多步工作使用。保持恰好一项 in_progress，完成即标 completed；"
    "禁止并行调用。最后一次更新后另行交付答案。"
)

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
    "memory_retain": "存储跨会话记忆。仅收高价值非临时信息；过短、似提问、像代码或重复近期记忆会被拒。优先存用户偏好、项目约束、反馈、外部链接，用 user_identity/project_constraint/feedback_rule/reference_link 等显式标签。",
    "memory_recall": "按语义检索跨会话记忆；返回与查询概念相关的历史记录。",
    "memory_delete": "按 ID 删除记忆；ID 取 memory_recall 输出。",
    "read_document": "下载附件文档并返回文本：pdf/docx/pptx 走 MinerU 转 Markdown；txt/md/log/json/py 直接解码；xlsx/csv 不转文本，返回沙箱处理指引。",
    "dify_kb_retrieve": "从当前 persona 绑定的 Dify 知识库检索片段：LLM 改写查询并判定是否检索，并行搜索、去重、重排后返回 top 片段。",
    "audio_transcribe": "按 URL 下载音频并转写为文本。",
    "upload_url_to_sandbox": "从 URL 下载文件到沙箱文件系统，供 shell/脚本访问。",
    "find_skills": "当前工具无法完成任务时，搜索技能市场（按名称/描述/标签关键词）。",
    "install_skill": "将技能市场技能临时装入当前沙箱工作区；返回路径，读 SKILL.md 后按其脚本执行。",
    "env_var_list": "列出当前用户已保存的环境变量名（值恒为掩码，绝不回显明文）。",
    "env_var_set": "保存环境变量（密文存储，不回读明文）。",
    "env_var_delete": "删除指定环境变量。",
    "env_var_delete_all": "删除全部环境变量。",
    "sandbox_mcp_add": "注册沙箱 MCP 服务器并持久化；env_keys 为注入的环境变量 KEY 名列表。",
    "sandbox_mcp_update": "更新沙箱 MCP 服务器的命令或环境变量注入。",
    "sandbox_mcp_remove": "移除沙箱 MCP 服务器并删除数据库记录。",
    "image_generate": "生成或编辑图片；给 input_images 即图生图模式。",
    "create_persona_preset": "创建 persona 预设：system_prompt 需覆盖角色身份、行为准则、输出格式、约束四部分。",
    "update_persona_preset": "按 preset_id 或名称更新 persona 预设。",
    "search_persona_presets": "建团队前先按任务角色词检索 persona 预设；空串列出近期可见预设。",
    "create_agent_team": "按 search_persona_presets 结果组建团队；members 每项含 persona_preset_id 等字段。",
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
    "memory_retain": {"content": "要存储的记忆内容（事实、观察、经验）。", "title": "短标题（≤25 字符）。", "summary": "简述（≤80 字符）。", "context": "可选上下文/分类（如 user_identity、project_constraint、feedback_rule、reference_link）。", "tags": "关键词标签（最多 5 个）。", "existing_memory_id": "更新指定记忆 ID，避免模糊去重。"},
    "memory_recall": {"query": "搜索查询。", "max_results": "返回条数上限（默认 5）。", "memory_types": "按记忆类型过滤；不传返回全部。"},
    "memory_delete": {"memory_id": "要删除的记忆 ID。"},
    "read_document": {"url": "文档附件 URL（绝对 URL 或 /api/upload/file/<key> 路径）。"},
    "dify_kb_retrieve": {"query": "用户问题或检索查询。", "top_k": "重排后返回片段数上限（默认取系统设置）。", "score_threshold": "最低相关度阈值 0.0-1.0（默认取系统设置）。"},
    "audio_transcribe": {"url": "音频文件 URL（支持绝对 URL 和 /api 路径）。", "model": "转写模型覆盖，如 gpt-4o-mini-transcribe。", "language": "语言提示，如 en 或 zh。", "prompt": "转写提示词，改善识别。"},
    "upload_url_to_sandbox": {"url": "要下载的文件 URL", "file_path": "沙箱内的目标文件路径（绝对路径）"},
    "find_skills": {"query": "技能市场搜索关键词（名称/描述/标签）。", "tags": "每个结果必须包含的标签。"},
    "install_skill": {"name": "find_skills 返回的精确技能名。"},
    "env_var_list": {},
    "env_var_set": {"key": "环境变量名（须匹配 ^[A-Za-z_][A-Za-z0-9_]*$）。", "value": "要加密存储的环境变量值。"},
    "env_var_delete": {"key": "要删除的环境变量名。"},
    "env_var_delete_all": {},
    "sandbox_mcp_add": {"server_name": "要注册的 MCP 服务器名。", "command": "stdio 命令，如 'npx @anthropic/mcp-server-fetch'。", "env_keys": "逗号分隔的环境变量 KEY 名列表（须已定义）。"},
    "sandbox_mcp_update": {"server_name": "要更新的 MCP 服务器名。", "command": "新的 stdio 命令（省略则不修改）。", "env_keys": "逗号分隔的环境变量 KEY 名列表（省略则不修改）。"},
    "sandbox_mcp_remove": {"server_name": "要移除的 MCP 服务器名。"},
    "image_generate": {"prompt": "描述要生成或编辑的图片。", "input_images": "源图片 URL；提供后进入图生图模式。", "background": "背景处理：auto、opaque 或 transparent。"},
    "create_persona_preset": {"name": "Persona 预设名。", "system_prompt": "定义角色身份、行为准则、输出格式与约束的系统提示词。", "description": "一行简介。", "avatar": "emoji 或头像图 URL。", "tags": "分类标签。"},
    "update_persona_preset": {"preset_id": "已知的精确预设 ID。", "current_name": "未知 preset_id 时用现有名称定位。", "name": "新的预设名。", "description": "新的一行简介。"},
    "search_persona_presets": {"query": "搜索文本；用任务角色/能力词。", "tag": "精确标签过滤。", "limit": "返回数量上限（1-50）。"},
    "create_agent_team": {"name": "团队名（≤80 字符）。", "members": "成员列表，每项含 persona_preset_id 等字段。", "team_id": "已有团队 ID；更新时传入。", "description": "团队用途简述。", "avatar": "emoji 或头像图 URL。", "tags": "可搜索标签。"},
}

ZH_CATALOG = HarnessCatalog(
    behavior_guide=COMPACT_ZH_BEHAVIOR_GUIDE,
    write_todos_system=(
        "## `write_todos`\n仅复杂多步工作使用；保持恰好一项 in_progress，完成即更新，"
        "禁止并行调用；简单任务跳过，最后一次调用后再交付答案。"
    ),
    memory_guide=(
        """## 跨会话记忆
`<memory_index>` 仅是线索；相关时用 `memory_recall` 取详情，不得视为事实。

`memory_retain` 仅保存长期用户事实、偏好、项目约束、非显然决策、外部链接及明确反馈；不存代码、Git 历史、临时状态或活动日志。优先更新而非重复。相对日期转为绝对日期。

`memory_delete` 删除错误或过时记忆。超过 30 天、路径、函数及开关均须重新核验；当前证据优先。用户要求忽略/忘记时不得再引用。仅用上述记忆工具，不使用 `/memories/` 路径。"""
    ),
    tool_descriptions=_ZH_TOOLS,
    schema_fields=_ZH_FIELDS,
    filesystem_system=(
        "## 文件规则\n编辑前先读，遵循现有风格；路径必须为绝对路径，大文件分页读取。超大结果存于 `/large_tool_results/<tool_call_id>`，用 `read_file` 或 `grep` 查看。"
    ),
    execute_system=(
        "## `execute`\n在沙箱运行命令、脚本、测试和构建，返回输出与退出码。"
    ),
    task_system=(
        "## `task`\n仅委派隔离且复杂的工作；提供完整上下文与期望输出。独立任务可并行，简单任务勿委派。返回内容由调用者核验整合。"
    ),
    available_agents_heading="可用代理类型：",
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


def build_short_todo_middleware() -> list[Any]:
    if (cls := _short_todo_class()) is None:
        return []
    return [
        cls(
            system_prompt=ZH_CATALOG.write_todos_system,
            tool_description=ZH_CATALOG.tool_descriptions["write_todos"],
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
    try:
        source = deepcopy(tool.args_schema) if isinstance(tool.args_schema, dict) else tool.get_input_schema().model_json_schema()
    except Exception:
        return tool
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


def build_harness_extra_middleware() -> Sequence[AgentMiddleware]:
    return (*build_short_todo_middleware(), HarnessLocalizationMiddleware(ZH_CATALOG))
