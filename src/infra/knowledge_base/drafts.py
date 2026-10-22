"""Agent-created "save to knowledge base" drafts (card confirmation).

``save_to_knowledge_base`` only *stores* a draft here; nothing is written to a KB until the user
confirms on the chat card. Status machine (persisted): ``pending -> applying -> confirmed`` or
``pending -> cancelled``; ``expired`` is derived from an overdue pending draft. Every transition
is one conditional update, so concurrent confirms can store the document only once.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import uuid4

from src.infra.knowledge_base import service
from src.infra.knowledge_base.access import require_kb_access
from src.infra.knowledge_base.errors import (
    KBConflictError,
    KBDraftExpiredError,
    KBError,
    KBNotFoundError,
    KBValidationError,
)
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.logging import get_logger
from src.infra.utils.datetime import ensure_utc, utc_now
from src.kernel.schemas.knowledge_base import KBDocument
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

DRAFT_TTL = timedelta(minutes=30)
DRAFT_PURGE_AFTER = timedelta(days=7)
CONTENT_MAX_CHARS = 200_000
PREVIEW_CHARS = 500
TITLE_MAX_CHARS = 80

STATUS_PENDING = "pending"
STATUS_APPLYING = "applying"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"  # derived, never persisted

GENERIC_CONFIRM_ERROR = "保存失败，请稍后重试"
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def sanitize_title(title: str | None) -> str:
    name = _UNSAFE_NAME.sub("_", str(title or "")).strip(" ._")
    if name.lower().endswith(".md"):
        name = name[:-3].strip(" ._")
    return name[:TITLE_MAX_CHARS].strip(" ._") or "未命名文档"


class _BytesStream:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


async def create_draft(
    *,
    owner_id: str,
    session_id: Optional[str],
    title: str,
    content: str,
    suggested_kb_id: Optional[str],
) -> dict[str, Any]:
    now = utc_now()
    doc = {
        "_id": uuid4().hex,
        "owner_id": owner_id,
        "session_id": session_id,
        "title": title,
        "content": content,
        "suggested_kb_id": suggested_kb_id,
        "status": STATUS_PENDING,
        "kb_id": None,
        "document_id": None,
        "last_error": None,
        "created_at": now,
        "expires_at": now + DRAFT_TTL,
        "purge_at": now + DRAFT_PURGE_AFTER,
    }
    await get_kb_storage().insert_draft(doc)
    return doc


def draft_view(doc: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utc_now()
    expires_at = ensure_utc(doc["expires_at"])
    status = doc.get("status", STATUS_PENDING)
    if status == STATUS_PENDING and expires_at <= now:
        status = STATUS_EXPIRED
    content = str(doc.get("content") or "")
    return {
        "draft_id": doc["_id"],
        "status": status,
        "title": doc.get("title") or "",
        "preview": content[:PREVIEW_CHARS],
        "char_count": len(content),
        "suggested_kb_id": doc.get("suggested_kb_id"),
        "kb_id": doc.get("kb_id"),
        "document_id": doc.get("document_id"),
        "expires_at": expires_at,
    }


async def get_draft(draft_id: str, owner_id: str) -> dict[str, Any]:
    service.ensure_enabled()
    doc = await get_kb_storage().get_draft(draft_id, owner_id)
    if doc is None:
        raise KBNotFoundError("草稿不存在或无权查看")
    return doc


async def _raise_not_claimable(draft_id: str, owner_id: str) -> None:
    doc = await get_draft(draft_id, owner_id)
    status = doc.get("status", STATUS_PENDING)
    if status == STATUS_PENDING and ensure_utc(doc["expires_at"]) <= utc_now():
        raise KBDraftExpiredError()
    if status == STATUS_CONFIRMED:
        raise KBConflictError("草稿已确认")
    if status == STATUS_CANCELLED:
        raise KBConflictError("草稿已取消")
    raise KBConflictError("草稿正在处理，请稍候")


async def _release(draft_id: str, owner_id: str, error: str) -> None:
    try:
        await get_kb_storage().transition_draft(
            draft_id, owner_id, STATUS_APPLYING, STATUS_PENDING, {"last_error": error}
        )
    except Exception:  # noqa: BLE001
        logger.exception("[KB] failed to release draft %s", draft_id)


async def confirm_draft(
    user: TokenPayload, draft_id: str, kb_id: str, title: Optional[str] = None
) -> tuple[KBDocument, str]:
    """Store a pending draft in ``kb_id`` exactly once; a duplicate returns the existing document."""
    service.ensure_enabled()
    storage = get_kb_storage()
    if not kb_id:
        raise KBValidationError("请选择知识库")
    await get_draft(draft_id, user.sub)  # 404 for foreign / unknown drafts
    access = await require_kb_access(user.sub, kb_id, write=True)

    claimed = await storage.transition_draft(
        draft_id,
        user.sub,
        STATUS_PENDING,
        STATUS_APPLYING,
        {"last_error": None},
        require_unexpired=True,
    )
    if claimed is None:
        await _raise_not_claimable(draft_id, user.sub)
        raise KBNotFoundError("草稿不存在或无权查看")  # pragma: no cover

    filename = f"{sanitize_title(title or claimed.get('title'))}.md"
    data = str(claimed.get("content") or "").encode("utf-8")
    try:
        response, _created = await service.add_document_from_stream(
            user,
            access,
            filename,
            _BytesStream(data),
            mime="text/markdown",
            source="agent",
            source_ref=draft_id,
        )
    except KBError as exc:
        await _release(draft_id, user.sub, str(exc))
        raise
    except Exception:
        await _release(draft_id, user.sub, GENERIC_CONFIRM_ERROR)
        raise

    await storage.transition_draft(
        draft_id,
        user.sub,
        STATUS_APPLYING,
        STATUS_CONFIRMED,
        {"kb_id": kb_id, "document_id": response.document.id, "last_error": None},
    )
    return response.document, kb_id


async def cancel_draft(draft_id: str, owner_id: str) -> dict[str, Any]:
    service.ensure_enabled()
    storage = get_kb_storage()
    cancelled = await storage.transition_draft(draft_id, owner_id, STATUS_PENDING, STATUS_CANCELLED)
    if cancelled is None:
        doc = await get_draft(draft_id, owner_id)
        return doc  # already confirmed / cancelled / applying: report the current state
    return cancelled
