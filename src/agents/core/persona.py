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

import importlib
from typing import Any

from src.agents.core.harness_prompt_overrides import (
    build_harness_extra_middleware,
    catalog_for_mode,
)
from src.infra.logging import get_logger
from src.kernel.config import get_active_harness_mode
from src.kernel.schemas.persona_preset import (
    DEFAULT_PREFERRED_AGENT_ID,
    PREFERRED_AGENT_IDS,
    PreferredAgentId,
)

logger = get_logger(__name__)
_HARNESS_MODE = get_active_harness_mode()

_deepagents: Any = None
try:
    _deepagents = importlib.import_module("deepagents")
except ImportError:  # pragma: no cover - compatibility with older deepagents builds
    pass

_HarnessProfile = getattr(_deepagents, "HarnessProfile", None) if _deepagents is not None else None
_register_harness_profile = (
    getattr(_deepagents, "register_harness_profile", None) if _deepagents is not None else None
)


DEFAULT_ROLE = (
    "你是具备工具和技能的智能助手。"
    if _HARNESS_MODE == "compact_zh"
    else "You are an intelligent assistant with tools and skills."
)


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
_CHANNEL_HEADING = "## Delivery channel"


def build_channel_prompt_section(channel_context: Any) -> str | None:
    """Build a run-scoped delivery-channel prompt section."""
    if not isinstance(channel_context, dict) or channel_context.get("channel") != "wecom":
        return None
    return (
        f"{_CHANNEL_HEADING}\n\n"
        "You are replying through WeCom (企业微信) on a mobile chat. Keep the response "
        "readable in short sections. If the user needs a file, call `reveal_file` for exactly "
        "one concrete file. The channel will attempt to deliver that revealed file. Do not use "
        "`reveal_project` to deliver a directory or project, do not expose local paths, and do not "
        "claim delivery succeeded until the tool/channel reports success."
    )

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
def _legacy_behavior_guide() -> str:
    """Restore the pre-compression vendor behavior while keeping persona authority."""
    try:
        from deepagents.graph import BASE_AGENT_PROMPT
    except ImportError:  # pragma: no cover
        return "You have access to tools. Be accurate, complete the task, and verify your work."
    _, separator, body = BASE_AGENT_PROMPT.partition("\n\n")
    return (
        "You have access to tools and can respond with text and tool calls. "
        "The user can see your responses and tool outputs in real time."
        + (separator + body if separator else "")
    )


_BEHAVIOR_GUIDE = (
    _legacy_behavior_guide()
    if _HARNESS_MODE == "legacy"
    else catalog_for_mode(_HARNESS_MODE).behavior_guide
)

if _HarnessProfile is not None and _register_harness_profile is not None:
    # Register on import — this is idempotent (additive merge).
    try:
        from langchain.agents.middleware import TodoListMiddleware as _TodoListMiddleware
    except ImportError:  # pragma: no cover - older langchain
        _TodoListMiddleware = None  # type: ignore[misc, assignment]

    _profile_kwargs: dict[str, Any] = {"base_system_prompt": _BEHAVIOR_GUIDE}
    if _HARNESS_MODE != "legacy":
        _catalog = catalog_for_mode(_HARNESS_MODE)
        _profile_kwargs["tool_description_overrides"] = _catalog.tool_descriptions
        _profile_kwargs["extra_middleware"] = lambda: build_harness_extra_middleware(
            _HARNESS_MODE
        )
    if _HARNESS_MODE != "legacy" and _TodoListMiddleware is not None:
        _profile_kwargs["excluded_middleware"] = frozenset({_TodoListMiddleware})

    _shared_profile = _HarnessProfile(**_profile_kwargs)
    for _provider_key in ("anthropic", "openai", "google_genai"):
        _register_harness_profile(_provider_key, _shared_profile)
    logger.info("[Harness] mode=%s", _HARNESS_MODE)


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
