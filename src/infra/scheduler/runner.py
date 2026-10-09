"""Scheduled task execution engine.

Flow (adapted from LambChat ``ScheduledTaskRunner``):
slot claim -> execution lock -> run record -> submit agent via
BackgroundTaskManager -> detached monitor (poll / timeout / retry) ->
result preview -> stats + auto-pause -> release lock -> notify owner.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Coroutine
from datetime import datetime, timedelta
from typing import Any, Optional

from src.infra.logging import get_logger
from src.infra.scheduler.locks import acquire_task_lock, claim_schedule_slot, release_task_lock
from src.infra.scheduler.schedule_spec import describe
from src.infra.scheduler.storage import get_scheduled_task_storage
from src.infra.utils.datetime import ensure_utc, utc_now
from src.kernel.config import settings
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    RunTrigger,
    ScheduledTask,
    ScheduledTaskStatus,
    ScheduleKind,
    TaskRunRecord,
)

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 2.0
AUTO_PAUSE_AFTER_FAILURES = 3
RESULT_PREVIEW_CHARS = 300
WS_EVENT_TYPE = "scheduled_task:run_finished"

_monitors: set[asyncio.Task[None]] = set()

OWNER_UNAVAILABLE_MESSAGE = "任务创建人账号已停用或已没有定时任务权限，任务已暂停"
NO_MODEL_MESSAGE = "当前账号没有可用的模型，请联系管理员"
REQUIRED_OWNER_PERMISSIONS = ("scheduled_task:use", "chat:write")


class NonRetryableRunError(Exception):
    """A failure that retrying cannot fix: stop retrying and pause the task."""

    def __init__(self, message: str, pause_reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.pause_reason = pause_reason or message


def _spawn(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    async def _safe() -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001
            logger.exception("[ScheduledRunner] detached monitor crashed (lock expires via TTL)")

    task = asyncio.create_task(_safe())
    _monitors.add(task)
    task.add_done_callback(_monitors.discard)
    return task


async def drain_monitors(timeout: float = 10.0) -> None:
    tasks = list(_monitors)
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def build_session_id(task_id: str, run_id: str, attempt: int = 0) -> str:
    base = f"sch_{task_id[:12]}_{run_id[:8]}"
    return base if attempt == 0 else f"{base}_r{attempt}"


def build_slot_id(task: ScheduledTask, fired_at: datetime) -> tuple[str, int]:
    """Deterministic slot id shared by every replica for the same fire time."""
    fired = ensure_utc(fired_at)
    if task.schedule.kind == ScheduleKind.ONCE and task.schedule.run_at is not None:
        return f"once:{int(fired.timestamp()) // 60}", 86400
    if task.schedule.kind == ScheduleKind.INTERVAL:
        hours = int(task.schedule.every_hours or 1)
        return f"interval:{int(fired.timestamp()) // 60}", max(hours * 7200, 3600)
    return f"cron:{int(fired.replace(second=0, microsecond=0).timestamp())}", 86400


async def _resolve_owner(user_id: str):
    from src.infra.role.storage import RoleStorage
    from src.infra.user.storage import UserStorage
    from src.kernel.schemas.user import TokenPayload

    user = await UserStorage().get_by_id(user_id)
    if not user or not getattr(user, "is_active", True):
        return None
    roles = await RoleStorage().get_by_names(list(user.roles or []))
    permissions: set[str] = set()
    for role in roles:
        for perm in role.permissions or []:
            permissions.add(perm if isinstance(perm, str) else perm.value)
    return TokenPayload(
        sub=user.id,
        username=user.username,
        roles=[r.name for r in roles],
        permissions=sorted(permissions),
    )


def extract_result_text(events: list[dict[str, Any]], limit: int = RESULT_PREVIEW_CHARS) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("event_type") != "message:chunk":
            continue
        data = event.get("data") or {}
        content = data.get("content")
        if isinstance(content, str):
            chunks.append(content)
    text = "".join(chunks).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


class ScheduledTaskRunner:
    async def run(
        self,
        task_id: str,
        trigger: RunTrigger = RunTrigger.SCHEDULE,
        fired_at: Optional[datetime] = None,
    ) -> dict[str, Any]:
        if not settings_enabled():
            return {"skipped": True, "reason": "feature_disabled"}
        storage = get_scheduled_task_storage()
        task = await storage.get_task(task_id)
        if task is None:
            return {"skipped": True, "reason": "task_not_found"}
        if trigger == RunTrigger.SCHEDULE and (
            not task.enabled or task.status != ScheduledTaskStatus.ACTIVE
        ):
            return {"skipped": True, "reason": "disabled"}

        now = utc_now()
        if trigger == RunTrigger.SCHEDULE:
            slot_id, slot_ttl = build_slot_id(task, fired_at or now)
            if not await claim_schedule_slot(task.id, slot_id, ttl=slot_ttl):
                return {"skipped": True, "reason": "slot_claimed_by_peer"}

        run_id = str(uuid.uuid4())
        attempts = max(1, int(task.max_retries or 0) + 1)
        token = await acquire_task_lock(task.id, run_id, ttl=task.timeout_seconds * attempts + 120)
        if token is None:
            return {"skipped": True, "reason": "already_running"}

        record = TaskRunRecord.model_validate(
            {
                "_id": run_id,
                "task_id": task.id,
                "owner_id": task.owner_id,
                "task_name": task.name,
                "trigger": trigger,
                "status": RunStatus.PENDING,
                "session_id": build_session_id(task.id, run_id),
                "started_at": now,
            }
        )
        await storage.create_run(record)
        _spawn(self._monitor(task, run_id, token, attempts, trigger, now))
        return {"run_id": run_id, "status": "submitted", "session_id": record.session_id}

    async def _monitor(
        self,
        task: ScheduledTask,
        run_id: str,
        token: str,
        attempts: int,
        trigger: RunTrigger,
        started_at: datetime,
    ) -> None:
        storage = get_scheduled_task_storage()
        status = RunStatus.FAILED
        error: Optional[str] = None
        session_id = build_session_id(task.id, run_id)
        agent_run_id = run_id
        preview = ""
        non_retryable_reason: Optional[str] = None
        try:
            for attempt in range(attempts):
                agent_run_id = attempt_run_id(run_id, attempt)
                session_id = build_session_id(task.id, run_id, attempt)
                await storage.update_run(
                    run_id,
                    {"status": RunStatus.RUNNING, "attempt": attempt, "session_id": session_id},
                )
                try:
                    status, error = await self._execute(
                        task, run_id, session_id, attempt, started_at
                    )
                except NonRetryableRunError as exc:
                    logger.warning(
                        "[ScheduledRunner] task=%s non-retryable failure: %s", task.id, exc.message
                    )
                    status, error = RunStatus.FAILED, exc.message
                    non_retryable_reason = exc.pause_reason
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[ScheduledRunner] task=%s attempt=%d error: %s", task.id, attempt, exc
                    )
                    status, error = RunStatus.FAILED, _friendly_error(exc)
                if status != RunStatus.FAILED:
                    break
            if status == RunStatus.SUCCESS:
                preview = await self._load_preview(session_id, agent_run_id)
        finally:
            finished = utc_now()
            await storage.update_run(
                run_id,
                {
                    "status": status,
                    "error_message": error,
                    "result_preview": preview or None,
                    "finished_at": finished,
                    "duration_ms": int((finished - started_at).total_seconds() * 1000),
                },
            )
            updated = await storage.record_task_result(
                task.id, run_id=run_id, status=status, session_id=session_id, finished_at=finished
            )
            await release_task_lock(task.id, token)
            paused_reason = await self._post_run_transitions(
                task, updated, trigger, non_retryable_reason
            )
            await self._notify(task, run_id, status, session_id, preview, error, paused_reason)

    async def _execute(
        self,
        task: ScheduledTask,
        run_id: str,
        session_id: str,
        attempt: int,
        started_at: datetime,
    ) -> tuple[RunStatus, Optional[str]]:
        from src.api.routes.chat import resolve_persona_request, validate_agent_model_access
        from src.infra.chat.user_message_timestamp import format_user_message_with_timestamp
        from src.infra.session.manager import SessionManager
        from src.infra.task.concurrency import get_registered_executor
        from src.infra.task.manager import get_task_manager
        from src.kernel.exceptions import AuthorizationError
        from src.kernel.schemas.agent import AgentRequest

        owner = await _resolve_owner(task.owner_id)
        if owner is None or any(
            perm not in set(owner.permissions or []) for perm in REQUIRED_OWNER_PERMISSIONS
        ):
            raise NonRetryableRunError(OWNER_UNAVAILABLE_MESSAGE)

        agent_options: dict[str, Any] = {}
        if task.model_id:
            agent_options["model_id"] = task.model_id
        try:
            await validate_agent_model_access(agent_options, owner)
        except AuthorizationError as exc:
            message = _friendly_error(exc) if task.model_id else NO_MODEL_MESSAGE
            raise NonRetryableRunError(message) from exc

        request = AgentRequest(
            message=task.prompt, persona_preset_id=task.persona_preset_id or None
        )
        agent_options_final: dict[str, Any] = agent_options
        if task.persona_preset_id:
            request.agent_options = agent_options
            await resolve_persona_request(request, owner)
            agent_options_final = request.agent_options or agent_options

        agent_id = task.agent_id or "fast"
        if task.persona_preset_id and request.persona_snapshot is not None:
            from src.agents.core.persona import resolve_persona_agent_id

            agent_id = resolve_persona_agent_id(
                agent_id, getattr(request.persona_snapshot, "preferred_agent_id", None)
            )

        message = format_user_message_with_timestamp(task.prompt, task.timezone, now=started_at)
        agent_run_id = attempt_run_id(run_id, attempt)
        metadata: dict[str, Any] = {
            "source": "scheduled_task",
            "scheduled_task_id": task.id,
            "scheduled_task_run_id": run_id,
            "scheduled_task_name": task.name,
            "hidden_from_conversation_list": True,
            "agent_id": agent_id,
            "executor_key": "agent_stream",
            "agent_options": agent_options_final,
        }
        if task.persona_preset_id:
            metadata["persona_preset_id"] = task.persona_preset_id
            if request.persona_snapshot is not None:
                metadata["persona_preset_name"] = request.persona_snapshot.name
                metadata["persona_snapshot"] = request.persona_snapshot.model_dump()

        common: dict[str, Any] = dict(
            session_id=session_id,
            agent_id=agent_id,
            message=message,
            user_id=task.owner_id,
            agent_options=agent_options_final,
            run_id=agent_run_id,
            enabled_skills=request.enabled_skills,
            persona_system_prompt=request.persona_system_prompt,
            session_name=f"[定时] {task.name}",
            display_message=task.prompt,
            write_user_message_immediately=True,
            persona_preset_id=task.persona_preset_id or None,
        )
        manager = get_task_manager()
        if settings.TASK_BACKEND == "arq":
            await manager.submit_arq(executor_key="agent_stream", **common)
        else:
            executor = get_registered_executor("agent_stream")
            if executor is None:
                return RunStatus.FAILED, "智能体执行器未就绪，请稍后重试"
            await manager.submit(executor=executor, **common)
        await SessionManager().update_session_metadata(session_id, metadata)
        return await self._wait(manager, session_id, agent_run_id, task)

    async def _wait(self, manager: Any, session_id: str, run_id: str, task: ScheduledTask):
        from src.infra.task.status import TaskStatus

        deadline = time.monotonic() + task.timeout_seconds
        while time.monotonic() < deadline:
            state = await manager.get_run_status(session_id, run_id)
            if state == TaskStatus.COMPLETED:
                return RunStatus.SUCCESS, None
            if state in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.EXPIRED):
                detail = None
                try:
                    detail = await manager.get_run_error(run_id)
                except Exception:  # noqa: BLE001
                    detail = None
                return RunStatus.FAILED, detail or "智能体执行失败"
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            await manager.cancel_run(run_id, user_id=task.owner_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ScheduledRunner] cancel timed-out run %s failed: %s", run_id, exc)
        return RunStatus.TIMEOUT, f"执行超过 {task.timeout_seconds // 60} 分钟未完成，已自动停止"

    async def _load_preview(self, session_id: str, run_id: str) -> str:
        try:
            from src.infra.session.trace_storage import get_trace_storage

            events = await get_trace_storage().get_session_events(
                session_id, event_types=["message:chunk"], run_id=run_id
            )
            return extract_result_text(events)
        except Exception as exc:  # noqa: BLE001 - preview is best-effort
            logger.debug("[ScheduledRunner] preview failed: %s", exc)
            return ""

    async def _post_run_transitions(
        self,
        task: ScheduledTask,
        updated: Optional[ScheduledTask],
        trigger: RunTrigger,
        non_retryable_reason: Optional[str] = None,
    ) -> Optional[str]:
        from src.infra.scheduler.service import get_scheduled_task_service

        if updated is None:
            return None
        service = get_scheduled_task_service()
        if non_retryable_reason:
            # Takes precedence over one-shot completion and the failure counter.
            await service.auto_pause(task.id, non_retryable_reason, paused_by="system")
            return non_retryable_reason
        if task.schedule.kind == ScheduleKind.ONCE and trigger == RunTrigger.SCHEDULE:
            await service.mark_completed(task.id)
            return None
        if (
            updated.consecutive_failures >= AUTO_PAUSE_AFTER_FAILURES
            and updated.status == ScheduledTaskStatus.ACTIVE
        ):
            reason = f"连续 {updated.consecutive_failures} 次执行失败，已自动暂停，请检查任务内容后重新启用"
            await service.auto_pause(task.id, reason)
            return reason
        return None

    async def _notify(
        self,
        task: ScheduledTask,
        run_id: str,
        status: RunStatus,
        session_id: str,
        preview: str,
        error: Optional[str],
        paused_reason: Optional[str],
    ) -> None:
        try:
            from src.infra.websocket import get_connection_manager

            await get_connection_manager().send_to_user_with_broadcast(
                task.owner_id,
                {
                    "type": WS_EVENT_TYPE,
                    "data": {
                        "task_id": task.id,
                        "task_name": task.name,
                        "run_id": run_id,
                        "status": status.value,
                        "session_id": session_id,
                        "schedule_text": describe(task.schedule, task.timezone),
                        "result_preview": preview,
                        "error_message": error,
                        "paused_reason": paused_reason,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001 - notification must never fail a run
            logger.warning("[ScheduledRunner] notify failed task=%s: %s", task.id, exc)


def attempt_run_id(run_id: str, attempt: int) -> str:
    """Each retry gets its own agent run id; the first reuses the record id."""
    return run_id if attempt == 0 else f"{run_id}-r{attempt}"


def _friendly_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    mapping = {
        "model_disabled": "所选模型已被停用，请编辑任务更换模型",
        "model_not_allowed": "你没有使用所选模型的权限，请编辑任务更换模型",
    }
    for key, friendly in mapping.items():
        if key in text:
            return friendly
    return text[:500]


def settings_enabled() -> bool:
    return bool(getattr(settings, "SCHEDULED_TASK_ENABLED", True))


def stale_run_cutoff(now: Optional[datetime] = None) -> datetime:
    return (now or utc_now()) - timedelta(hours=3)


_runner: Optional[ScheduledTaskRunner] = None


def get_scheduled_task_runner() -> ScheduledTaskRunner:
    global _runner
    if _runner is None:
        _runner = ScheduledTaskRunner()
    return _runner
