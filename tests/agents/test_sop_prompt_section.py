"""build_sop_guidance_section 测试（compact_zh 固化版）。"""

from __future__ import annotations

from src.agents.team_agent.sop.prompt_section import build_sop_guidance_section
from src.kernel.config import settings


def test_compact_zh_section_contains_key_constraints() -> None:
    text = build_sop_guidance_section()

    assert "update_sop" in text
    assert "单一职责" in text  # 步骤单一职责
    assert "可验证交付物" in text  # 可验证产出
    assert "step_id" in text  # 依赖引用已存在 step_id
    assert "并行" in text  # 独立步骤并行
    assert "direct_answer" in text  # 简单问题直接回答
    assert "completed" in text  # 收尾置 completed
    assert f"[{settings.TEAM_SOP_MIN_STEPS}, {settings.TEAM_SOP_MAX_STEPS}]" in text


def test_default_section_is_compact_zh() -> None:
    text = build_sop_guidance_section()
    assert "SOP 规划" in text
