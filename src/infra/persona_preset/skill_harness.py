"""Runtime prompt harness for Marketplace skills bound to a Persona."""

from collections.abc import Iterable
from typing import Any

from src.kernel.schemas.persona_preset import PersonaSkillHint

PERSONA_SKILL_HINTS_OPTION = "_persona_skill_hints"


def apply_persona_skill_hints_to_agent_options(
    agent_options: dict[str, Any] | None,
    skill_hints: list[PersonaSkillHint],
) -> dict[str, Any]:
    """Put validated runtime hints on the request without enabling any Skill."""
    options = agent_options if agent_options is not None else {}
    if skill_hints:
        options[PERSONA_SKILL_HINTS_OPTION] = [
            hint.model_dump(mode="json") for hint in skill_hints
        ]
    else:
        options.pop(PERSONA_SKILL_HINTS_OPTION, None)
    return options


def build_persona_skill_harness_section(
    raw_hints: object,
    tools: Iterable[Any] | None,
) -> str:
    """Build direct-install guidance only when install_skill is usable."""
    tool_names = {getattr(item, "name", "") for item in (tools or ())}
    if "install_skill" not in tool_names or not isinstance(raw_hints, list):
        return ""

    hints: list[PersonaSkillHint] = []
    for item in raw_hints:
        try:
            hints.append(PersonaSkillHint.model_validate(item))
        except Exception:
            continue
    if not hints:
        return ""

    lines = [
        "## Persona 技能能力",
        "当前 Persona 推荐下列精确技能：任务命中某条描述时，",
        "直接以该技能名调用 `install_skill`，勿先 `find_skills`。",
        "只安装与当前任务相关的技能，按返回路径读取 SKILL.md。",
        "",
    ]
    lines.extend(f"- `{hint.name}`: {hint.description}" for hint in hints)
    return "\n".join(lines)
