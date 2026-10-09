"""Scheduled task REST API (owner-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_current_user_required, require_permissions
from src.infra.logging import get_logger
from src.infra.scheduler import drafts
from src.infra.scheduler.persona import ScheduledTaskPersonaError
from src.infra.scheduler.schedule_spec import ScheduleValidationError
from src.infra.scheduler.service import (
    ScheduledTaskDisabledError,
    ScheduledTaskQuotaError,
    get_scheduled_task_service,
    max_tasks_per_user,
    min_interval_hours,
)
from src.infra.scheduler.storage import get_scheduled_task_storage
from src.infra.scheduler.templates import list_templates
from src.kernel.schemas.scheduled_task import (
    ScheduledTaskCreate,
    ScheduledTaskDraftConfirmResponse,
    ScheduledTaskDraftResponse,
    ScheduledTaskListResponse,
    ScheduledTaskResponse,
    ScheduledTaskTemplate,
    ScheduledTaskUpdate,
    SchedulePreviewRequest,
    SchedulePreviewResponse,
    TaskRunListResponse,
)
from src.kernel.schemas.user import TokenPayload

router = APIRouter(dependencies=[Depends(require_permissions("scheduled_task:use", "chat:write"))])

logger = get_logger(__name__)

_NOT_FOUND = "定时任务不存在或已被删除"


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/templates", response_model=list[ScheduledTaskTemplate])
async def get_templates() -> list[ScheduledTaskTemplate]:
    return list_templates()


@router.post("/preview", response_model=SchedulePreviewResponse)
async def preview_schedule(body: SchedulePreviewRequest) -> SchedulePreviewResponse:
    try:
        text, runs = get_scheduled_task_service().preview(body.schedule, body.timezone, body.count)
    except ScheduleValidationError as exc:
        return SchedulePreviewResponse(valid=False, error=str(exc))
    return SchedulePreviewResponse(valid=True, schedule_text=text, next_runs=runs)


@router.get("/drafts/{draft_id}", response_model=ScheduledTaskDraftResponse)
async def get_draft(
    draft_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskDraftResponse:
    try:
        doc = await drafts.get_draft(draft_id, user.sub)
    except drafts.DraftNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ScheduledTaskDraftResponse(**drafts.draft_view(doc))


@router.post("/drafts/{draft_id}/confirm", response_model=ScheduledTaskDraftConfirmResponse)
async def confirm_draft(
    draft_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskDraftConfirmResponse:
    """Apply a chat draft. Only this endpoint (a human click) creates / modifies the task."""
    try:
        doc, task = await drafts.confirm_draft(draft_id, user)
    except (drafts.DraftNotFound, drafts.DraftTaskMissing) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except drafts.DraftExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except (drafts.DraftStateConflict, drafts.DraftConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (
        ScheduleValidationError,
        ScheduledTaskQuotaError,
        ScheduledTaskDisabledError,
        ScheduledTaskPersonaError,
    ) as exc:
        raise _bad_request(exc) from exc
    except Exception as exc:
        logger.exception("[ScheduledTask] confirm draft %s failed", draft_id)
        raise HTTPException(status_code=500, detail=drafts.GENERIC_CONFIRM_ERROR) from exc
    return ScheduledTaskDraftConfirmResponse(
        draft=ScheduledTaskDraftResponse(**drafts.draft_view(doc)),
        task=await get_scheduled_task_service().to_response_with_persona(task, user),
    )


@router.post("/drafts/{draft_id}/cancel", response_model=ScheduledTaskDraftResponse)
async def cancel_draft(
    draft_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskDraftResponse:
    try:
        doc = await drafts.cancel_draft(draft_id, user.sub)
    except drafts.DraftNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except drafts.DraftStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ScheduledTaskDraftResponse(**drafts.draft_view(doc))


@router.get("", response_model=ScheduledTaskListResponse)
async def list_tasks(
    user: TokenPayload = Depends(get_current_user_required),
) -> ScheduledTaskListResponse:
    service = get_scheduled_task_service()
    tasks = await get_scheduled_task_storage().list_tasks(user.sub)
    return ScheduledTaskListResponse(
        items=await service.to_responses_with_persona(tasks, user),
        total=len(tasks),
        limit=max_tasks_per_user(),
        min_interval_hours=min_interval_hours(),
    )


@router.post("", response_model=ScheduledTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: ScheduledTaskCreate, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskResponse:
    service = get_scheduled_task_service()
    try:
        task = await service.create_task(body, user.sub, actor=user)
    except (
        ScheduleValidationError,
        ScheduledTaskQuotaError,
        ScheduledTaskDisabledError,
        ScheduledTaskPersonaError,
    ) as exc:
        raise _bad_request(exc) from exc
    return await service.to_response_with_persona(task, user)


@router.get("/{task_id}", response_model=ScheduledTaskResponse)
async def get_task(
    task_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskResponse:
    task = await get_scheduled_task_storage().get_task(task_id, user.sub)
    if task is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return await get_scheduled_task_service().to_response_with_persona(task, user)


@router.patch("/{task_id}", response_model=ScheduledTaskResponse)
async def update_task(
    task_id: str, body: ScheduledTaskUpdate, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskResponse:
    service = get_scheduled_task_service()
    try:
        task = await service.update_task(task_id, user.sub, body, actor=user)
    except (
        ScheduleValidationError,
        ScheduledTaskDisabledError,
        ScheduledTaskPersonaError,
    ) as exc:
        raise _bad_request(exc) from exc
    if task is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return await service.to_response_with_persona(task, user)


@router.post("/{task_id}/pause", response_model=ScheduledTaskResponse)
async def pause_task(
    task_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskResponse:
    service = get_scheduled_task_service()
    task = await service.set_enabled(task_id, user.sub, False)
    if task is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return await service.to_response_with_persona(task, user)


@router.post("/{task_id}/resume", response_model=ScheduledTaskResponse)
async def resume_task(
    task_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> ScheduledTaskResponse:
    service = get_scheduled_task_service()
    try:
        task = await service.set_enabled(task_id, user.sub, True)
    except (ScheduleValidationError, ScheduledTaskDisabledError) as exc:
        raise _bad_request(exc) from exc
    if task is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return await service.to_response_with_persona(task, user)


@router.post("/{task_id}/run")
async def run_task_now(
    task_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> dict:
    try:
        result = await get_scheduled_task_service().run_now(task_id, user.sub)
    except ScheduledTaskDisabledError as exc:
        raise _bad_request(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if result.get("skipped"):
        reason = result.get("reason")
        if reason == "already_running":
            raise HTTPException(
                status_code=409, detail="这个任务正在执行中，请等它完成后再试"
            )
        if reason == "task_not_found":
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        raise _bad_request(ScheduledTaskDisabledError())
    return result


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str, user: TokenPayload = Depends(get_current_user_required)
) -> None:
    if not await get_scheduled_task_service().delete_task(task_id, user.sub):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)


@router.get("/{task_id}/runs", response_model=TaskRunListResponse)
async def list_runs(
    task_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: TokenPayload = Depends(get_current_user_required),
) -> TaskRunListResponse:
    items, total = await get_scheduled_task_storage().list_runs(task_id, user.sub, skip, limit)
    return TaskRunListResponse(items=items, total=total)
