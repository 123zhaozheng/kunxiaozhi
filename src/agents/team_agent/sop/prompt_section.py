"""主代理 SOP 使用引导段（compact_zh 中文版）。

仅团队模式且 TEAM_SOP_MODE 开启时拼进主代理 system prompt。
"""

from __future__ import annotations

from src.kernel.config import settings

_COMPACT_ZH_SOP_GUIDANCE_SECTION = """\
## SOP 规划（复杂团队任务）
复杂/多角色任务：先调用 `update_sop`（action=create）提交完整 SOP DAG，确认后再逐步执行；简单问题：`update_sop` 设 `direct_answer=true` 直接回答，不建 DAG、不弹确认。

步骤写法：每步单一职责，产出可验证交付物（expected_output）；依赖显式声明且只引用已存在 step_id；无依赖的独立步骤可并行；assignee 选最匹配角色；步数控制在 [{min_steps}, {max_steps}]；自查粒度，过细合并、过粗拆分。

执行：确认后逐步完成，每个 `task` 描述带上本步要求与前驱关键输出；每步完成后调 `update_sop` 更新步骤状态与输出；步骤失败则更新状态、说明原因，必要时调整后续步骤；全部完成总结交付，`update_sop` 置计划 status=completed。
"""


def build_sop_guidance_section() -> str:
    """构建主代理 system prompt 追加段（compact_zh 中文版）。"""
    return _COMPACT_ZH_SOP_GUIDANCE_SECTION.format(
        max_steps=settings.TEAM_SOP_MAX_STEPS,
        min_steps=settings.TEAM_SOP_MIN_STEPS,
    )
