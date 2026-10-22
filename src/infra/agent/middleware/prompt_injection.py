"""System prompt injection middleware — memory, sandbox MCP, env vars, sections."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import SystemMessage

from src.infra.agent.middleware._helpers import (
    _append_system_text_block,
    _append_system_text_blocks,
    _normalize_prompt_text,
    _system_message_to_blocks,
)

logger = logging.getLogger(__name__)


class SectionPromptMiddleware(AgentMiddleware):
    """Append one or more deterministic prompt sections as separate system blocks.

    Each section becomes its own content block in the system message, enabling
    fine-grained KV cache breakpoints.  Sections are normalized (trailing
    whitespace stripped) at construction time and batch-appended in a single
    pass to avoid O(n²) block-list rebuilding.
    """

    def __init__(self, *, sections: list[str] | tuple[str, ...]) -> None:
        super().__init__()
        self._sections = tuple(
            _normalize_prompt_text(section) for section in sections if section.strip()
        )

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        if not self._sections:
            return await handler(request)

        # Batch-append all sections in one pass (avoids repeated _system_message_to_blocks)
        blocks = _system_message_to_blocks(request.system_message)
        blocks.extend({"type": "text", "text": section} for section in self._sections)
        request = request.override(system_message=SystemMessage(content=blocks))
        return await handler(request)


class MemoryIndexMiddleware(AgentMiddleware):
    """Injects the native memory index into the system prompt at request time.

    Uses ``NativeMemoryBackend.build_memory_index(user_id)`` which has its own
    5-minute per-user cache, so repeated calls are essentially free after the first.
    Only active when the native backend is selected and the index feature is enabled.
    """

    def __init__(self, *, user_id: str | None) -> None:
        super().__init__()
        self._user_id = user_id

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        if not self._user_id:
            return await handler(request)

        index_str = await _build_memory_index_for_user(self._user_id)
        if not index_str:
            return await handler(request)

        new_system_message = _append_system_text_block(request.system_message, index_str)
        request = request.override(system_message=new_system_message)
        return await handler(request)


async def _build_memory_index_for_user(user_id: str) -> str:
    """Build memory index string for a user. Returns empty string on any failure."""
    try:
        from src.infra.memory.tools import _get_backend

        backend = await _get_backend()
        if backend is None or backend.name != "native":
            return ""

        from src.infra.memory.client.native import NativeMemoryBackend

        if not isinstance(backend, NativeMemoryBackend):
            return ""
        index = await backend.build_memory_index(user_id)
        return index if index else ""
    except Exception:
        logger.warning("[Memory] Failed to build memory index for user %s", user_id, exc_info=True)
        return ""


class SandboxMCPMiddleware(AgentMiddleware):
    """Injects sandbox tool descriptions into the system prompt at request time.

    By injecting via middleware (instead of baking into the base system prompt string),
    the sandbox tools end up at the TAIL of the final system message — after
    deepagent's BASE_AGENT_PROMPT and all other middleware injections (memory, subagent,
    summarization, etc.).  This maximizes KV cache hit rates because changes to sandbox tools
    only invalidate the tail of the cache, not the stable prefix.

    ``build_sandbox_mcp_prompt`` has its own per-user 30-minute cache, so repeated
    ``awrap_model_call`` invocations within a session are essentially free.
    """

    def __init__(self, *, backend: Any, user_id: str) -> None:
        super().__init__()
        self._backend = backend
        self._user_id = user_id

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        from src.infra.tool.sandbox_mcp_prompt import build_sandbox_mcp_prompt_sections

        prompt_sections = await build_sandbox_mcp_prompt_sections(self._backend, self._user_id)
        if prompt_sections:
            new_system_message = _append_system_text_blocks(request.system_message, prompt_sections)
            request = request.override(system_message=new_system_message)
        return await handler(request)


class EnvVarPromptMiddleware(AgentMiddleware):
    """Inject configured environment variable keys into the system prompt.

    Only key names are included. Values are never read as plaintext here.
    """

    def __init__(self, *, user_id: str) -> None:
        super().__init__()
        self._user_id = user_id

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        from src.infra.tool.env_var_prompt import build_env_var_prompt_sections

        prompt_sections = await build_env_var_prompt_sections(self._user_id)
        if prompt_sections:
            new_system_message = _append_system_text_blocks(request.system_message, prompt_sections)
            request = request.override(system_message=new_system_message)
        return await handler(request)


KB_PROMPT_MAX_ITEMS = 10
KB_PROMPT_DESC_CHARS = 60
KB_PROMPT_NAME_CHARS = 40


def _prompt_safe(text: str | None, limit: int) -> str:
    """Flatten user-authored KB text (names may come from other users' public KBs)."""
    cleaned = re.sub(r"[\x00-\x1f\x7f<>]", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit] + "…" if len(cleaned) > limit else cleaned


def build_knowledge_base_prompt(scope: list[Any], mentioned: bool) -> str:
    """Prompt section for the chat-visible KBs; empty when there are none."""
    if not scope:
        return ""
    parts = []
    for kb in scope[:KB_PROMPT_MAX_ITEMS]:
        name = _prompt_safe(kb.name, KB_PROMPT_NAME_CHARS)
        desc = _prompt_safe(kb.description, KB_PROMPT_DESC_CHARS)
        parts.append(f"{name}（{desc}）" if desc else name)
    listing = "；".join(parts)
    if len(scope) > KB_PROMPT_MAX_ITEMS:
        listing += f" 等 {len(scope)} 个"
    lines = [
        "<知识库>",
        f"当前对话可用的个人知识库：{listing}",
        "当问题可能与这些知识库内容相关时，先用 search_knowledge_base 检索，回答中注明来源文件。",
    ]
    if mentioned:
        names = "、".join(
            _prompt_safe(kb.name, KB_PROMPT_NAME_CHARS) for kb in scope[:KB_PROMPT_MAX_ITEMS]
        )
        lines.append(f"用户本轮指定了知识库：{names}。回答前必须先在这些知识库中检索。")
    lines.append("</知识库>")
    return "\n".join(lines)


class KnowledgeBasePromptMiddleware(AgentMiddleware):
    """Append the chat-visible knowledge bases to the system prompt (scope computed once)."""

    def __init__(self, *, user_id: str | None, mentioned_ids: list[str] | None = None) -> None:
        super().__init__()
        self._user_id = user_id
        self._mentioned_ids = list(mentioned_ids or []) or None
        self._section: str | None = None

    async def _compute(self) -> str:
        if not self._user_id:
            return ""
        try:
            from src.infra.knowledge_base.chat_scope import resolve_chat_kb_scope
            from src.kernel.config import settings

            if not getattr(settings, "KB_ENABLED", False):
                return ""
            scope = await resolve_chat_kb_scope(self._user_id, self._mentioned_ids)
            return build_knowledge_base_prompt(scope, bool(self._mentioned_ids))
        except Exception:
            logger.warning("[KB] Failed to build knowledge base prompt", exc_info=True)
            return ""

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        if self._section is None:
            self._section = await self._compute()
        if not self._section:
            return await handler(request)
        new_system_message = _append_system_text_block(request.system_message, self._section)
        return await handler(request.override(system_message=new_system_message))
