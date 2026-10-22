"""Knowledge base access resolution.

Owner/editor/viewer come from ``kb_members`` rows. Subscribers and visitors (any signed-in user)
only read a KB while it is public *and* approved and ``KB_ALLOW_PUBLIC`` is on; this is checked on
every request so unpublishing, delisting or disabling public KBs revokes access immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.infra.knowledge_base import users
from src.infra.knowledge_base.errors import KBForbiddenError, KBNotFoundError
from src.infra.knowledge_base.storage import get_kb_storage
from src.kernel.config import settings
from src.kernel.schemas.knowledge_base import (
    KB_ROLE_EDITOR,
    KB_ROLE_OWNER,
    KB_ROLE_SUBSCRIBER,
    KB_ROLE_VIEWER,
    KB_ROLE_VISITOR,
    KB_STATUS_ACTIVE,
    PUBLIC_APPROVED,
)

# role -> (can_read, can_write, can_manage, can_share, can_chat)
ROLE_CAPABILITIES: dict[str, tuple[bool, bool, bool, bool, bool]] = {
    KB_ROLE_OWNER: (True, True, True, True, True),
    KB_ROLE_EDITOR: (True, True, False, False, True),
    KB_ROLE_VIEWER: (True, False, False, False, True),
    KB_ROLE_SUBSCRIBER: (True, False, False, False, True),
    KB_ROLE_VISITOR: (True, False, False, False, False),
}


@dataclass(frozen=True)
class KBAccess:
    kb: dict[str, Any]
    role: str
    can_read: bool
    can_write: bool
    can_manage: bool
    can_share: bool = False
    can_chat: bool = False

    @property
    def kb_id(self) -> str:
        return str(self.kb["_id"])


def is_publicly_readable(kb: dict[str, Any]) -> bool:
    """Public, approved, active, and the global public switch is on."""
    return bool(
        getattr(settings, "KB_ALLOW_PUBLIC", True)
        and kb.get("status") == KB_STATUS_ACTIVE
        and kb.get("visibility") == "public"
        and kb.get("public_status") == PUBLIC_APPROVED
    )


def _access(kb: dict[str, Any], role: str) -> KBAccess:
    read, write, manage, share, chat = ROLE_CAPABILITIES[role]
    return KBAccess(
        kb=kb,
        role=role,
        can_read=read,
        can_write=write,
        can_manage=manage,
        can_share=share,
        can_chat=chat,
    )


async def resolve_kb_access(user_id: str, kb_id: str) -> KBAccess | None:
    """Return the caller's access to an active KB, or ``None`` (indistinguishable from absent)."""
    storage = get_kb_storage()
    kb = await storage.get_base(kb_id)
    if kb is None or kb.get("status") != KB_STATUS_ACTIVE:
        return None
    member = await storage.get_member(kb_id, user_id)
    role = str(member.get("role")) if member else None
    if role is not None and role not in ROLE_CAPABILITIES:
        return None
    if role == KB_ROLE_OWNER:
        resolved = KB_ROLE_OWNER
    elif role in (KB_ROLE_EDITOR, KB_ROLE_VIEWER):
        resolved = role
    elif is_publicly_readable(kb):
        resolved = KB_ROLE_SUBSCRIBER if role == KB_ROLE_SUBSCRIBER else KB_ROLE_VISITOR
    else:
        return None
    if not await users.is_user_active(user_id):
        return None
    return _access(kb, resolved)


async def require_kb_access(
    user_id: str,
    kb_id: str,
    *,
    write: bool = False,
    manage: bool = False,
    chat: bool = False,
) -> KBAccess:
    """Resolve access or raise: no access -> not found; insufficient role -> forbidden."""
    access = await resolve_kb_access(user_id, kb_id)
    if access is None or not access.can_read:
        raise KBNotFoundError()
    if manage and not access.can_manage:
        raise KBForbiddenError()
    if write and not access.can_write:
        raise KBForbiddenError()
    if chat and not access.can_chat:
        raise KBForbiddenError("请先订阅该知识库后再使用对话检索")
    return access
