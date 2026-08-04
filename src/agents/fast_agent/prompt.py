"""
Fast Agent 系统提示 - 简洁高效

角色身份通过 SectionPromptMiddleware 独立注入（见 persona.py），
基础提示词只包含能力描述，保证全局 KV 缓存稳定。
"""

FAST_SYSTEM_PROMPT = """## 文件
`/workspace`：持久文件；`/skills/`：可编辑技能定义。

记忆：`memory_retain` 仅存长期用户事实、偏好、约束和反馈，不存寒暄、问题、代码或临时状态。`<memory_index>` 仅作线索，依赖细节前用 `memory_recall`；删除用 `memory_delete`。"""
