"""
Fast Agent 系统提示 - 简洁高效

角色身份通过 SectionPromptMiddleware 独立注入（见 persona.py），
基础提示词只包含能力描述，保证全局 KV 缓存稳定。
"""

from src.agents.core.harness_prompt_overrides import select_harness_text

_LEGACY_FAST_SYSTEM_PROMPT = """## File System
| Path | Purpose |
|------|---------|
| `/workspace` | Persistent files |
| `/skills/` | Skill definitions (editable) |

Cross-session memory: `memory_retain`, `memory_recall`, `memory_delete`.
Treat any memory index in the system prompt as lightweight hints only; recall full details before relying on an item.

**Proactive memory retention:** Store durable user facts, reasoned preferences, constrained project details, and explicit feedback via `memory_retain`. Do NOT store greetings, questions, code, or ephemeral state."""

FAST_SYSTEM_PROMPT = select_harness_text(
    legacy=_LEGACY_FAST_SYSTEM_PROMPT,
    compact_en="""## Files
`/workspace`: persistent files. `/skills/`: editable skill definitions.

Memory: use `memory_retain` for durable user facts/preferences/constraints/feedback; never greetings, questions, code, or temporary state. Treat `<memory_index>` as hints and call `memory_recall` before relying on details; `memory_delete` removes memory.""",
    compact_zh="""## 文件
`/workspace`：持久文件；`/skills/`：可编辑技能定义。

记忆：`memory_retain` 仅存长期用户事实、偏好、约束和反馈，不存寒暄、问题、代码或临时状态。`<memory_index>` 仅作线索，依赖细节前用 `memory_recall`；删除用 `memory_delete`。""",
)

DEFERRED_TOOL_GUIDE = ""
