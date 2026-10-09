"""In-memory fakes for scheduled task tests (importable from other test packages)."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from src.infra.scheduler import locks, runner, service, storage
from src.infra.scheduler.runtime import RuntimeScheduler
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    ScheduledTask,
    ScheduledTaskStatus,
    TaskRunRecord,
)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        if script == locks._RELEASE_LOCK_LUA:
            key, token = args
            if self.data.get(key) == token:
                del self.data[key]
                return 1
            return 0
        if script == locks._ACQUIRE_RUN_SLOT_LUA:
            key, now, limit, expire_at, member = args
            zset = self.zsets.setdefault(key, {})
            for stale in [m for m, score in zset.items() if score <= float(now)]:
                del zset[stale]
            if member in zset or len(zset) < int(limit):
                zset[member] = float(expire_at)
                return 1
            return 0
        raise AssertionError(f"unexpected script: {script!r}")

    async def zrem(self, key: str, *members: str) -> int:
        zset = self.zsets.get(key, {})
        return sum(1 for m in members if zset.pop(m, None) is not None)


class FakeStorage:
    def __init__(self) -> None:
        self.tasks: dict[str, ScheduledTask] = {}
        self.runs: dict[str, dict[str, Any]] = {}

    async def ensure_indexes(self) -> None:
        return None

    async def create_task(self, task: ScheduledTask) -> None:
        self.tasks[task.id] = task

    async def get_task(
        self, task_id: str, owner_id: Optional[str] = None
    ) -> Optional[ScheduledTask]:
        task = self.tasks.get(task_id)
        if task is None or (owner_id is not None and task.owner_id != owner_id):
            return None
        return task

    async def update_task(self, task_id: str, updates: dict[str, Any]) -> Optional[ScheduledTask]:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        data = task.model_dump(by_alias=True)
        data.update(updates)
        data["updated_at"] = utc_now()
        self.tasks[task_id] = ScheduledTask.model_validate(data)
        return self.tasks[task_id]

    async def delete_task(self, task_id: str, owner_id: str) -> bool:
        if await self.get_task(task_id, owner_id) is None:
            return False
        del self.tasks[task_id]
        return True

    async def count_tasks(self, owner_id: str) -> int:
        return sum(1 for t in self.tasks.values() if t.owner_id == owner_id)

    async def list_tasks(self, owner_id: str) -> list[ScheduledTask]:
        return [t for t in self.tasks.values() if t.owner_id == owner_id]

    async def list_active_tasks(self) -> list[ScheduledTask]:
        return [
            t for t in self.tasks.values() if t.enabled and t.status == ScheduledTaskStatus.ACTIVE
        ]

    async def get_active_marker(self) -> str:
        active = await self.list_active_tasks()
        stamps = sorted(str(t.updated_at) for t in self.tasks.values())
        return f"{len(active)}:{stamps[-1] if stamps else None}"

    async def record_task_result(self, task_id, *, run_id, status, session_id, finished_at):
        task = self.tasks[task_id]
        if status == RunStatus.SUCCESS:
            failures = 0
        elif status == RunStatus.SKIPPED:
            failures = task.consecutive_failures
        else:
            failures = task.consecutive_failures + 1
        return await self.update_task(
            task_id,
            {
                "last_run_at": finished_at,
                "last_run_status": status,
                "last_run_id": run_id,
                "last_session_id": session_id,
                "total_runs": task.total_runs + 1,
                "consecutive_failures": failures,
            },
        )

    async def create_run(self, record: TaskRunRecord) -> None:
        self.runs[record.id] = record.model_dump(by_alias=True)

    async def update_run(self, run_id: str, updates: dict[str, Any]) -> None:
        self.runs[run_id].update(updates)

    async def list_runs(self, task_id, owner_id, skip=0, limit=20):
        items = [
            TaskRunRecord.model_validate(r)
            for r in self.runs.values()
            if r["task_id"] == task_id and r["owner_id"] == owner_id
        ]
        return items, len(items)

    async def mark_run_interrupted(self, run_id: str) -> bool:
        record = self.runs.get(run_id)
        if record is None:
            return False
        status = getattr(record.get("status"), "value", record.get("status"))
        if status not in (RunStatus.PENDING.value, RunStatus.RUNNING.value):
            return False
        record.update(
            {
                "status": RunStatus.FAILED,
                "error_message": "服务重启，本次执行被中断",
                "finished_at": utc_now(),
            }
        )
        return True

    async def mark_stale_runs_failed(self, older_than) -> int:
        return 0


def install_fakes(monkeypatch: pytest.MonkeyPatch):
    """Patch storage / redis / scheduler singletons and return the fake handles."""
    fake_storage = FakeStorage()
    fake_redis = FakeRedis()
    scheduler = RuntimeScheduler()
    for mod in (service, runner, storage):
        monkeypatch.setattr(mod, "get_scheduled_task_storage", lambda: fake_storage, raising=False)
    monkeypatch.setattr(locks, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(service, "get_runtime_scheduler", lambda: scheduler)
    svc = service.ScheduledTaskService()
    monkeypatch.setattr(service, "_service", svc)
    return type(
        "Fakes",
        (),
        {"storage": fake_storage, "redis": fake_redis, "scheduler": scheduler, "service": svc},
    )



# ── execution fakes (persona / task manager / session metadata) ──────────────


class FakePersonaStorage:
    """Persona documents keyed by id; mirrors the fields ``PersonaPresetManager`` reads."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.preferences: dict[tuple[str, str], dict[str, Any]] = {}

    def add(
        self,
        preset_id: str,
        *,
        scope: str = "global",
        visibility: str = "public",
        status: str = "published",
        owner_user_id: Optional[str] = None,
        name: str = "分析师",
        avatar: Optional[str] = "📊",
        system_prompt: str = "你是资深分析师",
        preferred_agent_id: str = "fast",
        skill_names: Optional[list[str]] = None,
        dify_kb_dataset_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        now = utc_now()
        doc = {
            "id": preset_id,
            "scope": scope,
            "visibility": visibility,
            "status": status,
            "owner_user_id": owner_user_id,
            "name": name,
            "description": "",
            "avatar": avatar,
            "tags": [],
            "system_prompt": system_prompt,
            "starter_prompts": [],
            "skill_names": list(skill_names or []),
            "dify_kb_dataset_ids": list(dify_kb_dataset_ids or []),
            "preferred_agent_id": preferred_agent_id,
            "version": 1,
            "usage_count": 0,
            "created_by": owner_user_id or "admin-1",
            "updated_by": owner_user_id or "admin-1",
            "created_at": now,
            "updated_at": now,
        }
        self.docs[preset_id] = doc
        return doc

    def remove(self, preset_id: str) -> None:
        self.docs.pop(preset_id, None)

    async def get_by_id(self, preset_id: str) -> Optional[dict[str, Any]]:
        doc = self.docs.get(preset_id)
        return dict(doc) if doc else None

    async def increment_usage(self, preset_id: str) -> None:
        self.docs[preset_id]["usage_count"] = self.docs[preset_id].get("usage_count", 0) + 1

    async def touch_user_preference(self, *, user_id: str, preset_id: str) -> dict[str, Any]:
        pref = self.preferences.setdefault((user_id, preset_id), {})
        pref["last_used_at"] = utc_now()
        return dict(pref)


class FakeMarketplaceStorage:
    """Skill lookups succeed with a one-line description so skill hints resolve."""

    async def get_active_marketplace_skills_by_names(self, names: list[str]) -> dict[str, Any]:
        return {name: object() for name in names}

    async def get_active_skill_md_by_names(self, names: list[str]) -> dict[str, str]:
        return {name: f"---\nname: {name}\ndescription: 技能 {name}\n---" for name in names}


class FakeTaskManager:
    """Records submit / submit_arq kwargs; every run completes immediately."""

    def __init__(self) -> None:
        self.submits: list[dict[str, Any]] = []

    async def submit(self, **kwargs: Any) -> None:
        self.submits.append({"backend": "local", **kwargs})

    async def submit_arq(self, **kwargs: Any) -> None:
        self.submits.append({"backend": "arq", **kwargs})

    async def get_run_status(self, session_id: str, run_id: str):
        from src.infra.task.status import TaskStatus

        return TaskStatus.COMPLETED

    async def get_run_error(self, run_id: str) -> Optional[str]:
        return None

    async def cancel_run(self, run_id: str, user_id: Optional[str] = None) -> None:
        return None


class FakeSessionManager:
    """Records ``update_session_metadata`` calls."""

    def __init__(self) -> None:
        self.metadata_updates: list[tuple[str, dict[str, Any]]] = []

    async def update_session_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        self.metadata_updates.append((session_id, metadata))


def install_execution_fakes(monkeypatch: pytest.MonkeyPatch):
    """Patch everything ``ScheduledTaskRunner._execute`` touches outside storage.

    Persona lookups use ``FakePersonaStorage`` through the real ``PersonaPresetManager`` so
    ``resolve_persona_request`` (snapshot, skill hints, usage counting) runs for real.
    """
    import src.api.routes.chat as chat_routes
    import src.infra.session.manager as session_manager_mod
    import src.infra.task.concurrency as concurrency_mod
    import src.infra.task.manager as task_manager_mod
    from src.infra.persona_preset.manager import PersonaPresetManager
    from src.infra.scheduler import persona as persona_mod
    from src.infra.scheduler.runner import ScheduledTaskRunner
    from src.kernel.config import settings
    from src.kernel.schemas.user import TokenPayload

    persona_storage = FakePersonaStorage()
    task_manager = FakeTaskManager()
    session_manager = FakeSessionManager()

    def _manager() -> PersonaPresetManager:
        return PersonaPresetManager(persona_storage, FakeMarketplaceStorage())

    monkeypatch.setattr(chat_routes, "PersonaPresetManager", _manager)
    monkeypatch.setattr(persona_mod, "_persona_manager", _manager)

    state: dict[str, Any] = {
        "owner": TokenPayload(
            sub="u1",
            username="alice",
            roles=["user"],
            permissions=["scheduled_task:use", "chat:write"],
        )
    }

    async def fake_resolve_owner(user_id: str):
        return state["owner"]

    async def fake_validate_model(agent_options, user):
        if agent_options is not None:
            agent_options.setdefault("model_id", "model-default")
            agent_options["_resolved_model_profile"] = {"fake": True}

    async def fake_preview(self, session_id, run_id):
        return ""

    notified: list[tuple] = []

    async def fake_notify(self, task, run_id, status, session_id, preview, error, paused_reason):
        notified.append((status, error, paused_reason))

    monkeypatch.setattr(runner, "_resolve_owner", fake_resolve_owner)
    monkeypatch.setattr(chat_routes, "validate_agent_model_access", fake_validate_model)
    monkeypatch.setattr(task_manager_mod, "get_task_manager", lambda: task_manager)
    monkeypatch.setattr(concurrency_mod, "get_registered_executor", lambda key: object())
    monkeypatch.setattr(session_manager_mod, "SessionManager", lambda: session_manager)
    monkeypatch.setattr(ScheduledTaskRunner, "_load_preview", fake_preview)
    monkeypatch.setattr(ScheduledTaskRunner, "_notify", fake_notify)
    monkeypatch.setattr(settings, "TASK_BACKEND", "local")
    monkeypatch.setattr(settings, "SANDBOX_PLATFORM", "daytona")
    monkeypatch.setattr(settings, "DIFY_KB_DEFAULT_DATASET_IDS", [])

    class Handles:
        pass

    handles = Handles()
    handles.personas = persona_storage
    handles.task_manager = task_manager
    handles.sessions = session_manager
    handles.notified = notified
    handles.settings = settings
    handles.state = state
    return handles
