"""Authenticated personal-storage APIs and logical content streaming."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from src.api.deps import get_current_user_required, require_permissions
from src.infra.logging import get_logger
from src.infra.storage.s3.service import get_or_init_storage
from src.infra.storage.user_storage import (
    StorageDomainError,
    StorageManagedBySourceError,
    StorageQuotaExceededError,
    UserStorageQuotaService,
    encode_storage_cursor,
)
from src.kernel.schemas.storage import (
    BatchDeleteRequest,
    FileLifecycleStatus,
    FileStatusRequest,
    StorageBatchDeleteResponse,
    StorageDeleteResult,
    StorageErrorDetail,
    StorageFileListQuery,
    StorageFileListResponse,
    StorageFileResponse,
    StorageFileStatus,
    StorageSource,
    StorageUsage,
    UserQuotaUpdate,
)
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)
router = APIRouter()


def get_storage_service() -> UserStorageQuotaService:
    """Factory kept small so route tests can replace the domain service."""
    return UserStorageQuotaService()


def _usage_payload(usage: StorageUsage | None) -> dict[str, Any] | None:
    return usage.model_dump(mode="json") if usage is not None else None


def _storage_http_error(exc: StorageDomainError) -> HTTPException:
    usage = exc.usage if isinstance(exc, StorageQuotaExceededError) else None
    detail = StorageErrorDetail(
        code=exc.code,
        message=str(exc),
        usage=usage,
        required_bytes=getattr(exc, "required_bytes", None),
    )
    return HTTPException(status_code=exc.status_code, detail=detail.model_dump(mode="json"))


@router.get("/usage", response_model=StorageUsage)
async def get_storage_usage(
    current_user: TokenPayload = Depends(get_current_user_required),
    service: UserStorageQuotaService = Depends(get_storage_service),
) -> StorageUsage:
    try:
        return await service.get_usage(current_user.sub, roles=current_user.roles)
    except StorageDomainError as exc:
        raise _storage_http_error(exc) from exc


@router.get("/files", response_model=StorageFileListResponse, response_model_by_alias=False)
async def list_storage_files(
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=50, ge=1, le=100),
    source: StorageSource | None = None,
    category: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=128),
    status: FileLifecycleStatus | None = None,
    sort: str = Query(default="created_at", pattern="^(created_at|size|name)$"),
    descending: bool = True,
    current_user: TokenPayload = Depends(get_current_user_required),
    service: UserStorageQuotaService = Depends(get_storage_service),
) -> StorageFileListResponse:
    try:
        query = StorageFileListQuery(
            cursor=cursor,
            limit=limit,
            source=source,
            category=category,
            search=q,
            status=status,
            sort=sort,
            descending=descending,
        )
        files, has_more, usage = await service.list_files(current_user.sub, query, roles=current_user.roles)
        next_cursor = encode_storage_cursor(files[-1], query) if has_more and files else None
        return StorageFileListResponse(
            files=[StorageFileResponse.model_validate(file.model_dump()) for file in files],
            has_more=has_more,
            next_cursor=next_cursor,
            usage=usage,
        )
    except StorageDomainError as exc:
        raise _storage_http_error(exc) from exc


@router.post("/files/status", response_model=list[StorageFileStatus])
async def get_storage_file_status(
    body: FileStatusRequest,
    current_user: TokenPayload = Depends(get_current_user_required),
    service: UserStorageQuotaService = Depends(get_storage_service),
) -> list[StorageFileStatus]:
    rows = await service.status_for_user(current_user.sub, body.file_ids, body.keys)
    # Unknown identifiers are intentionally omitted, not reported as another
    # user's existence or metadata.
    return [
        StorageFileStatus(
            file_id=str(row.get("_id") or row.get("file_id")),
            status=row.get("status"),
            name=row.get("name"),
            mime_type=row.get("mime_type"),
            size=row.get("size"),
            source=row.get("source"),
            deleted_reason=row.get("deleted_reason"),
        )
        for row in rows
    ]


@router.delete("/files/{file_id}", response_model=StorageDeleteResult)
async def delete_storage_file(
    file_id: str,
    current_user: TokenPayload = Depends(get_current_user_required),
    service: UserStorageQuotaService = Depends(get_storage_service),
) -> StorageDeleteResult:
    try:
        result = await service.delete_file(current_user.sub, file_id)
        if result.get("logical_status") == "managed_by_source":
            raise _storage_http_error(StorageManagedBySourceError("文件必须通过所属功能管理"))
        if result.get("logical_status") == "not_found":
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
        return StorageDeleteResult(**result)
    except HTTPException:
        raise
    except StorageDomainError as exc:
        raise _storage_http_error(exc) from exc


@router.post("/files/batch-delete", response_model=StorageBatchDeleteResponse)
async def batch_delete_storage_files(
    body: BatchDeleteRequest,
    current_user: TokenPayload = Depends(get_current_user_required),
    service: UserStorageQuotaService = Depends(get_storage_service),
) -> StorageBatchDeleteResponse:
    results: list[StorageDeleteResult] = []
    for file_id in body.file_ids[:100]:
        try:
            item_idempotency_key = (
                hashlib.sha256(f"{body.idempotency_key}:{file_id}".encode("utf-8")).hexdigest()
                if body.idempotency_key
                else None
            )
            result = await service.delete_file(
                current_user.sub,
                file_id,
                idempotency_key=item_idempotency_key,
            )
            results.append(StorageDeleteResult(**result))
        except StorageDomainError as exc:
            # Batch operations are explicitly partial: a bad/protected row does
            # not make successful rows look undone.
            results.append(
                StorageDeleteResult(
                    file_id=file_id,
                    logical_status=exc.code,
                    released_bytes=0,
                    physical_status="failed",
                    message=str(exc),
                )
            )
    usage = await service.get_usage(current_user.sub, roles=current_user.roles)
    succeeded = sum(1 for result in results if result.logical_status in {"deleted", "already_deleted"})
    return StorageBatchDeleteResponse(
        results=results,
        usage=usage,
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


@router.put("/admin/users/{user_id}/quota", response_model=StorageUsage)
async def set_storage_quota(
    user_id: str,
    body: UserQuotaUpdate,
    current_user: TokenPayload = Depends(get_current_user_required),
    _: None = Depends(require_permissions("user:write")),
    service: UserStorageQuotaService = Depends(get_storage_service),
) -> StorageUsage:
    try:
        if body.quota_mb is None:
            return await service.clear_user_quota(user_id, roles=current_user.roles)
        return await service.set_user_quota(user_id, body.quota_mb * 1024 * 1024)
    except StorageDomainError as exc:
        raise _storage_http_error(exc) from exc


@router.get("/files/{file_id}/content")
async def stream_storage_file(
    file_id: str,
    request: Request,
    service: UserStorageQuotaService = Depends(get_storage_service),
) -> Any:
    """Stream an active logical file, or return a stable tombstone 410."""
    row = await service.get_content_file(file_id)
    if not row:
        raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
    status = str(row.get("status"))
    if status in {FileLifecycleStatus.DELETE_PENDING.value, FileLifecycleStatus.DELETED.value}:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "file_deleted",
                "message": "文件已删除",
                "file_id": str(row.get("_id") or file_id),
                "name": row.get("name"),
                "mime_type": row.get("mime_type"),
                "size": row.get("size"),
            },
            headers={"Cache-Control": "private, no-store"},
        )
    if status != FileLifecycleStatus.ACTIVE.value:
        raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不可用"})
    blob = await service.storage.get_blob(str(row.get("blob_id")))
    if not blob or blob.get("status") in {"missing", "purged"}:
        raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
    object_storage = await get_or_init_storage()
    key = str(blob.get("storage_key") or row.get("storage_key") or "")
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    filename = str(row.get("name") or "download")
    mime_type = str(row.get("mime_type") or "application/octet-stream")
    if object_storage.is_local:
        try:
            path = object_storage.get_file_path(key)
            if not path.exists():
                raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
            return FileResponse(path=str(path), media_type=mime_type, filename=filename, headers=headers)
        except HTTPException:
            raise
        except (OSError, ValueError) as exc:
            logger.warning("Managed local content path rejected for %s: %s", file_id, exc)
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"}) from exc
    try:
        if not await object_storage.file_exists(key):
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Managed object existence check failed for %s: %s", file_id, exc)
        raise HTTPException(status_code=503, detail={"code": "storage_unavailable", "message": "文件存储暂不可用"}) from exc
    return StreamingResponse(object_storage.download_stream(key), media_type=mime_type, headers=headers)
