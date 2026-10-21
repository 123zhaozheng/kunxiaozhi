"""P2: persona selection for scheduled tasks (validation, execution, display)."""

from __future__ import annotations

import asyncio

import pytest

from src.infra.scheduler import persona as persona_mod
from src.infra.scheduler import runner as runner_mod
from src.infra.scheduler.persona import (
    PERSONA_UNAVAILABLE_RUN_MESSAGE,
    ScheduledTaskPersonaError,
    ensure_persona_usable,
    persona_briefs,
)
from src.infra.scheduler.runner import SANDBOX_BUSY_MESSAGE, ScheduledTaskRunner
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    RunTrigger,
    ScheduledTaskCreate,
    ScheduledTaskStatus,
    ScheduledTaskUpdate,
)
from src.kernel.schemas.user import TokenPayload


def _user(sub: str = "u1", *extra: str) -> TokenPayload:
    return TokenPayload(
        sub=sub,
        username=sub,
        roles=["user"],
        permissions=["scheduled_task:use", "chat:write", *extra],
    )


def _create(**kw) -> ScheduledTaskCreate:
    base = {
        "name": "晨报",
        "prompt": "整理今天的重点",
        "schedule": {"kind": "weekdays", "time": "08:30"},
        "max_retries": 2,
    }
    base.update(kw)
    return ScheduledTaskCreate(**base)


async def _run(task_id: str) -> None:
    await ScheduledTaskRunner().run(task_id, trigger=RunTrigger.MANUAL)
    await asyncio.sleep(0)
    await runner_mod.drain_monitors(timeout=5)


# ── execution: persona wiring ──────────────────


@pytest.mark.asyncio
async def test_persona_run_uses_snapshot_skills_kb_and_agent(fakes, execution) -> None:
    execution.personas.add(
        "p1",
        name="检索专家",
        avatar="🔍",
        system_prompt="你是检索专家",
        preferred_agent_id="search",
        skill_names=["web-research"],
        dify_kb_dataset_ids=["kb-1", "kb-2"],
    )
    task = await fakes.service.create_task(
        _create(persona_preset_id="p1", model_id="m1"), "u1", actor=_user()
    )
    assert execution.personas.docs["p1"]["usage_count"] == 0  # validation is not a "use"

    await _run(task.id)

    assert len(execution.task_manager.submits) == 1
    sub = execution.task_manager.submits[0]
    assert sub["agent_id"] == "search"
    assert sub["enabled_skills"] == ["web-research"]
    assert sub["persona_system_prompt"] == "你是检索专家"
    assert sub["persona_preset_id"] == "p1"
    assert sub["agent_options"]["dify_kb_dataset_ids"] == ["kb-1", "kb-2"]
    assert sub["agent_options"]["model_id"] == "m1"
    assert sub["agent_options"]["_persona_skill_hints"][0]["name"] == "web-research"
    assert sub["display_message"] == "整理今天的重点"

    assert execution.personas.docs["p1"]["usage_count"] == 1
    (session_id, metadata), = execution.sessions.metadata_updates
    assert session_id == sub["session_id"]
    assert metadata["persona_preset_id"] == "p1"
    assert metadata["persona_preset_name"] == "检索专家"
    assert metadata["persona_snapshot"]["preset_id"] == "p1"
    assert metadata["persona_avatar"] == "🔍"
    assert metadata["enabled_skills"] == ["web-research"]
    assert metadata["agent_id"] == "search"
    assert metadata["agent_options"]["dify_kb_dataset_ids"] == ["kb-1", "kb-2"]
    assert metadata["source"] == "scheduled_task"
    assert metadata["scheduled_task_id"] == task.id
    assert metadata["scheduled_task_run_id"] == sub["run_id"]
    assert metadata["scheduled_task_name"] == "晨报"
    assert metadata["hidden_from_conversation_list"] is True
    assert metadata["current_run_id"] == sub["run_id"]
    # 定时运行标记：提交时传 unattended=True；metadata 记下本次运行的 run_id（重启后据此不自动恢复）
    assert sub["unattended"] is True
    assert metadata["unattended_run_id"] == sub["run_id"]
    assert "unattended" not in sub["agent_options"]
    assert "unattended" not in metadata["agent_options"]
    assert fakes.storage.tasks[task.id].last_run_status == RunStatus.SUCCESS


@pytest.mark.asyncio
async def test_run_is_submitted_unattended_on_both_backends(fakes, execution) -> None:
    execution.personas.add("p1", preferred_agent_id="fast", skill_names=[])
    task = await fakes.service.create_task(
        _create(persona_preset_id="p1"), "u1", actor=_user()
    )

    for backend in ("local", "arq"):
        execution.settings.TASK_BACKEND = backend
        await _run(task.id)

    assert [s["backend"] for s in execution.task_manager.submits] == ["local", "arq"]
    assert all(s["unattended"] is True for s in execution.task_manager.submits)


@pytest.mark.asyncio
async def test_persona_without_skills_leaves_enabled_skills_unrestricted(
    fakes, execution
) -> None:
    execution.personas.add("p1", preferred_agent_id="fast", skill_names=[])
    task = await fakes.service.create_task(
        _create(persona_preset_id="p1"), "u1", actor=_user()
    )
    await _run(task.id)

    sub = execution.task_manager.submits[0]
    assert sub["agent_id"] == "fast"
    assert sub["enabled_skills"] is None
    assert sub["persona_system_prompt"] == "你是资深分析师"


@pytest.mark.asyncio
async def test_no_persona_run_is_plain_fast_and_still_injects_default_kb(
    fakes, execution
) -> None:
    execution.settings.DIFY_KB_DEFAULT_DATASET_IDS = ["default-kb"]
    task = await fakes.service.create_task(_create(), "u1")
    await _run(task.id)

    sub = execution.task_manager.submits[0]
    assert sub["agent_id"] == "fast"
    assert sub["enabled_skills"] is None
    assert sub["persona_system_prompt"] is None
    assert sub["persona_preset_id"] is None
    assert sub["agent_options"]["dify_kb_dataset_ids"] == ["default-kb"]
    (_, metadata), = execution.sessions.metadata_updates
    assert "persona_preset_id" not in metadata and "persona_snapshot" not in metadata
    assert metadata["source"] == "scheduled_task"


@pytest.mark.asyncio
async def test_no_persona_no_default_kb_leaves_agent_options_clean(fakes, execution) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    await _run(task.id)
    assert "dify_kb_dataset_ids" not in execution.task_manager.submits[0]["agent_options"]


@pytest.mark.asyncio
async def test_legacy_agent_id_is_normalised_to_fast(fakes, execution) -> None:
    task = await fakes.service.create_task(_create(), "u1")
    fakes.storage.tasks[task.id] = fakes.storage.tasks[task.id].model_copy(
        update={"agent_id": "team"}
    )
    await _run(task.id)
    assert execution.task_manager.submits[0]["agent_id"] == "fast"


@pytest.mark.asyncio
async def test_arq_backend_receives_the_same_kwargs(fakes, execution) -> None:
    execution.settings.TASK_BACKEND = "arq"
    execution.personas.add("p1", skill_names=["s"], preferred_agent_id="search")
    task = await fakes.service.create_task(
        _create(persona_preset_id="p1"), "u1", actor=_user()
    )
    await _run(task.id)
    sub = execution.task_manager.submits[0]
    assert sub["backend"] == "arq" and sub["executor_key"] == "agent_stream"
    assert sub["enabled_skills"] == ["s"] and sub["agent_id"] == "search"


# ── execution: unusable persona ────────────────


def _break_persona(execution, how: str) -> None:
    doc = execution.personas.docs["p1"]
    if how == "deleted":
        execution.personas.remove("p1")
    elif how == "archived":
        doc["status"] = "archived"
    elif how == "foreign-private":
        doc.update(scope="user", visibility="private", status="draft", owner_user_id="u2")


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["deleted", "archived", "foreign-private"])
async def test_unusable_persona_pauses_without_retry(fakes, execution, how) -> None:
    execution.personas.add("p1")
    task = await fakes.service.create_task(
        _create(persona_preset_id="p1", max_retries=2), "u1", actor=_user()
    )
    _break_persona(execution, how)

    await _run(task.id)

    assert execution.task_manager.submits == []
    assert execution.sessions.metadata_updates == []
    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.PAUSED and stored.enabled is False
    assert stored.paused_by == "system"
    assert stored.pause_reason == PERSONA_UNAVAILABLE_RUN_MESSAGE
    assert stored.last_run_status == RunStatus.FAILED
    run = next(iter(fakes.storage.runs.values()))
    assert run["attempt"] == 0  # single attempt despite max_retries=2
    assert run["error_message"] == PERSONA_UNAVAILABLE_RUN_MESSAGE
    assert execution.notified == [
        (RunStatus.FAILED, PERSONA_UNAVAILABLE_RUN_MESSAGE, PERSONA_UNAVAILABLE_RUN_MESSAGE)
    ]


@pytest.mark.asyncio
async def test_archived_global_persona_is_still_usable_by_persona_admin_owner(
    fakes, execution
) -> None:
    execution.personas.add("p1", status="archived")
    execution.state["owner"] = _user("u1", "persona_preset:admin")
    task = await fakes.service.create_task(
        _create(persona_preset_id="p1"), "u1", actor=_user("u1", "persona_preset:admin")
    )
    await _run(task.id)
    assert len(execution.task_manager.submits) == 1


def test_friendly_error_maps_persona_not_found() -> None:
    err = runner_mod._friendly_error(Exception("persona_preset_not_found"))
    assert err == PERSONA_UNAVAILABLE_RUN_MESSAGE


# ── execution: sandbox admission ───────────────


def _opensandbox(monkeypatch, execution, *, supports: bool, raises: bool):
    from src.agents.core.base import AgentFactory
    from src.infra.sandbox import session_manager as sandbox_mod
    from src.kernel.exceptions import SandboxCapacityUnavailable

    calls: list[str] = []

    class _Agent:
        _supports_sandbox = supports

    class _Sandbox:
        async def admit(self, user_id: str) -> None:
            calls.append(user_id)
            if raises:
                raise SandboxCapacityUnavailable()

    execution.settings.SANDBOX_PLATFORM = "opensandbox"
    monkeypatch.setattr(AgentFactory, "get_class", lambda _agent_id: _Agent)
    monkeypatch.setattr(sandbox_mod, "get_session_sandbox_manager", lambda: _Sandbox())
    return calls


@pytest.mark.asyncio
async def test_sandbox_capacity_failure_is_retryable_and_not_paused(
    fakes, execution, monkeypatch
) -> None:
    calls = _opensandbox(monkeypatch, execution, supports=True, raises=True)
    task = await fakes.service.create_task(_create(max_retries=2), "u1")
    await _run(task.id)

    assert calls == ["u1", "u1", "u1"]  # 1 + 2 retries
    assert execution.task_manager.submits == []
    stored = fakes.storage.tasks[task.id]
    assert stored.status == ScheduledTaskStatus.ACTIVE and stored.paused_by is None
    run = next(iter(fakes.storage.runs.values()))
    assert run["status"] == RunStatus.FAILED and run["error_message"] == SANDBOX_BUSY_MESSAGE


@pytest.mark.asyncio
async def test_sandbox_admission_skipped_for_agents_without_sandbox(
    fakes, execution, monkeypatch
) -> None:
    calls = _opensandbox(monkeypatch, execution, supports=False, raises=True)
    task = await fakes.service.create_task(_create(), "u1")
    await _run(task.id)
    assert calls == [] and len(execution.task_manager.submits) == 1


# ── service: create / update validation ────────


@pytest.mark.asyncio
async def test_create_rejects_foreign_private_and_unpublished_global(fakes, execution) -> None:
    p = execution.personas
    p.add("foreign", scope="user", visibility="private", status="draft", owner_user_id="u2")
    p.add("global-draft", scope="global", visibility="public", status="draft")
    p.add("global-private", scope="global", visibility="private", status="published")
    for pid in ("foreign", "global-draft", "global-private", "missing"):
        with pytest.raises(ScheduledTaskPersonaError, match="所选专家不存在或你无权使用"):
            await fakes.service.create_task(_create(persona_preset_id=pid), "u1", actor=_user())
    assert fakes.storage.tasks == {}


@pytest.mark.asyncio
async def test_create_accepts_own_draft_and_published_global(fakes, execution) -> None:
    execution.personas.add(
        "mine", scope="user", visibility="private", status="draft", owner_user_id="u1"
    )
    execution.personas.add("plaza")
    for pid in ("mine", "plaza"):
        task = await fakes.service.create_task(_create(persona_preset_id=pid), "u1", actor=_user())
        assert task.persona_preset_id == pid
    assert all(d["usage_count"] == 0 for d in execution.personas.docs.values())


@pytest.mark.asyncio
async def test_create_with_persona_requires_actor(fakes, execution) -> None:
    execution.personas.add("plaza")
    with pytest.raises(ValueError, match="actor"):
        await fakes.service.create_task(_create(persona_preset_id="plaza"), "u1")


@pytest.mark.asyncio
async def test_update_skips_validation_when_persona_unchanged(fakes, execution) -> None:
    execution.personas.add("p1")
    svc = fakes.service
    task = await svc.create_task(_create(persona_preset_id="p1"), "u1", actor=_user())
    execution.personas.remove("p1")  # persona vanished after creation

    renamed = await svc.update_task(
        task.id, "u1", ScheduledTaskUpdate(name="新名字"), actor=_user()
    )
    assert renamed.name == "新名字"
    same = await svc.update_task(
        task.id, "u1", ScheduledTaskUpdate(persona_preset_id="p1", prompt="新提示"), actor=_user()
    )
    assert same.prompt == "新提示" and same.persona_preset_id == "p1"


@pytest.mark.asyncio
async def test_update_validates_a_changed_persona(fakes, execution) -> None:
    execution.personas.add("p1")
    execution.personas.add("p2", name="另一个")
    execution.personas.add(
        "hidden", scope="user", visibility="private", status="draft", owner_user_id="u2"
    )
    svc = fakes.service
    task = await svc.create_task(_create(persona_preset_id="p1"), "u1", actor=_user())

    with pytest.raises(ScheduledTaskPersonaError):
        await svc.update_task(
            task.id, "u1", ScheduledTaskUpdate(persona_preset_id="hidden"), actor=_user()
        )
    assert fakes.storage.tasks[task.id].persona_preset_id == "p1"

    changed = await svc.update_task(
        task.id, "u1", ScheduledTaskUpdate(persona_preset_id="p2"), actor=_user()
    )
    assert changed.persona_preset_id == "p2"

    with pytest.raises(ValueError, match="actor"):
        await svc.update_task(task.id, "u1", ScheduledTaskUpdate(persona_preset_id="p1"))


@pytest.mark.asyncio
async def test_update_with_explicit_null_clears_persona_without_actor(fakes, execution) -> None:
    execution.personas.add("p1")
    svc = fakes.service
    task = await svc.create_task(_create(persona_preset_id="p1"), "u1", actor=_user())

    untouched = await svc.update_task(task.id, "u1", ScheduledTaskUpdate(name="x"))
    assert untouched.persona_preset_id == "p1"  # unset field keeps the persona

    cleared = await svc.update_task(task.id, "u1", ScheduledTaskUpdate(persona_preset_id=None))
    assert cleared.persona_preset_id is None
    blank = await svc.update_task(
        task.id, "u1", ScheduledTaskUpdate(persona_preset_id="p1"), actor=_user()
    )
    assert blank.persona_preset_id == "p1"
    emptied = await svc.update_task(task.id, "u1", ScheduledTaskUpdate(persona_preset_id=""))
    assert emptied.persona_preset_id is None


# ── persona.py helpers ─────────────────────────


@pytest.mark.asyncio
async def test_ensure_persona_usable_returns_preset_and_does_not_count_usage(execution) -> None:
    execution.personas.add("p1", name="分析师")
    preset = await ensure_persona_usable("p1", _user())
    assert preset.name == "分析师"
    assert execution.personas.docs["p1"]["usage_count"] == 0


@pytest.mark.asyncio
async def test_persona_briefs_visible_hidden_and_unknown(execution, monkeypatch) -> None:
    execution.personas.add("ok", name="可用", avatar="🙂")
    execution.personas.add("draft", status="draft")
    calls: list[str] = []
    real = persona_mod._persona_manager()

    class _Flaky:
        async def get_preset(self, preset_id, *, user_id, is_admin):
            calls.append(preset_id)
            if preset_id == "boom":
                raise RuntimeError("db down")
            return await real.get_preset(preset_id, user_id=user_id, is_admin=is_admin)

    monkeypatch.setattr(persona_mod, "_persona_manager", lambda: _Flaky())
    briefs = await persona_briefs(
        ["ok", "ok", "draft", "gone", "boom", ""], user_id="u1", is_admin=False
    )

    assert sorted(calls) == ["boom", "draft", "gone", "ok"]  # de-duplicated, blanks dropped
    assert briefs["ok"] == {"name": "可用", "avatar": "🙂", "available": True}
    assert briefs["draft"] == {"name": None, "avatar": None, "available": False}
    assert briefs["gone"]["available"] is False
    assert briefs["boom"] == {"name": None, "avatar": None, "available": None}

    admin = await persona_briefs(["draft"], user_id="u9", is_admin=True)
    assert admin["draft"]["available"] is True
    assert await persona_briefs([], user_id="u1", is_admin=False) == {}


@pytest.mark.asyncio
async def test_to_responses_with_persona_fills_fields_per_viewer(fakes, execution) -> None:
    execution.personas.add("p1", name="分析师", avatar="📊")
    svc = fakes.service
    with_persona = await svc.create_task(_create(persona_preset_id="p1"), "u1", actor=_user())
    plain = await svc.create_task(_create(name="无专家"), "u1")

    items = await svc.to_responses_with_persona([with_persona, plain], _user())
    assert (items[0].persona_name, items[0].persona_avatar, items[0].persona_available) == (
        "分析师",
        "📊",
        True,
    )
    assert (items[1].persona_name, items[1].persona_avatar, items[1].persona_available) == (
        None,
        None,
        None,
    )

    execution.personas.docs["p1"]["status"] = "archived"
    gone = await svc.to_response_with_persona(with_persona, _user())
    assert gone.persona_available is False and gone.persona_name is None
    assert gone.persona_preset_id == "p1"
