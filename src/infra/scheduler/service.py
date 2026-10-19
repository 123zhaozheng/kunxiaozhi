"""Scheduled task business logic: CRUD, quota, scheduler (un)registration, resync."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal, Optional
from uuid import uuid4

from apscheduler.triggers.base import BaseTrigger
from pydantic import ValidationError

from src.infra.logging import get_logger
from src.infra.scheduler.drafts import get_draft_storage
from src.infra.scheduler.persona import _is_persona_admin, ensure_persona_usable, persona_briefs
from src.infra.scheduler.runner import get_scheduled_task_runner, settings_enabled, stale_run_cutoff
from src.infra.scheduler.runtime import ScheduledJob, get_runtime_scheduler
from src.infra.scheduler.schedule_spec import (
    ScheduleValidationError,
    build_trigger,
    describe,
    next_fire_times,
    to_display_spec,
    to_storage_spec,
    validate_spec,
)
from src.infra.scheduler.storage import get_scheduled_task_storage
from src.infra.utils.datetime import ensure_utc, utc_now
from src.kernel.config import settings
from src.kernel.schemas.scheduled_task import (
    RunTrigger,
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduledTaskResponse,
    ScheduledTaskStatus,
    ScheduledTaskUpdate,
    ScheduleKind,
    ScheduleSpec,
)
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

JOB_PREFIX = "user_task:"
RESYNC_JOB_ID = "scheduled_task.resync"
RESYNC_INTERVAL_SECONDS = 60
ADMIN_PAUSE_PREFIX = "管理员已暂停："
ADMIN_PAUSE_REASON_MAX = 200
# APScheduler drops a fire that starts more than this late (its default is 1s).
MISFIRE_GRACE_SECONDS = 300


class ScheduledTaskQuotaError(ValueError):
    """User-facing quota error (Chinese message)."""


class ScheduledTaskDisabledError(ValueError):
    """Raised when the feature is switched off by the administrator."""

    def __init__(self, message: str = "定时任务功能已被管理员关闭") -> None:
        super().__init__(message)


class ScheduledTaskAdminPausedError(PermissionError):
    """The task was paused by an administrator; the owner cannot resume or run it."""

    def __init__(self, pause_reason: Optional[str] = None) -> None:
        reason = (pause_reason or "").strip()
        if reason.startswith(ADMIN_PAUSE_PREFIX):
            reason = reason[len(ADMIN_PAUSE_PREFIX) :].strip()
        super().__init__(f"该任务已被管理员暂停（原因：{reason}），如需恢复请联系管理员")


def ensure_not_admin_paused(task: ScheduledTask) -> None:
    """Raise when ``task`` is held by an administrator (owner may not resume / run it)."""
    if task.paused_by == "admin":
        raise ScheduledTaskAdminPausedError(task.pause_reason)


def max_tasks_per_user() -> int:
    return max(1, int(getattr(settings, "SCHEDULED_TASK_MAX_PER_USER", 20) or 20))


def min_interval_hours() -> int:
    return max(1, int(getattr(settings, "SCHEDULED_TASK_MIN_INTERVAL_HOURS", 1) or 1))


def job_id_for(task_id: str) -> str:
    return f"{JOB_PREFIX}{task_id}"


def _ensure_enabled() -> None:
    if not settings_enabled():
        raise ScheduledTaskDisabledError()


class ScheduledTaskService:
    def __init__(self) -> None:
        self._signatures: dict[str, str] = {}
        self._marker: Optional[str] = None

    # ── validation / preview ───────────────────────

    @staticmethod
    def validate(spec: ScheduleSpec, timezone_name: str) -> None:
        validate_spec(spec, timezone_name, min_interval_hours=min_interval_hours())

    @staticmethod
    def preview(
        spec: ScheduleSpec, timezone_name: str, count: int = 5
    ) -> tuple[str, list[datetime]]:
        validate_spec(spec, timezone_name, min_interval_hours=min_interval_hours())
        return describe(spec, timezone_name), next_fire_times(spec, timezone_name, count)

    # ── CRUD ───────────────────────────────────────

    async def create_task(
        self,
        request: ScheduledTaskCreate,
        owner_id: str,
        *,
        actor: Optional[TokenPayload] = None,
        created_via: Literal["web", "chat"] = "web",
    ) -> ScheduledTask:
        _ensure_enabled()
        self.validate(request.schedule, request.timezone)
        if request.persona_preset_id:
            if actor is None:
                raise ValueError("actor is required when persona_preset_id is set")
            await ensure_persona_usable(request.persona_preset_id, actor)
        storage = get_scheduled_task_storage()
        limit = max_tasks_per_user()
        if await storage.count_tasks(owner_id) >= limit:
            raise ScheduledTaskQuotaError(
                f"每人最多创建 {limit} 个定时任务，请先删除不再需要的任务"
            )
        now = utc_now()
        task = ScheduledTask.model_validate(
            {
                "_id": str(uuid4()),
                "owner_id": owner_id,
                **request.model_dump(),
                "schedule": to_storage_spec(request.schedule, request.timezone),
                "created_via": created_via,
                "status": ScheduledTaskStatus.ACTIVE,
                "created_at": now,
                "updated_at": now,
            }
        )
        await storage.create_task(task)
        self.sync_registration(task)
        logger.info(
            "[ScheduledTaskService] created %s (%s)",
            task.id,
            describe(task.schedule, task.timezone),
        )
        return task

    async def update_task(
        self,
        task_id: str,
        owner_id: str,
        request: ScheduledTaskUpdate,
        *,
        actor: Optional[TokenPayload] = None,
    ) -> Optional[ScheduledTask]:
        storage = get_scheduled_task_storage()
        task = await storage.get_task(task_id, owner_id)
        if task is None:
            return None
        updates: dict[str, Any] = request.model_dump(exclude_unset=True)
        if updates.get("enabled") is True:
            ensure_not_admin_paused(task)
        if updates != {"enabled": False}:
            _ensure_enabled()  # pausing stays allowed while the feature is off
        new_persona = updates.get("persona_preset_id")
        # Only a *changed* persona is validated: renaming a task must not fail just because
        # its current persona went away (the runner handles that at execution time).
        # An explicit null clears the persona and needs no check.
        if new_persona and new_persona != task.persona_preset_id:
            if actor is None:
                raise ValueError("actor is required when persona_preset_id changes")
            await ensure_persona_usable(new_persona, actor)
        if "schedule" in updates or "timezone" in updates:
            spec = request.schedule or task.schedule
            self.validate(spec, updates.get("timezone") or task.timezone)
            if request.schedule is not None:
                updates["schedule"] = to_storage_spec(
                    request.schedule, updates.get("timezone") or task.timezone
                ).model_dump(mode="python")
            # A rescheduled one-shot / completed task becomes active again.
            if task.status == ScheduledTaskStatus.COMPLETED:
                updates.setdefault("enabled", True)
        if updates.get("enabled") is True:
            ensure_not_admin_paused(task)
            updates["status"] = ScheduledTaskStatus.ACTIVE.value
            updates["pause_reason"] = None
            updates["paused_by"] = None
            updates["consecutive_failures"] = 0
        elif updates.get("enabled") is False:
            updates["status"] = ScheduledTaskStatus.PAUSED.value
            # An administrator's hold is never downgraded to a user pause.
            if task.paused_by != "admin":
                updates["paused_by"] = "user"
        if not updates:
            return task
        # Validate the merged document first: an invalid $set would poison the task for every
        # reader (startup load and resync list all active tasks).
        try:
            ScheduledTask.model_validate({**task.model_dump(by_alias=True), **updates})
        except ValidationError as exc:
            raise ScheduleValidationError("任务参数不合法，请检查后重试") from exc
        updated = await storage.update_task(task_id, updates)
        if updated is not None:
            self.sync_registration(updated)
        return updated

    async def set_enabled(
        self, task_id: str, owner_id: str, enabled: bool
    ) -> Optional[ScheduledTask]:
        storage = get_scheduled_task_storage()
        task = await storage.get_task(task_id, owner_id)
        if task is None:
            return None
        if enabled:
            ensure_not_admin_paused(task)
            _ensure_enabled()
        if enabled and task.schedule.kind == ScheduleKind.ONCE:
            # Resuming a past one-shot would never fire; ask the user to pick a new time.
            self.validate(task.schedule, task.timezone)
        return await self.update_task(task_id, owner_id, ScheduledTaskUpdate(enabled=enabled))

    async def delete_task(self, task_id: str, owner_id: str) -> bool:
        deleted = await get_scheduled_task_storage().delete_task(task_id, owner_id)
        if deleted:
            self._unregister(task_id)
        return deleted

    async def run_now(self, task_id: str, owner_id: str) -> Optional[dict[str, Any]]:
        task = await get_scheduled_task_storage().get_task(task_id, owner_id)
        if task is None:
            return None
        ensure_not_admin_paused(task)
        _ensure_enabled()
        return await get_scheduled_task_runner().run(task.id, trigger=RunTrigger.MANUAL)

    # ── transitions used by the runner ─────────────

    async def mark_completed(self, task_id: str) -> None:
        updated = await get_scheduled_task_storage().update_task(
            task_id, {"status": ScheduledTaskStatus.COMPLETED.value, "enabled": False}
        )
        self._unregister(task_id)
        if updated is not None:
            logger.info("[ScheduledTaskService] one-shot task %s completed", task_id)

    async def auto_pause(self, task_id: str, reason: str, paused_by: str = "system") -> None:
        await get_scheduled_task_storage().update_task(
            task_id,
            {
                "status": ScheduledTaskStatus.PAUSED.value,
                "enabled": False,
                "pause_reason": reason,
                "paused_by": paused_by,
            },
        )
        self._unregister(task_id)
        logger.warning("[ScheduledTaskService] auto-paused %s (%s): %s", task_id, paused_by, reason)

    # ── administrator interventions ────────────────

    @staticmethod
    def _audit(action: str, task: ScheduledTask, actor: str, reason: str = "") -> None:
        """Minimal operation trail: one structured line per administrator action."""
        logger.info(
            "[ScheduledTaskAdmin] action=%s task=%s owner=%s actor=%s reason=%s",
            action,
            task.id,
            task.owner_id,
            actor,
            reason,
        )

    async def admin_pause(self, task_id: str, reason: str, actor: str) -> Optional[ScheduledTask]:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("请填写暂停原因")
        if len(reason) > ADMIN_PAUSE_REASON_MAX:
            raise ValueError(f"暂停原因不能超过 {ADMIN_PAUSE_REASON_MAX} 字")
        storage = get_scheduled_task_storage()
        if await storage.get_task(task_id) is None:
            return None
        await self.auto_pause(task_id, f"{ADMIN_PAUSE_PREFIX}{reason}", paused_by="admin")
        task = await storage.get_task(task_id)
        if task is not None:
            self._audit("pause", task, actor, reason)
        return task

    async def admin_resume(self, task_id: str, actor: str) -> Optional[ScheduledTask]:
        _ensure_enabled()
        storage = get_scheduled_task_storage()
        task = await storage.get_task(task_id)
        if task is None:
            return None
        if task.schedule.kind == ScheduleKind.ONCE:
            self.validate(task.schedule, task.timezone)  # a past one-shot would never fire
        updated = await storage.update_task(
            task_id,
            {
                "enabled": True,
                "status": ScheduledTaskStatus.ACTIVE.value,
                "paused_by": None,
                "pause_reason": None,
                "consecutive_failures": 0,
            },
        )
        if updated is None:
            return None
        self.sync_registration(updated)
        self._audit("resume", updated, actor)
        return updated

    async def admin_delete(self, task_id: str, actor: str) -> bool:
        storage = get_scheduled_task_storage()
        task = await storage.get_task(task_id)
        if task is None:
            return False
        deleted = await storage.delete_task(task_id)
        if deleted:
            self._unregister(task_id)
            self._audit("delete", task, actor)
        return deleted

    # ── registration ───────────────────────────────

    def sync_registration(self, task: ScheduledTask) -> None:
        if task.enabled and task.status == ScheduledTaskStatus.ACTIVE and settings_enabled():
            self._register(task)
        else:
            self._unregister(task.id)

    def _register(self, task: ScheduledTask) -> None:
        signature = self._signature(task)
        scheduler = get_runtime_scheduler()
        job_id = job_id_for(task.id)
        if self._signatures.get(task.id) == signature and scheduler.has_job(job_id):
            return
        if task.schedule.kind == ScheduleKind.ONCE:
            run_at = task.schedule.run_at
            if run_at is not None and _is_past(run_at, task.timezone):
                return
        try:
            trigger = build_trigger(
                task.schedule, task.timezone, anchor=task.last_run_at or task.created_at
            )
        except ScheduleValidationError as exc:
            logger.warning("[ScheduledTaskService] skip invalid task %s: %s", task.id, exc)
            return
        runner = get_scheduled_task_runner()
        task_id = task.id

        async def _handler() -> Any:
            return await runner.run(
                task_id, trigger=RunTrigger.SCHEDULE, fired_at=scheduled_fire_time(trigger)
            )

        scheduler.register_job(
            ScheduledJob(
                id=job_id,
                name=f"scheduled task {task.name}",
                handler=_handler,
                trigger=trigger,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=MISFIRE_GRACE_SECONDS,
            )
        )
        self._signatures[task.id] = signature

    def _unregister(self, task_id: str) -> None:
        get_runtime_scheduler().unregister_job(job_id_for(task_id))
        self._signatures.pop(task_id, None)

    @staticmethod
    def _signature(task: ScheduledTask) -> str:
        return json.dumps(
            {"schedule": task.schedule.model_dump(mode="json"), "tz": task.timezone},
            sort_keys=True,
        )

    def next_run_at(self, task: ScheduledTask) -> Optional[datetime]:
        if not task.enabled or task.status != ScheduledTaskStatus.ACTIVE:
            return None
        live = get_runtime_scheduler().get_next_run_time(job_id_for(task.id))
        if live is not None:
            return live
        try:
            times = next_fire_times(
                task.schedule, task.timezone, 1, anchor=task.last_run_at or task.created_at
            )
        except Exception:  # noqa: BLE001
            return None
        return times[0] if times else None

    # ── startup / resync ───────────────────────────

    async def load_all(self, *, force: bool = False) -> int:
        """Register every active task; cheap no-op when nothing changed."""
        if not settings_enabled():
            for task_id in list(self._signatures):
                self._unregister(task_id)
            # Forget the marker so re-enabling re-registers everything on the next resync.
            self._marker = None
            return 0
        storage = get_scheduled_task_storage()
        marker = await storage.get_active_marker()
        if not force and marker == self._marker:
            return len(self._signatures)
        tasks = await storage.list_active_tasks()
        active_ids: set[str] = set()
        for task in tasks:
            if task.schedule.kind == ScheduleKind.ONCE and task.schedule.run_at is not None:
                if _is_past(task.schedule.run_at, task.timezone) and task.total_runs > 0:
                    await self.mark_completed(task.id)
                    continue
            active_ids.add(task.id)
            self._register(task)
        for stale in set(self._signatures) - active_ids:
            self._unregister(stale)
        self._marker = marker
        return len(active_ids)

    async def startup(self) -> None:
        storage = get_scheduled_task_storage()
        try:
            await storage.ensure_indexes()
            closed = await storage.mark_stale_runs_failed(stale_run_cutoff())
            if closed:
                logger.info("[ScheduledTaskService] closed %d stale runs", closed)
            count = await self.load_all(force=True)
            logger.info("[ScheduledTaskService] loaded %d active scheduled tasks", count)
        except Exception as exc:  # noqa: BLE001 - never block app startup
            logger.warning("[ScheduledTaskService] startup load failed: %s", exc)

        try:
            await get_draft_storage().ensure_indexes()
        except Exception as exc:  # noqa: BLE001 - drafts are optional, never block startup
            logger.warning("[ScheduledTaskService] draft index creation failed: %s", exc)

        async def _resync() -> Any:
            return await self.load_all()

        get_runtime_scheduler().register_interval_job(
            ScheduledJob(
                id=RESYNC_JOB_ID,
                name="Scheduled task resync",
                interval_seconds=RESYNC_INTERVAL_SECONDS,
                handler=_resync,
            )
        )

    # ── DTO ────────────────────────────────────────

    def to_response(self, task: ScheduledTask) -> ScheduledTaskResponse:
        data = task.model_dump()
        data.pop("owner_id", None)
        data["schedule"] = to_display_spec(task.schedule, task.timezone)
        return ScheduledTaskResponse(
            **data,
            schedule_text=describe(task.schedule, task.timezone),
            next_run_at=self.next_run_at(task),
        )

    async def to_responses_with_persona(
        self, tasks: list[ScheduledTask], viewer: TokenPayload
    ) -> list[ScheduledTaskResponse]:
        """Responses enriched with persona name / avatar / availability for ``viewer``."""
        briefs = await persona_briefs(
            [t.persona_preset_id for t in tasks if t.persona_preset_id],
            user_id=viewer.sub,
            is_admin=_is_persona_admin(viewer),
        )
        items: list[ScheduledTaskResponse] = []
        for task in tasks:
            response = self.to_response(task)
            brief = briefs.get(task.persona_preset_id) if task.persona_preset_id else None
            if brief is not None:
                response.persona_name = brief["name"]
                response.persona_avatar = brief["avatar"]
                response.persona_available = brief["available"]
            items.append(response)
        return items

    async def to_response_with_persona(
        self, task: ScheduledTask, viewer: TokenPayload
    ) -> ScheduledTaskResponse:
        return (await self.to_responses_with_persona([task], viewer))[0]


def scheduled_fire_time(trigger: BaseTrigger, now: Optional[datetime] = None) -> datetime:
    """The fire time being executed, identical on every replica even when one fires late.

    Feeds the cross-replica dedupe slot; using the wall clock would let a late replica
    (within the misfire grace) land in a different slot and run the task twice.
    """
    now = now or utc_now()
    try:
        fire = trigger.get_next_fire_time(None, now - timedelta(seconds=MISFIRE_GRACE_SECONDS))
    except Exception:  # noqa: BLE001 - fall back to the wall clock
        fire = None
    return fire if fire is not None and fire <= now else now


def _is_past(run_at: datetime, timezone_name: str) -> bool:
    from src.infra.scheduler.schedule_spec import resolve_timezone

    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=resolve_timezone(timezone_name))
    return ensure_utc(run_at) <= utc_now()


_service: Optional[ScheduledTaskService] = None


def get_scheduled_task_service() -> ScheduledTaskService:
    global _service
    if _service is None:
        _service = ScheduledTaskService()
    return _service
