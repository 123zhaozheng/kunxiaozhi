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
        "## Persona skill capabilities",
        "The active Persona recommends the exact Marketplace skills below.",
        "When the user's task matches one of these descriptions, call "
        "`install_skill` with that exact skill name before doing the task.",
        "Do not call `find_skills` first for these named skills. Install only skills "
        "that are relevant to the current task, then follow the returned path and read SKILL.md.",
        "",
    ]
    lines.extend(f"- `{hint.name}`: {hint.description}" for hint in hints)
    return "\n".join(lines)
