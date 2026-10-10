"""Chat tools for scheduled tasks: list them, and *draft* a create / update (D8).

``draft_scheduled_task`` never creates or modifies a task. It validates the request, stores a
draft and returns it; the chat UI renders a confirmation card and only the user's click on
"confirm" (``POST /api/scheduled-tasks/drafts/{id}/confirm``) applies it.

Contract notes:

* Every code path catches its own exceptions and returns a JSON string with a Chinese
  ``error``; the tools must never raise, otherwise ``MCPToolWithRetry`` would retry them.
* All results carry ``now`` (Beijing time) so the model does not guess the date.
* Scheduled (unattended) runs are refused here too, as a second line of defence behind
  ``ScheduledRunMiddleware``.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Any, Optional

from langchain_core.tools import BaseTool, InjectedToolArg
from pydantic import ValidationError

from src.infra.logging import get_logger
from src.infra.logging.context import TraceContext
from src.infra.tool.backend_utils import get_user_id_from_runtime
from src.kernel.schemas.scheduled_task import (
    ScheduledTask,
    ScheduledTaskCreate,
    ScheduleSpec,
)
from src.kernel.schemas.user import TokenPayload

if TYPE_CHECKING:
    from langchain.tools import ToolRuntime
else:
    try:
        from langchain.tools import ToolRuntime  # type: ignore[assignment]
    except ImportError:  # pragma: no cover
        _mod = type(sys)("langchain.tools")  # type: ignore[assignment]
        _mod.ToolRuntime = Any  # type: ignore[assignment]
        sys.modules.setdefault("langchain.tools", _mod)
        from langchain.tools import ToolRuntime  # type: ignore[assignment]

from langchain.tools import tool  # noqa: E402

logger = get_logger(__name__)

LIST_TOOL_NAME = "list_scheduled_tasks"
DRAFT_TOOL_NAME = "draft_scheduled_task"

UNATTENDED_MESSAGE = "定时任务自动执行中不能管理定时任务"
NO_USER_MESSAGE = "无法识别当前用户，请在聊天中使用"
INACTIVE_USER_MESSAGE = "当前账号不可用，无法管理定时任务"
NO_PERMISSION_MESSAGE = "你没有使用定时任务的权限，请联系管理员开通"
DISABLED_MESSAGE = "定时任务功能已被管理员关闭"
GENERIC_ERROR_MESSAGE = "操作失败，请稍后重试，或到「定时任务」页面手动操作"
DRAFT_MESSAGE = "草稿已生成，尚未生效。请提醒用户在聊天卡片上点击「确认」后才会生效。"
NEXT_RUN_COUNT = 5
_WEEKDAY_ZH = "一二三四五六日"
_BEIJING = timezone(timedelta(hours=8))
REQUIRED_PERMISSIONS = ("scheduled_task:use", "chat:write")


# ── small helpers ─────────────────────────────────────────────────────────


def _now_text() -> str:
    now = datetime.now(_BEIJING)
    return f"{now:%Y-%m-%d %H:%M}（北京时间，周{_WEEKDAY_ZH[now.weekday()]}）"


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    from src.infra.utils.datetime import ensure_utc

    return ensure_utc(value).isoformat()


def _enum(value: Any) -> Any:
    return getattr(value, "value", value)


def _dumps(data: dict[str, Any]) -> str:
    data.setdefault("now", _now_text())
    return json.dumps(data, ensure_ascii=False, default=str)


def _fail(message: str, candidates: Optional[list[Any]] = None) -> dict[str, Any]:
    result: dict[str, Any] = {"error": message}
    if candidates:
        result["candidates"] = candidates
    return result


def _session_id_from_runtime(runtime: Any) -> Optional[str]:
    try:
        ctx = runtime.config["configurable"]["context"]
        return getattr(ctx, "session_id", None) or None
    except Exception:  # noqa: BLE001
        return None


async def _session_persona_id(session_id: Optional[str]) -> Optional[str]:
    """Persona the current chat session was started with (session metadata)."""
    if not session_id:
        return None
    from src.infra.session.manager import SessionManager

    session = await SessionManager().get_session(session_id)
    metadata = getattr(session, "metadata", None) or {}
    value = metadata.get("persona_preset_id") if isinstance(metadata, dict) else None
    return value or None


async def _search_personas(user: TokenPayload, query: str) -> list[Any]:
    """Visible personas whose name / description matches ``query``."""
    from src.infra.persona_preset.manager import PersonaPresetManager
    from src.infra.scheduler.persona import _is_persona_admin

    return await PersonaPresetManager().list_presets(
        user_id=user.sub, is_admin=_is_persona_admin(user), q=query, limit=10
    )


async def _authorize(runtime: Any) -> tuple[Optional[TokenPayload], Optional[dict[str, Any]]]:
    """Common gate: not a scheduled run, known active user with the required permissions."""
    from src.infra.scheduler import runner

    if TraceContext.get_request_context().unattended:
        return None, _fail(UNATTENDED_MESSAGE)
    user_id = get_user_id_from_runtime(runtime)
    if not user_id:
        return None, _fail(NO_USER_MESSAGE)
    user = await runner.resolve_active_user(user_id)
    if user is None:
        return None, _fail(INACTIVE_USER_MESSAGE)
    if any(perm not in set(user.permissions or []) for perm in REQUIRED_PERMISSIONS):
        return None, _fail(NO_PERMISSION_MESSAGE)
    if not runner.settings_enabled():
        return None, _fail(DISABLED_MESSAGE)
    return user, None


def _safe_preview(spec: ScheduleSpec, tz: str) -> tuple[str, list[str]]:
    """(schedule_text, next run ISO strings); raises ScheduleValidationError on invalid specs."""
    from src.infra.scheduler.service import get_scheduled_task_service

    text, runs = get_scheduled_task_service().preview(spec, tz, NEXT_RUN_COUNT)
    return text, [_iso(r) or "" for r in runs]


def _persona_info(preset_id: Optional[str], brief: Optional[dict[str, Any]]) -> Optional[dict]:
    if not preset_id:
        return None
    brief = brief or {}
    return {
        "id": preset_id,
        "name": brief.get("name") or "已不可用的专家",
        "avatar": brief.get("avatar"),
    }


def _summary(
    *,
    name: str,
    prompt: str,
    schedule_text: str,
    next_runs: list[str],
    persona: Optional[dict[str, Any]],
    enabled: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "prompt": prompt,
        "schedule_text": schedule_text,
        "next_runs": next_runs,
        "persona": persona,
        "enabled": enabled,
    }


# ── list ──────────────────────────────────────────────────────────────────


async def _list(user: TokenPayload) -> dict[str, Any]:
    from src.infra.scheduler.service import get_scheduled_task_service, max_tasks_per_user
    from src.infra.scheduler.storage import get_scheduled_task_storage

    service = get_scheduled_task_service()
    tasks = await get_scheduled_task_storage().list_tasks(user.sub)
    responses = await service.to_responses_with_persona(tasks, user)
    return {
        "entity_type": "scheduled_task_list",
        "now": _now_text(),
        "total": len(responses),
        "limit": max_tasks_per_user(),
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "schedule_text": r.schedule_text,
                "enabled": r.enabled,
                "status": _enum(r.status),
                "next_run_at": _iso(r.next_run_at),
                "last_run_status": _enum(r.last_run_status),
                "last_run_at": _iso(r.last_run_at),
                "persona_name": r.persona_name,
                "paused_by": r.paused_by,
                "pause_reason": r.pause_reason,
            }
            for r in responses
        ],
    }


@tool
async def list_scheduled_tasks(
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """查询当前用户的全部定时任务（只读）：名称、频率、是否启用、下次运行时间、最近一次运行结果。"""
    try:
        user, error = await _authorize(runtime)
        if error is not None or user is None:
            return _dumps(error or _fail(GENERIC_ERROR_MESSAGE))
        return _dumps(await _list(user))
    except Exception:  # noqa: BLE001 - never raise (MCPToolWithRetry would retry)
        logger.exception("[ScheduledTaskTool] list failed")
        return _safe_error()


def _safe_error() -> str:
    try:
        return _dumps(_fail(GENERIC_ERROR_MESSAGE))
    except Exception:  # noqa: BLE001
        return json.dumps({"error": GENERIC_ERROR_MESSAGE}, ensure_ascii=False)


# ── persona resolution ────────────────────────────────────────────────────


async def _resolve_persona_by_name(
    user: TokenPayload, name: str
) -> tuple[Optional[Any], Optional[dict[str, Any]]]:
    """Exact (case-insensitive) name match among visible personas, validated for use."""
    from src.infra.scheduler.persona import ScheduledTaskPersonaError, ensure_persona_usable

    wanted = name.strip()
    found = await _search_personas(user, wanted)
    exact = [p for p in found if (p.name or "").strip().casefold() == wanted.casefold()]
    if len(exact) == 1:
        try:
            return await ensure_persona_usable(exact[0].id, user), None
        except ScheduledTaskPersonaError as exc:
            return None, _fail(str(exc))
    if found:
        return None, _fail(
            f"「{wanted}」匹配到多个专家或没有完全同名的专家，请让用户从候选中确认专家名称",
            [p.name for p in found],
        )
    return None, _fail(f"没有找到名为「{wanted}」的专家，请确认名称，或不指定专家")


# ── draft: create ─────────────────────────────────────────────────────────


async def _draft_create(
    user: TokenPayload,
    session_id: Optional[str],
    *,
    name: Optional[str],
    prompt: Optional[str],
    schedule: Optional[ScheduleSpec],
    persona_name: Optional[str],
    clear_persona: bool,
    enabled: Optional[bool],
) -> dict[str, Any]:
    from src.infra.scheduler import drafts
    from src.infra.scheduler.persona import ScheduledTaskPersonaError, ensure_persona_usable
    from src.infra.scheduler.schedule_spec import ScheduleValidationError
    from src.infra.scheduler.service import max_tasks_per_user
    from src.infra.scheduler.storage import get_scheduled_task_storage

    missing = [
        label
        for label, value in (
            ("任务名称（name）", (name or "").strip()),
            ("执行内容（prompt）", (prompt or "").strip()),
            ("执行时间/频率（schedule）", schedule),
        )
        if not value
    ]
    if missing:
        return _fail("创建定时任务还缺少：" + "、".join(missing) + "。请先向用户询问清楚再生成草稿")

    limit = max_tasks_per_user()
    if await get_scheduled_task_storage().count_tasks(user.sub) >= limit:
        return _fail(f"每人最多创建 {limit} 个定时任务，请先删除不再需要的任务")

    note = ""
    persona = None
    if persona_name and persona_name.strip():
        persona, error = await _resolve_persona_by_name(user, persona_name)
        if error is not None:
            return error
    elif not clear_persona:
        try:
            default_id = await _session_persona_id(session_id)
        except Exception:  # noqa: BLE001 - the default persona is a convenience only
            logger.warning("[ScheduledTaskTool] session persona lookup failed", exc_info=True)
            default_id = None
        if default_id:
            try:
                persona = await ensure_persona_usable(default_id, user)
            except ScheduledTaskPersonaError:
                note = "当前会话使用的专家已不可用，草稿未绑定专家。"

    try:
        request = ScheduledTaskCreate(
            name=name or "",
            prompt=prompt or "",
            schedule=schedule,  # type: ignore[arg-type]
            persona_preset_id=persona.id if persona is not None else None,
            enabled=True if enabled is None else bool(enabled),
        )
    except ValidationError:
        return _fail(
            "参数不合法：任务名称不能为空且不超过 100 字，执行内容不能为空且不超过 8000 字"
        )

    try:
        schedule_text, next_runs = _safe_preview(request.schedule, request.timezone)
    except ScheduleValidationError as exc:
        return _fail(str(exc))

    summary = _summary(
        name=request.name,
        prompt=request.prompt,
        schedule_text=schedule_text,
        next_runs=next_runs,
        persona=(
            {"id": persona.id, "name": persona.name, "avatar": persona.avatar}
            if persona is not None
            else None
        ),
        enabled=request.enabled,
    )
    doc = await drafts.build_draft(
        owner_id=user.sub,
        session_id=session_id,
        action="create",
        task_id=None,
        payload=request.model_dump(mode="json"),
        summary=summary,
    )
    return _draft_result(doc, note)


def _draft_result(doc: dict[str, Any], note: str = "") -> dict[str, Any]:
    return {
        "entity_type": "scheduled_task_draft",
        "draft_id": doc["_id"],
        "action": doc["action"],
        "task_id": doc.get("task_id"),
        "summary": doc["summary"],
        "before": doc.get("before"),
        "changes": doc.get("changes") or [],
        "expires_at": _iso(doc["expires_at"]),
        "now": _now_text(),
        "message": f"{note}{DRAFT_MESSAGE}",
    }


# ── draft: update ─────────────────────────────────────────────────────────


async def _locate_task(
    user: TokenPayload, task_id: Optional[str], task_name: Optional[str]
) -> tuple[Optional[ScheduledTask], Optional[dict[str, Any]]]:
    from src.infra.scheduler.schedule_spec import describe
    from src.infra.scheduler.storage import get_scheduled_task_storage

    storage = get_scheduled_task_storage()
    if task_id and task_id.strip():
        task = await storage.get_task(task_id.strip(), user.sub)
        if task is None:
            return None, _fail("没有找到这个定时任务，可先查询任务列表确认")
        return task, None
    if not (task_name and task_name.strip()):
        return None, _fail("修改定时任务需要提供 task_id 或 task_name")

    wanted = task_name.strip().casefold()
    tasks = await storage.list_tasks(user.sub)
    for matches in (
        [t for t in tasks if t.name.strip().casefold() == wanted],
        [t for t in tasks if wanted in t.name.casefold()],
    ):
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, _fail(
                f"有多个任务与「{task_name.strip()}」匹配，请让用户确认是哪一个",
                [
                    {"id": t.id, "name": t.name, "schedule_text": describe(t.schedule, t.timezone)}
                    for t in matches
                ],
            )
    return None, _fail(f"没有找到名为「{task_name.strip()}」的定时任务，可先查询任务列表确认")


async def _draft_update(
    user: TokenPayload,
    session_id: Optional[str],
    *,
    task_id: Optional[str],
    task_name: Optional[str],
    name: Optional[str],
    prompt: Optional[str],
    schedule: Optional[ScheduleSpec],
    persona_name: Optional[str],
    clear_persona: bool,
    enabled: Optional[bool],
) -> dict[str, Any]:
    from src.infra.scheduler import drafts
    from src.infra.scheduler.persona import _is_persona_admin, persona_briefs
    from src.infra.scheduler.schedule_spec import ScheduleValidationError, describe
    from src.infra.scheduler.service import ScheduledTaskAdminPausedError
    from src.kernel.schemas.scheduled_task import ScheduledTaskStatus

    task, error = await _locate_task(user, task_id, task_name)
    if error is not None or task is None:
        return error or _fail(GENERIC_ERROR_MESSAGE)

    provided = (
        name is not None
        or prompt is not None
        or schedule is not None
        or bool(persona_name and persona_name.strip())
        or clear_persona
        or enabled is not None
    )
    if not provided:
        return _fail("请说明要修改哪些内容（名称、执行内容、频率、专家，或暂停/恢复）")

    payload: dict[str, Any] = {}
    changes: list[str] = []
    new_persona = None  # resolved persona when changed by name

    if name is not None and name.strip() and name.strip() != task.name:
        payload["name"] = name.strip()
        changes.append("name")
    if prompt is not None and prompt.strip() and prompt.strip() != task.prompt:
        payload["prompt"] = prompt.strip()
        changes.append("prompt")
    new_schedule = task.schedule
    if schedule is not None and schedule.model_dump(mode="json") != task.schedule.model_dump(
        mode="json"
    ):
        new_schedule = schedule
        payload["schedule"] = schedule.model_dump(mode="json")
        changes.append("schedule")
    if persona_name and persona_name.strip():
        new_persona, error = await _resolve_persona_by_name(user, persona_name)
        if error is not None:
            return error
        if new_persona.id != task.persona_preset_id:
            payload["persona_preset_id"] = new_persona.id
            changes.append("persona")
    elif clear_persona and task.persona_preset_id:
        payload["persona_preset_id"] = None
        changes.append("persona")
    if enabled is not None and bool(enabled) != task.enabled:
        payload["enabled"] = bool(enabled)
        changes.append("enabled")

    if not changes:
        return _fail("提供的内容与当前设置相同，无需修改")

    # An administrator's hold cannot be lifted by the owner, so do not even offer a draft.
    resumes = payload.get("enabled") is True or (
        "schedule" in changes and task.status == ScheduledTaskStatus.COMPLETED
    )
    if resumes and task.paused_by == "admin":
        return _fail(str(ScheduledTaskAdminPausedError(task.pause_reason)))
    # Validate a changed schedule (or resuming a task); an untouched schedule is only previewed.
    must_validate = "schedule" in changes or payload.get("enabled") is True
    try:
        schedule_text, next_runs = _safe_preview(new_schedule, task.timezone)
    except ScheduleValidationError:
        if must_validate:
            raise
        schedule_text, next_runs = describe(task.schedule, task.timezone), []

    final_enabled = task.enabled
    if "enabled" in payload:
        final_enabled = payload["enabled"]
    elif "schedule" in changes and task.status == ScheduledTaskStatus.COMPLETED:
        final_enabled = True  # update_task re-activates a rescheduled completed task
        changes.append("enabled")

    ids = [i for i in (task.persona_preset_id, payload.get("persona_preset_id")) if i]
    briefs = await persona_briefs(ids, user_id=user.sub, is_admin=_is_persona_admin(user))
    final_persona_id = (
        payload["persona_preset_id"] if "persona_preset_id" in payload else (task.persona_preset_id)
    )
    if new_persona is not None and final_persona_id == new_persona.id:
        briefs[new_persona.id] = {"name": new_persona.name, "avatar": new_persona.avatar}

    try:
        before_text, before_runs = _safe_preview(task.schedule, task.timezone)
    except ScheduleValidationError:
        before_text, before_runs = describe(task.schedule, task.timezone), []
    before = _summary(
        name=task.name,
        prompt=task.prompt,
        schedule_text=before_text,
        next_runs=before_runs,
        persona=_persona_info(task.persona_preset_id, briefs.get(task.persona_preset_id or "")),
        enabled=task.enabled,
    )
    summary = _summary(
        name=payload.get("name", task.name),
        prompt=payload.get("prompt", task.prompt),
        schedule_text=schedule_text,
        next_runs=next_runs,
        persona=_persona_info(final_persona_id, briefs.get(final_persona_id or "")),
        enabled=bool(final_enabled),
    )
    doc = await drafts.build_draft(
        owner_id=user.sub,
        session_id=session_id,
        action="update",
        task_id=task.id,
        payload=payload,
        summary=summary,
        before=before,
        changes=changes,
        base_signature=drafts.task_signature(task),
    )
    return _draft_result(doc)


async def _draft(
    runtime: Any,
    action: str,
    task_id: Optional[str],
    task_name: Optional[str],
    name: Optional[str],
    prompt: Optional[str],
    schedule: Any,
    persona_name: Optional[str],
    clear_persona: bool,
    enabled: Optional[bool],
) -> dict[str, Any]:
    from src.infra.scheduler.schedule_spec import ScheduleValidationError

    user, error = await _authorize(runtime)
    if error is not None or user is None:
        return error or _fail(GENERIC_ERROR_MESSAGE)
    normalized = (action or "").strip().lower()
    if normalized not in ("create", "update"):
        return _fail("action 只能是 create（新建）或 update（修改）")
    if schedule is not None and not isinstance(schedule, ScheduleSpec):
        try:
            schedule = ScheduleSpec.model_validate(schedule)
        except ValidationError:
            return _fail("执行频率参数不合法，请按 schedule 的字段说明重新填写")
    session_id = _session_id_from_runtime(runtime)
    try:
        if normalized == "create":
            return await _draft_create(
                user,
                session_id,
                name=name,
                prompt=prompt,
                schedule=schedule,
                persona_name=persona_name,
                clear_persona=bool(clear_persona),
                enabled=enabled,
            )
        return await _draft_update(
            user,
            session_id,
            task_id=task_id,
            task_name=task_name,
            name=name,
            prompt=prompt,
            schedule=schedule,
            persona_name=persona_name,
            clear_persona=bool(clear_persona),
            enabled=enabled,
        )
    except ScheduleValidationError as exc:
        return _fail(str(exc))


@tool
async def draft_scheduled_task(
    action: Annotated[str, "create=新建定时任务；update=修改已有定时任务（含暂停/恢复）"],
    task_id: Annotated[str | None, "update 时使用：任务 ID（优先于 task_name）"] = None,
    task_name: Annotated[str | None, "update 时使用：任务名称，task_id 未知时用它定位"] = None,
    name: Annotated[str | None, "任务名称（create 必填；update 为新名称）"] = None,
    prompt: Annotated[
        str | None, "每次运行时让助手执行的内容（create 必填；update 为新内容）"
    ] = None,
    schedule: Annotated[ScheduleSpec | None, "执行频率（create 必填；update 为新频率）"] = None,
    persona_name: Annotated[str | None, "要使用的专家名称；不填则默认沿用当前会话的专家"] = None,
    clear_persona: Annotated[bool, "true 表示不使用任何专家"] = False,
    enabled: Annotated[bool | None, "update 时 false=暂停、true=恢复；create 时默认启用"] = None,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """为用户生成定时任务草稿（新建或修改），只生成草稿，不会直接生效。
    必须由用户在聊天卡片上点击确认后才生效；不支持删除。
    用户询问时间时，以工具返回的 now 为准。"""
    try:
        return _dumps(
            await _draft(
                runtime,
                action,
                task_id,
                task_name,
                name,
                prompt,
                schedule,
                persona_name,
                clear_persona,
                enabled,
            )
        )
    except Exception:  # noqa: BLE001 - never raise (MCPToolWithRetry would retry)
        logger.exception("[ScheduledTaskTool] draft failed")
        return _safe_error()


def get_scheduled_task_tools() -> list[BaseTool]:
    return [list_scheduled_tasks, draft_scheduled_task]
