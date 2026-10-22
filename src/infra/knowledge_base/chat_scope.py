"""Which knowledge bases a chat turn may use (computed live on every tool call, never cached)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.infra.knowledge_base import users
from src.infra.knowledge_base.access import is_publicly_readable
from src.infra.knowledge_base.storage import get_kb_storage
from src.kernel.config import settings
from src.kernel.schemas.knowledge_base import (
    KB_ROLE_EDITOR,
    KB_ROLE_OWNER,
    KB_ROLE_SUBSCRIBER,
    KB_ROLE_VIEWER,
    KB_STATUS_ACTIVE,
)

_READ_ROLES = [KB_ROLE_OWNER, KB_ROLE_EDITOR, KB_ROLE_VIEWER, KB_ROLE_SUBSCRIBER]


@dataclass(frozen=True)
class ChatKB:
    kb_id: str
    name: str
    description: str
    role: str
    doc_count: int
    indexed: bool
    chat_enabled: bool = True


async def resolve_readable_kbs(user_id: str) -> tuple[list[ChatKB], dict[str, dict[str, Any]]]:
    """Every active KB the user can read (member or valid subscriber), plus the raw rows."""
    if not getattr(settings, "KB_ENABLED", False) or not user_id:
        return [], {}
    storage = get_kb_storage()
    memberships = {str(m["kb_id"]): m for m in await storage.list_memberships(user_id, _READ_ROLES)}
    if not memberships or not await users.is_user_active(user_id):
        return [], {}
    bases = await storage.list_bases(list(memberships))
    visible: list[dict[str, Any]] = []
    for kb in bases:
        member = memberships[str(kb["_id"])]
        if kb.get("status") != KB_STATUS_ACTIVE:
            continue
        if member.get("role") == KB_ROLE_SUBSCRIBER and not is_publicly_readable(kb):
            continue
        visible.append(kb)
    stats = await storage.stats_for([str(kb["_id"]) for kb in visible])
    result: list[ChatKB] = []
    rows: dict[str, dict[str, Any]] = {}
    for kb in visible:
        kb_id = str(kb["_id"])
        member = memberships[kb_id]
        rows[kb_id] = kb
        result.append(
            ChatKB(
                kb_id=kb_id,
                name=str(kb.get("name") or ""),
                description=str(kb.get("description") or ""),
                role=str(member.get("role")),
                doc_count=int(stats[kb_id]["doc_count"]),
                indexed=bool(kb.get("embedding")),
                chat_enabled=bool(member.get("chat_enabled", True)),
            )
        )
    return result, rows


async def resolve_chat_kb_scope(user_id: str, mentioned_ids: list[str] | None) -> list[ChatKB]:
    """Mentioned ∩ readable (ignoring ``chat_enabled``), else readable with ``chat_enabled``."""
    readable, _ = await resolve_readable_kbs(user_id)
    if mentioned_ids:
        wanted = set(mentioned_ids)
        return [kb for kb in readable if kb.kb_id in wanted]
    return [kb for kb in readable if kb.chat_enabled]
