"""Scheduled task administration: cross-user overview and intervention.

Every endpoint needs ``scheduled_task:admin``. Interventions are recorded by
``ScheduledTaskService`` as one structured ``[ScheduledTaskAdmin]`` log line each.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import require_permissions
from src.api.routes.opensandbox_admin import load_usernames, user_ids_matching_username
from src.infra.logging import get_logger
from src.infra.scheduler.persona import persona_briefs
from src.infra.scheduler.schedule_spec import ScheduleValidationError
from src.infra.scheduler.service import (
    ScheduledTaskDisabledError,
    get_scheduled_task_service,
)
from src.infra.scheduler.storage import get_scheduled_task_storage
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    ScheduledTask,
    ScheduledTaskAdminItem,
    ScheduledTaskAdminListResponse,
    ScheduledTaskAdminPauseRequest,
    ScheduledTaskStatus,
    TaskRunListResponse,
)
from src.kernel.schemas.user import TokenPayload

_require_admin = require_permissions("scheduled_task:admin")

router = APIRouter(dependencies=[Depends(_require_admin)])

logger = get_logger(__name__)

_NOT_FOUND = "定时任务不存在或已被删除"


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


async def _persona_info_by_owner(tasks: list[ScheduledTask]) -> dict[tuple[str, str], dict]:
    """``{(owner_id, persona_id): brief}``; availability means "can the *owner* still use it"."""
    ids_by_owner: dict[str, list[str]] = {}
    for task in tasks:
        if task.persona_preset_id:
            ids_by_owner.setdefault(task.owner_id, []).append(task.persona_preset_id)
    owners = list(ids_by_owner)
    results = await asyncio.gather(
        *(persona_briefs(ids_by_owner[owner], user_id=owner, is_admin=False) for owner in owners)
    )
    briefs: dict[tuple[str, str], dict] = {}
    for owner, owner_briefs in zip(owners, results, strict=True):
        for persona_id, brief in owner_briefs.items():
            briefs[(owner, persona_id)] = dict(brief)

    # The owner may no longer see a persona, but the administrator still wants its name.
    nameless = sorted({pid for (_, pid), brief in briefs.items() if not brief.get("name")})
    if nameless:
        try:
            fallback = await persona_briefs(nameless, user_id="", is_admin=True)
        except Exception:  # noqa: BLE001 - display only
            fallback = {}
        for (owner, persona_id), brief in briefs.items():
            if not brief.get("name") and fallback.get(persona_id, {}).get("name"):
                brief["name"] = fallback[persona_id]["name"]
                brief["avatar"] = fallback[persona_id].get("avatar")
    return briefs


async def _build_items(tasks: list[ScheduledTask]) -> list[ScheduledTaskAdminItem]:
    service = get_scheduled_task_service()
    usernames = await load_usernames(sorted({t.owner_id for t in tasks}))
    briefs = await _persona_info_by_owner(tasks)
    items: list[ScheduledTaskAdminItem] = []
    for task in tasks:
        response = service.to_response(task)
        item = ScheduledTaskAdminItem(
            **response.model_dump(),
            owner_id=task.owner_id,
            owner_name=usernames.get(task.owner_id),
        )
        brief = (
            briefs.get((task.owner_id, task.persona_preset_id)) if task.persona_preset_id else None
        )
        if brief is not None:
            item.persona_name = brief.get("name")
            item.persona_avatar = brief.get("avatar")
            item.persona_available = brief.get("available")
        items.append(item)
    return items


def _actor_name(user: TokenPayload) -> str:
    return user.username or user.sub


@router.get("", response_model=ScheduledTaskAdminListResponse)
async def list_tasks(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None, description="任务名称，模糊匹配"),
    owner: Optional[str] = Query(None, description="创建人用户名，模糊匹配"),
    task_status: Optional[ScheduledTaskStatus] = Query(None, alias="status"),
    last_run_status: Optional[RunStatus] = Query(None),
) -> ScheduledTaskAdminListResponse:
    owner_ids: Optional[list[str]] = None
    if owner and owner.strip():
        owner_ids = await user_ids_matching_username(owner)
    tasks, total = await get_scheduled_task_storage().list_tasks_admin(
        skip=skip,
        limit=limit,
        name_query=q,
        owner_ids=owner_ids,
        status=task_status.value if task_status else None,
        last_run_status=last_run_status.value if last_run_status else None,
    )
    return ScheduledTaskAdminListResponse(
        items=await _build_items(tasks), total=total, skip=skip, limit=limit
    )


@router.post("/{task_id}/pause", response_model=ScheduledTaskAdminItem)
async def pause_task(
    task_id: str,
    body: ScheduledTaskAdminPauseRequest,
    admin: TokenPayload = Depends(_require_admin),
) -> ScheduledTaskAdminItem:
    try:
        task = await get_scheduled_task_service().admin_pause(
            task_id, body.reason, _actor_name(admin)
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    if task is None:
        raise _not_found()
    return (await _build_items([task]))[0]


@router.post("/{task_id}/resume", response_model=ScheduledTaskAdminItem)
async def resume_task(
    task_id: str,
    admin: TokenPayload = Depends(_require_admin),
) -> ScheduledTaskAdminItem:
    try:
        task = await get_scheduled_task_service().admin_resume(task_id, _actor_name(admin))
    except (ValueError, ScheduleValidationError, ScheduledTaskDisabledError) as exc:
        raise _bad_request(exc) from exc
    if task is None:
        raise _not_found()
    return (await _build_items([task]))[0]


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    admin: TokenPayload = Depends(_require_admin),
) -> None:
    if not await get_scheduled_task_service().admin_delete(task_id, _actor_name(admin)):
        raise _not_found()


@router.get("/{task_id}/runs", response_model=TaskRunListResponse)
async def list_runs(
    task_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> TaskRunListResponse:
    storage = get_scheduled_task_storage()
    if await storage.get_task(task_id) is None:
        raise _not_found()
    items, total = await storage.list_runs(task_id, None, skip, limit)
    return TaskRunListResponse(items=items, total=total)
