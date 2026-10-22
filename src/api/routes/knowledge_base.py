"""Personal knowledge base REST API (mounted at ``/api/kb``)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from src.api.deps import get_current_user_required
from src.infra.knowledge_base import drafts, notifications, quota, service, sharing
from src.infra.knowledge_base.errors import (
    KBBusyError,
    KBConflictError,
    KBDisabledError,
    KBDocumentNotFoundError,
    KBDraftExpiredError,
    KBForbiddenError,
    KBNotFoundError,
    KBQuotaError,
    KBTaskNotFoundError,
    KBValidationError,
)
from src.infra.logging import get_logger
from src.infra.storage.content_url import content_disposition
from src.kernel.schemas.knowledge_base import (
    KBChatEnabledUpdate,
    KBChunkListResponse,
    KBConfig,
    KBContentResponse,
    KBCreate,
    KBDeleteDocumentsResponse,
    KBDocIdsRequest,
    KBDocument,
    KBDocumentListResponse,
    KBDraft,
    KBDraftConfirmRequest,
    KBDraftConfirmResponse,
    KBImportableFileListResponse,
    KBImportRequest,
    KBImportResponse,
    KBListResponse,
    KBMember,
    KBMemberAdd,
    KBMemberAddResponse,
    KBMemberListResponse,
    KBMemberUpdate,
    KBNotificationListResponse,
    KBPlazaResponse,
    KBQuotaResponse,
    KBSearchRequest,
    KBSearchResponse,
    KBTask,
    KBTaskEnqueueResponse,
    KBTaskListResponse,
    KBUnreadCount,
    KBUpdate,
    KBUploadResponse,
    KBUserResolveRequest,
    KBUserResolveResponse,
    KnowledgeBase,
)
from src.kernel.schemas.user import TokenPayload

router = APIRouter()
logger = get_logger(__name__)

GENERIC_ERROR = "知识库服务暂时不可用，请稍后重试"


@contextmanager
def _map_errors() -> Iterator[None]:
    """Translate domain errors into the HTTP contract (404 never reveals why)."""
    try:
        yield
    except HTTPException:
        raise
    except KBDisabledError as exc:
        raise HTTPException(
            status_code=403, detail={"code": "kb_disabled", "message": str(exc)}
        ) from exc
    except (KBNotFoundError, KBDocumentNotFoundError, KBTaskNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KBQuotaError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc), "limit": exc.limit},
        ) from exc
    except KBDraftExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KBConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KBValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KBForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KBBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[KB] request failed")
        raise HTTPException(status_code=500, detail=GENERIC_ERROR) from exc


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


# Literal paths are declared before ``/{kb_id}`` so they are never shadowed.


@router.get("/config", response_model=KBConfig)
async def get_config(_: TokenPayload = Depends(get_current_user_required)) -> KBConfig:
    with _map_errors():
        return await service.get_config()


@router.get("", response_model=KBListResponse)
async def list_kbs(
    scope: str = Query("mine"), user: TokenPayload = Depends(get_current_user_required)
) -> KBListResponse:
    with _map_errors():
        return KBListResponse(items=await service.list_kbs(user, scope))


@router.post("", response_model=KnowledgeBase, status_code=201)
async def create_kb(
    body: KBCreate, user: TokenPayload = Depends(get_current_user_required)
) -> KnowledgeBase:
    with _map_errors():
        return await service.create_kb(user, body)


@router.get("/quota", response_model=KBQuotaResponse)
async def get_quota(user: TokenPayload = Depends(get_current_user_required)) -> KBQuotaResponse:
    with _map_errors():
        service.ensure_enabled()
        return await quota.get_quota(user)


@router.post("/users/resolve", response_model=KBUserResolveResponse)
async def resolve_users(
    body: KBUserResolveRequest, _: TokenPayload = Depends(get_current_user_required)
) -> KBUserResolveResponse:
    with _map_errors():
        return await sharing.resolve_users(body.usernames)


@router.get("/plaza", response_model=KBPlazaResponse)
async def list_plaza(
    q: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
    user: TokenPayload = Depends(get_current_user_required),
) -> KBPlazaResponse:
    with _map_errors():
        return await sharing.list_plaza(user, q=q, page=page, page_size=page_size)


@router.get("/notifications", response_model=KBNotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    page: int = Query(1),
    page_size: int = Query(20),
    user: TokenPayload = Depends(get_current_user_required),
) -> KBNotificationListResponse:
    with _map_errors():
        return await notifications.list_notifications(
            user, unread_only=unread_only, page=page, page_size=page_size
        )


@router.get("/notifications/unread-count", response_model=KBUnreadCount)
async def unread_count(user: TokenPayload = Depends(get_current_user_required)) -> KBUnreadCount:
    with _map_errors():
        return await notifications.unread_count(user)


@router.post("/notifications/read-all")
async def mark_all_read(user: TokenPayload = Depends(get_current_user_required)) -> dict:
    with _map_errors():
        return {"updated": await notifications.mark_all_read(user)}


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_notification_read(
    notification_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> Response:
    with _map_errors():
        await notifications.mark_read(user, notification_id)
    return Response(status_code=204)


@router.get("/importable-files", response_model=KBImportableFileListResponse)
async def list_importable_files(
    q: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
    user: TokenPayload = Depends(get_current_user_required),
) -> KBImportableFileListResponse:
    with _map_errors():
        return await service.list_importable_files(user, q=q, page=page, page_size=page_size)


@router.get("/drafts/{draft_id}", response_model=KBDraft)
async def get_draft(draft_id: str, user: TokenPayload = Depends(get_current_user_required)):
    with _map_errors():
        return drafts.draft_view(await drafts.get_draft(draft_id, user.sub))


@router.post("/drafts/{draft_id}/confirm", response_model=KBDraftConfirmResponse)
async def confirm_draft(
    draft_id: str,
    body: KBDraftConfirmRequest,
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        document, kb_id = await drafts.confirm_draft(user, draft_id, body.kb_id, body.title)
    return KBDraftConfirmResponse(document=document, kb_id=kb_id)


@router.post("/drafts/{draft_id}/cancel", response_model=KBDraft)
async def cancel_draft(draft_id: str, user: TokenPayload = Depends(get_current_user_required)):
    with _map_errors():
        return drafts.draft_view(await drafts.cancel_draft(draft_id, user.sub))


@router.get("/{kb_id}", response_model=KnowledgeBase)
async def get_kb(kb_id: str, user: TokenPayload = Depends(get_current_user_required)):
    with _map_errors():
        return await service.get_kb(user, kb_id)


@router.patch("/{kb_id}", response_model=KnowledgeBase)
async def update_kb(
    kb_id: str, body: KBUpdate, user: TokenPayload = Depends(get_current_user_required)
):
    with _map_errors():
        return await service.update_kb(user, kb_id, body)


@router.patch("/{kb_id}/chat-enabled", response_model=KnowledgeBase)
async def set_chat_enabled(
    kb_id: str,
    body: KBChatEnabledUpdate,
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        return await service.set_chat_enabled(user, kb_id, body.enabled)


@router.delete("/{kb_id}", status_code=204)
async def delete_kb(
    kb_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> Response:
    with _map_errors():
        await service.delete_kb(user, kb_id)
    return Response(status_code=204)


@router.get("/{kb_id}/documents", response_model=KBDocumentListResponse)
async def list_documents(
    kb_id: str,
    status: str | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(50),
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        return await service.list_documents(
            user, kb_id, status=status, q=q, page=page, page_size=page_size
        )


@router.post("/{kb_id}/documents/upload", response_model=KBUploadResponse, status_code=201)
async def upload_document(
    kb_id: str,
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        result, created = await service.upload_document(
            user, kb_id, file, content_length=_content_length(request)
        )
    if not created:
        response.status_code = 200
    return result


@router.post("/{kb_id}/documents/retry", response_model=KBTaskEnqueueResponse)
async def retry_documents(
    kb_id: str,
    body: KBDocIdsRequest | None = None,
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        return await service.retry_documents(user, kb_id, body.doc_ids if body else [])


@router.post("/{kb_id}/documents/delete", response_model=KBDeleteDocumentsResponse)
async def delete_documents(
    kb_id: str, body: KBDocIdsRequest, user: TokenPayload = Depends(get_current_user_required)
):
    with _map_errors():
        return await service.delete_documents(user, kb_id, body.doc_ids)


@router.get("/{kb_id}/documents/{doc_id}", response_model=KBDocument)
async def get_document(
    kb_id: str, doc_id: str, user: TokenPayload = Depends(get_current_user_required)
):
    with _map_errors():
        return await service.get_document(user, kb_id, doc_id)


@router.get("/{kb_id}/documents/{doc_id}/content", response_model=KBContentResponse)
async def get_document_content(
    kb_id: str,
    doc_id: str,
    offset: int = Query(1),
    limit: int = Query(400),
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        return await service.get_content(user, kb_id, doc_id, offset=offset, limit=limit)


@router.get("/{kb_id}/documents/{doc_id}/chunks", response_model=KBChunkListResponse)
async def list_document_chunks(
    kb_id: str,
    doc_id: str,
    page: int = Query(1),
    page_size: int = Query(20),
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        return await service.list_chunks(user, kb_id, doc_id, page=page, page_size=page_size)


@router.get("/{kb_id}/documents/{doc_id}/download")
async def download_document(
    kb_id: str, doc_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> StreamingResponse:
    with _map_errors():
        doc, stream = await service.open_download(user, kb_id, doc_id)
    headers = {
        "Content-Disposition": content_disposition(doc.get("filename"), "attachment"),
        "X-Content-Type-Options": "nosniff",
    }
    if doc.get("size"):
        headers["Content-Length"] = str(doc["size"])
    return StreamingResponse(stream, media_type="application/octet-stream", headers=headers)


@router.post("/{kb_id}/search", response_model=KBSearchResponse)
async def search(
    kb_id: str, body: KBSearchRequest, user: TokenPayload = Depends(get_current_user_required)
):
    with _map_errors():
        return await service.search(user, kb_id, body)


@router.get("/{kb_id}/tasks", response_model=KBTaskListResponse)
async def list_tasks(
    kb_id: str,
    active: bool = Query(False),
    user: TokenPayload = Depends(get_current_user_required),
):
    with _map_errors():
        return await service.list_tasks(user, kb_id, active=active)


@router.post("/{kb_id}/tasks/{task_id}/cancel", response_model=KBTask)
async def cancel_task(
    kb_id: str, task_id: str, user: TokenPayload = Depends(get_current_user_required)
):
    with _map_errors():
        return await service.cancel_task(user, kb_id, task_id)


@router.post("/{kb_id}/reindex", response_model=KBTaskEnqueueResponse)
async def reindex(kb_id: str, user: TokenPayload = Depends(get_current_user_required)):
    with _map_errors():
        return await service.reindex_kb(user, kb_id)


@router.post("/{kb_id}/documents/import", response_model=KBImportResponse)
async def import_documents(
    kb_id: str, body: KBImportRequest, user: TokenPayload = Depends(get_current_user_required)
) -> KBImportResponse:
    with _map_errors():
        return await service.import_files(user, kb_id, body.file_ids)


@router.get("/{kb_id}/members", response_model=KBMemberListResponse)
async def list_members(
    kb_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> KBMemberListResponse:
    with _map_errors():
        return await sharing.list_members(user, kb_id)


@router.post("/{kb_id}/members", response_model=KBMemberAddResponse)
async def add_members(
    kb_id: str, body: KBMemberAdd, user: TokenPayload = Depends(get_current_user_required)
) -> KBMemberAddResponse:
    with _map_errors():
        return await sharing.add_members(user, kb_id, body)


@router.patch("/{kb_id}/members/{member_id}", response_model=KBMember)
async def update_member(
    kb_id: str,
    member_id: str,
    body: KBMemberUpdate,
    user: TokenPayload = Depends(get_current_user_required),
) -> KBMember:
    with _map_errors():
        return await sharing.update_member(user, kb_id, member_id, body.role)


@router.delete("/{kb_id}/members/{member_id}", status_code=204)
async def remove_member(
    kb_id: str, member_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> Response:
    with _map_errors():
        await sharing.remove_member(user, kb_id, member_id)
    return Response(status_code=204)


@router.post("/{kb_id}/publish", response_model=KnowledgeBase)
async def publish_kb(
    kb_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> KnowledgeBase:
    with _map_errors():
        return await sharing.publish(user, kb_id)


@router.post("/{kb_id}/unpublish", response_model=KnowledgeBase)
async def unpublish_kb(
    kb_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> KnowledgeBase:
    with _map_errors():
        return await sharing.unpublish(user, kb_id)


@router.post("/{kb_id}/subscribe", response_model=KnowledgeBase)
async def subscribe_kb(
    kb_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> KnowledgeBase:
    with _map_errors():
        return await sharing.subscribe(user, kb_id)


@router.delete("/{kb_id}/subscribe", status_code=204)
async def unsubscribe_kb(
    kb_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> Response:
    with _map_errors():
        await sharing.unsubscribe(user, kb_id)
    return Response(status_code=204)
