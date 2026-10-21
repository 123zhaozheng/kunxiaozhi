"""Chat-created scheduled task drafts (D8: card confirmation).

The ``draft_scheduled_task`` agent tool only *stores* a draft here. Nothing is created or
modified until the user clicks "confirm" on the chat card, which calls
``POST /api/scheduled-tasks/drafts/{id}/confirm`` -> :func:`confirm_draft`.

Status machine (persisted): ``pending -> confirming -> confirmed`` / ``pending -> cancelled``.
``expired`` is never persisted; :func:`draft_view` derives it from a pending, overdue draft.
Every transition is a single atomic ``find_one_and_update`` so concurrent confirms (double
click, two tabs, a retried request) can only create/modify the task once.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal, Optional
from uuid import uuid4

from pymongo import DESCENDING, ReturnDocument

from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import ensure_utc, utc_now
from src.kernel.config import settings
from src.kernel.schemas.scheduled_task import (
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
)
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

# ruff: noqa: N818 - draft exception names are part of the P5 contract (no Error suffix)

DRAFTS_COLLECTION = "scheduled_task_drafts"
DRAFT_TTL = timedelta(minutes=30)
DRAFT_PURGE_AFTER = timedelta(days=30)

STATUS_PENDING = "pending"
STATUS_CONFIRMING = "confirming"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"  # derived, never persisted

GENERIC_CONFIRM_ERROR = "确认失败，请稍后重试"


# ── errors (Chinese, shown to the user as-is) ─────────────────────────────


class DraftError(Exception):
    """Base class for draft lifecycle errors."""

    default_message = "草稿操作失败"

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default_message)


class DraftNotFound(DraftError):
    default_message = "草稿不存在或无权查看"


class DraftExpired(DraftError):
    default_message = "草稿已过期，请让助手重新生成"


class DraftStateConflict(DraftError):
    """The draft is not pending (already confirmed / cancelled / being processed)."""

    def __init__(self, status: str) -> None:
        self.status = status
        if status == STATUS_CONFIRMED:
            message = "草稿已确认"
        elif status == STATUS_CANCELLED:
            message = "草稿已取消"
        else:
            message = "草稿正在处理，请稍候"
        super().__init__(message)


class DraftConflict(DraftError):
    """The task changed after the draft was generated (signature mismatch)."""

    default_message = "任务已在别处修改，请让助手重新生成草稿"


class DraftTaskMissing(DraftError):
    default_message = "定时任务不存在或已被删除"


# ── storage ──────────────────────────────────────────────────────────────


class ScheduledTaskDraftStorage:
    def __init__(self) -> None:
        self._col = None

    @property
    def col(self):
        if self._col is None:
            self._col = get_mongo_client()[settings.MONGODB_DB][DRAFTS_COLLECTION]
        return self._col

    async def ensure_indexes(self) -> None:
        await self.col.create_index([("owner_id", 1), ("created_at", DESCENDING)])
        await self.col.create_index("purge_at", expireAfterSeconds=0)
        logger.info("[ScheduledTaskDraftStorage] indexes ensured")

    async def create(self, doc: dict[str, Any]) -> None:
        await self.col.insert_one(doc)

    async def get(self, draft_id: str, owner_id: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"_id": draft_id, "owner_id": owner_id})

    async def transition(
        self,
        draft_id: str,
        owner_id: str,
        from_status: str,
        to_status: str,
        extra_set: Optional[dict[str, Any]] = None,
        require_unexpired: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Atomically move ``from_status -> to_status``; returns the new doc or None."""
        query: dict[str, Any] = {"_id": draft_id, "owner_id": owner_id, "status": from_status}
        if require_unexpired:
            query["expires_at"] = {"$gt": utc_now()}
        return await self.col.find_one_and_update(
            query,
            {"$set": {**(extra_set or {}), "status": to_status}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_fields(
        self, draft_id: str, owner_id: str, fields: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        return await self.col.find_one_and_update(
            {"_id": draft_id, "owner_id": owner_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )


_storage: Optional[ScheduledTaskDraftStorage] = None


def get_draft_storage() -> ScheduledTaskDraftStorage:
    global _storage
    if _storage is None:
        _storage = ScheduledTaskDraftStorage()
    return _storage


# ── helpers ──────────────────────────────────────────────────────────────


def task_signature(task: ScheduledTask) -> str:
    """Hash of the user-editable fields.

    ``updated_at`` is deliberately excluded: every run refreshes it, which would make an
    update draft look stale although the user did not touch the task.
    """
    payload = {
        "name": task.name,
        "prompt": task.prompt,
        "schedule": task.schedule.model_dump(mode="json"),
        "timezone": task.timezone,
        "persona_preset_id": task.persona_preset_id,
        "enabled": task.enabled,
        "status": getattr(task.status, "value", task.status),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()  # noqa: S324 - change detection only


async def build_draft(
    *,
    owner_id: str,
    session_id: Optional[str],
    action: Literal["create", "update"],
    task_id: Optional[str],
    payload: dict[str, Any],
    summary: dict[str, Any],
    before: Optional[dict[str, Any]] = None,
    changes: Optional[list[str]] = None,
    base_signature: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a pending draft and return the stored document."""
    now = utc_now()
    doc = {
        "_id": uuid4().hex,
        "owner_id": owner_id,
        "session_id": session_id,
        "action": action,
        "task_id": task_id,
        "payload": payload,
        "base_signature": base_signature,
        "summary": summary,
        "before": before,
        "changes": list(changes or []),
        "status": STATUS_PENDING,
        "last_error": None,
        "result_task_id": None,
        "created_at": now,
        "expires_at": now + DRAFT_TTL,
        "purge_at": now + DRAFT_PURGE_AFTER,
    }
    await get_draft_storage().create(doc)
    return doc


def draft_view(doc: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    """Public representation; a pending draft past its deadline is shown as ``expired``."""
    now = now or utc_now()
    expires_at = ensure_utc(doc["expires_at"])
    status = doc.get("status", STATUS_PENDING)
    if status == STATUS_PENDING and expires_at <= now:
        status = STATUS_EXPIRED
    return {
        "id": doc["_id"],
        "action": doc["action"],
        "task_id": doc.get("task_id"),
        "status": status,
        "summary": doc.get("summary") or {},
        "before": doc.get("before"),
        "changes": list(doc.get("changes") or []),
        "expires_at": expires_at,
        "result_task_id": doc.get("result_task_id"),
        "last_error": doc.get("last_error"),
    }


async def get_draft(draft_id: str, owner_id: str) -> dict[str, Any]:
    doc = await get_draft_storage().get(draft_id, owner_id)
    if doc is None:
        raise DraftNotFound()
    return doc


async def _raise_not_claimable(draft_id: str, owner_id: str) -> None:
    """A pending -> X transition found nothing: work out why."""
    doc = await get_draft_storage().get(draft_id, owner_id)
    if doc is None:
        raise DraftNotFound()
    status = doc.get("status", STATUS_PENDING)
    if status == STATUS_PENDING and ensure_utc(doc["expires_at"]) <= utc_now():
        raise DraftExpired()
    raise DraftStateConflict(status)


async def _apply(doc: dict[str, Any], actor: TokenPayload) -> ScheduledTask:
    # Imported lazily: ``service`` imports this module for index creation.
    from src.infra.scheduler.service import get_scheduled_task_service
    from src.infra.scheduler.storage import get_scheduled_task_storage

    service = get_scheduled_task_service()
    payload = doc.get("payload") or {}
    if doc["action"] == "create":
        return await service.create_task(
            ScheduledTaskCreate(**payload), actor.sub, actor=actor, created_via="chat"
        )

    task_id = doc.get("task_id")
    task = await get_scheduled_task_storage().get_task(task_id, actor.sub) if task_id else None
    if task is None:
        raise DraftTaskMissing()
    if task_signature(task) != doc.get("base_signature"):
        raise DraftConflict()
    if set(payload) == {"enabled"}:
        updated = await service.set_enabled(task_id, actor.sub, bool(payload["enabled"]))
    else:
        updated = await service.update_task(
            task_id, actor.sub, ScheduledTaskUpdate(**payload), actor=actor
        )
    if updated is None:
        raise DraftTaskMissing()
    return updated


async def _release(draft_id: str, owner_id: str, error: Optional[str]) -> None:
    """confirming -> pending so the user can retry; never masks the original error."""
    try:
        await get_draft_storage().transition(
            draft_id, owner_id, STATUS_CONFIRMING, STATUS_PENDING, extra_set={"last_error": error}
        )
    except Exception:  # noqa: BLE001
        logger.exception("[ScheduledTaskDraft] failed to release draft %s", draft_id)


async def confirm_draft(draft_id: str, actor: TokenPayload) -> tuple[dict[str, Any], ScheduledTask]:
    """Apply a pending draft exactly once; see module docstring for the failure semantics."""
    from src.infra.scheduler.persona import ScheduledTaskPersonaError
    from src.infra.scheduler.schedule_spec import ScheduleValidationError
    from src.infra.scheduler.service import (
        ScheduledTaskAdminPausedError,
        ScheduledTaskDisabledError,
        ScheduledTaskQuotaError,
    )

    storage = get_draft_storage()
    claimed = await storage.transition(
        draft_id,
        actor.sub,
        STATUS_PENDING,
        STATUS_CONFIRMING,
        extra_set={"last_error": None},
        require_unexpired=True,
    )
    if claimed is None:
        await _raise_not_claimable(draft_id, actor.sub)
        raise DraftNotFound()  # pragma: no cover - _raise_not_claimable always raises

    try:
        task = await _apply(claimed, actor)
    except (
        ScheduleValidationError,
        ScheduledTaskQuotaError,
        ScheduledTaskPersonaError,
        ScheduledTaskDisabledError,
        ScheduledTaskAdminPausedError,
        DraftConflict,
        DraftTaskMissing,
    ) as exc:
        # User-facing (Chinese) errors: stay pending so the user can fix it and retry.
        await _release(draft_id, actor.sub, str(exc))
        raise
    except Exception:
        await _release(draft_id, actor.sub, GENERIC_CONFIRM_ERROR)
        raise

    confirmed = await storage.transition(
        draft_id,
        actor.sub,
        STATUS_CONFIRMING,
        STATUS_CONFIRMED,
        extra_set={"result_task_id": task.id, "last_error": None},
    )
    return (confirmed or {**claimed, "status": STATUS_CONFIRMED, "result_task_id": task.id}), task


async def cancel_draft(draft_id: str, owner_id: str) -> dict[str, Any]:
    cancelled = await get_draft_storage().transition(
        draft_id, owner_id, STATUS_PENDING, STATUS_CANCELLED
    )
    if cancelled is None:
        doc = await get_draft_storage().get(draft_id, owner_id)
        if doc is None:
            raise DraftNotFound()
        raise DraftStateConflict(doc.get("status", STATUS_PENDING))
    return cancelled
