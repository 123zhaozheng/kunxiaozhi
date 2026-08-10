"""Persona prompt helpers.

角色身份通过 middleware 注入，与基础提示词解耦。
通过 register_harness_profile 移除 BASE_AGENT_PROMPT 中的身份声明，
让 persona 系统完全控制角色身份，不再冲突。

最终 system message 结构：
  [Block 0] SANDBOX/DEFAULT/FAST_SYSTEM_PROMPT + BEHAVIOR_GUIDE      ← 全局稳定
  [Block 1-3] deepagents 内部 (write_todos, conventions, task)        ← 全局稳定
  [Block 4]   ## Persona (角色 + 行为合并)                             ← 同 persona 缓存命中
  [Block 5]   Skills                                                  ← 同 session 缓存命中
  [Block 6]   Memory guide                                            ← 同用户缓存命中
  [Block 7+]  Memory index / Tool search                              ← 每 turn 变化
"""


from src.agents.core.harness_prompt_overrides import (
    DEFAULT_HARNESS_BEHAVIOR_GUIDE,
)
from src.kernel.schemas.persona_preset import (
    DEFAULT_PREFERRED_AGENT_ID,
    PREFERRED_AGENT_IDS,
    PreferredAgentId,
)

DEFAULT_ROLE = "你是具备工具和技能的智能助手。"


def resolve_persona_agent_id(
    requested_agent_id: str | None,
    preferred_agent_id: str | None,
) -> PreferredAgentId:
    """Resolve agent id for persona-bound chat.

    Backend authority shared by plaza use, session create, and future WeCom binding.
    Preferred agent wins when valid; otherwise fall back to requested, then fast.
    Missing/invalid preferred is treated as fast without side effects.
    """
    if preferred_agent_id in PREFERRED_AGENT_IDS:
        return preferred_agent_id  # type: ignore[return-value]
    if requested_agent_id in PREFERRED_AGENT_IDS:
        return requested_agent_id  # type: ignore[return-value]
    return DEFAULT_PREFERRED_AGENT_ID

_PERSONA_HEADING = "## Persona"

# ---------------------------------------------------------------------------
# Strip the identity line from BASE_AGENT_PROMPT so persona has full control.
#
# Original first line: "You are a deep agent, an AI assistant that helps
# users accomplish tasks using tools. You respond with text and tool calls.
# The user can see your responses and tool outputs in real time."
#
# We keep everything else (Core Behavior, Professional Objectivity, Doing
# Tasks, etc.) because those are valuable behavioral guardrails that don't
# conflict with persona roles.
#
# Registering the same profile under every model adapter's resolved provider
# keeps the core harness consistent across Anthropic, OpenAI-compatible, and
# Google models. Model-specific deepagents profiles still merge their suffixes
# on top of this shared base.
# ---------------------------------------------------------------------------

_BEHAVIOR_GUIDE = DEFAULT_HARNESS_BEHAVIOR_GUIDE

def split_persona_prompt(system_prompt: str) -> tuple[str, str]:
    """Split a persona system_prompt into role identity and behavior body.

    The first paragraph (before the first blank line) is the *role identity*.
    Everything after the first blank line is *behavior instructions*.

    Returns (role, behavior).  Either may be empty.
    """
    text = system_prompt.strip()
    if not text:
        return "", ""

    parts = text.split("\n\n", 1)
    role = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""
    return role, body


def build_persona_prompt_sections(system_prompt: str | None) -> list[str]:
    """Build persona sections as content blocks for injection.

    Role and behavior are merged into a **single block** for stronger signal.
    Splitting into two blocks dilutes the persona identity — the model may
    latch onto the default role before reaching a separate behavior block.

    Always returns exactly one block (with default role when no persona).
    """
    role, body = split_persona_prompt(system_prompt or "")
    effective_role = role if role else DEFAULT_ROLE

    if body:
        return [f"{_PERSONA_HEADING}\n\n{effective_role}\n\n{body}"]
    return [f"{_PERSONA_HEADING}\n\n{effective_role}"]


def build_persona_prompt_section(system_prompt: str | None) -> str:
    """Legacy single-section builder. Prefer ``build_persona_prompt_sections``."""
    return build_persona_prompt_sections(system_prompt)[0]
