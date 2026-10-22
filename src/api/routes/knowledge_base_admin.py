"""Admin review of public knowledge bases (mounted at ``/api/admin/kb``).

Every endpoint needs ``knowledge_base:admin``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.api.deps import require_permissions
from src.api.routes.knowledge_base import _map_errors
from src.infra.knowledge_base import sharing
from src.kernel.schemas.knowledge_base import KBAdminListResponse, KBReviewRequest, KnowledgeBase
from src.kernel.schemas.user import TokenPayload

_require_admin = require_permissions("knowledge_base:admin")

router = APIRouter()


@router.get("/public", response_model=KBAdminListResponse)
async def list_public(
    status: str | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
    _: TokenPayload = Depends(_require_admin),
) -> KBAdminListResponse:
    with _map_errors():
        return await sharing.admin_list_public(status=status, q=q, page=page, page_size=page_size)


@router.post("/{kb_id}/approve", response_model=KnowledgeBase)
async def approve(kb_id: str, admin: TokenPayload = Depends(_require_admin)) -> KnowledgeBase:
    with _map_errors():
        return await sharing.admin_approve(admin, kb_id)


@router.post("/{kb_id}/reject", response_model=KnowledgeBase)
async def reject(
    kb_id: str,
    body: KBReviewRequest | None = None,
    admin: TokenPayload = Depends(_require_admin),
) -> KnowledgeBase:
    with _map_errors():
        return await sharing.admin_reject(admin, kb_id, body.reason if body else "")


@router.post("/{kb_id}/delist", response_model=KnowledgeBase)
async def delist(
    kb_id: str,
    body: KBReviewRequest | None = None,
    admin: TokenPayload = Depends(_require_admin),
) -> KnowledgeBase:
    with _map_errors():
        return await sharing.admin_delist(admin, kb_id, body.reason if body else "")
