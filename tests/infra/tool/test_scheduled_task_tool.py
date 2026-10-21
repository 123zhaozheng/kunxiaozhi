"""Chat tools for scheduled tasks: list + draft (never creates / modifies a task)."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.infra.logging.context import TraceContext
from src.infra.tool import scheduled_task_tool as tool_mod
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.persona_preset import PersonaPreset
from src.kernel.schemas.scheduled_task import (
    ScheduledTaskCreate,
    ScheduleKind,
    ScheduleSpec,
)
from src.kernel.schemas.user import TokenPayload
from tests.infra.scheduler.fakes import install_execution_fakes, install_fakes

WEEKDAYS_0830 = ScheduleSpec(kind=ScheduleKind.WEEKDAYS, time="08:30")
DAILY_0900 = ScheduleSpec(kind=ScheduleKind.DAILY, time="09:00")


class _Runtime:
    def __init__(self, user_id: str | None = "u1", session_id: str | None = "sess-1") -> None:
        context = (
            SimpleNamespace(user_id=user_id, session_id=session_id) if user_id is not None else None
        )
        self.config = {"configurable": {"context": context}}


class Env:
    def __init__(self, fakes, execution, monkeypatch) -> None:
        self.fakes = fakes
        self.execution = execution
        self.monkeypatch = monkeypatch
        self.session_persona: str | None = None

    async def make_task(self, owner: str = "u1", name: str = "晨报", **overrides: Any):
        request = ScheduledTaskCreate(
            name=name,
            prompt=overrides.pop("prompt", "整理今天的重点"),
            schedule=overrides.pop("schedule", WEEKDAYS_0830),
            **overrides,
        )
        return await self.fakes.service.create_task(
            request, owner, actor=_actor(owner), created_via="web"
        )

    async def draft(self, runtime: Any = None, **kwargs: Any) -> dict[str, Any]:
        raw = await tool_mod.draft_scheduled_task.coroutine(
            runtime=runtime or _Runtime(), **{"action": "create", **kwargs}
        )
        return json.loads(raw)

    async def list(self, runtime: Any = None) -> dict[str, Any]:
        return json.loads(
            await tool_mod.list_scheduled_tasks.coroutine(runtime=runtime or _Runtime())
        )


def _actor(sub: str) -> TokenPayload:
    return TokenPayload(
        sub=sub,
        username=sub,
        roles=["user"],
        permissions=["scheduled_task:use", "chat:write"],
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    fakes = install_fakes(monkeypatch)
    execution = install_execution_fakes(monkeypatch)
    e = Env(fakes, execution, monkeypatch)

    async def fake_session_persona(session_id):
        return e.session_persona

    async def fake_search(user, query):
        return [
            PersonaPreset(**doc)
            for doc in execution.personas.docs.values()
            if query.casefold() in doc["name"].casefold()
        ]

    monkeypatch.setattr(tool_mod, "_session_persona_id", fake_session_persona)
    monkeypatch.setattr(tool_mod, "_search_personas", fake_search)
    monkeypatch.setattr(settings, "SCHEDULED_TASK_ENABLED", True, raising=False)
    yield e
    TraceContext.clear_request_context()


def _pending_drafts(env: Env) -> list[dict[str, Any]]:
    return list(env.fakes.drafts.docs.values())


# ── list ───────────────────────────────────────


@pytest.mark.asyncio
async def test_list_only_returns_own_tasks(env: Env) -> None:
    await env.make_task("u1", "晨报")
    await env.make_task("u2", "别人的任务")

    result = await env.list()

    assert result["entity_type"] == "scheduled_task_list"
    assert result["total"] == 1 and result["limit"] >= 1
    assert "北京时间" in result["now"]
    item = result["items"][0]
    assert item["name"] == "晨报" and item["schedule_text"].startswith("每个工作日")
    assert item["enabled"] is True and item["status"] == "active"
    assert item["persona_name"] is None and item["paused_by"] is None
    assert set(item) == {
        "id",
        "name",
        "schedule_text",
        "enabled",
        "status",
        "next_run_at",
        "last_run_status",
        "last_run_at",
        "persona_name",
        "paused_by",
        "pause_reason",
    }


# ── gate: user / permission / unattended ───────


@pytest.mark.asyncio
async def test_no_user_context_returns_chinese_error(env: Env) -> None:
    for result in (
        await env.list(_Runtime(None)),
        await env.draft(_Runtime(None), name="a", prompt="b", schedule=DAILY_0900),
    ):
        assert result["error"] and "用户" in result["error"]
        assert "now" in result


@pytest.mark.asyncio
async def test_missing_permission_or_inactive_user(env: Env) -> None:
    env.execution.state["owner"] = TokenPayload(
        sub="u1", username="u1", roles=["user"], permissions=["chat:write"]
    )
    for result in (
        await env.list(),
        await env.draft(name="a", prompt="b", schedule=DAILY_0900),
    ):
        assert "权限" in result["error"]
    env.execution.state["owner"] = None
    assert "不可用" in (await env.list())["error"]
    assert not _pending_drafts(env)


@pytest.mark.asyncio
async def test_unattended_run_is_refused(env: Env) -> None:
    TraceContext.set_request_context(unattended=True)
    listed = await env.list()
    drafted = await env.draft(name="a", prompt="b", schedule=DAILY_0900)
    assert listed["error"] == drafted["error"] == "定时任务自动执行中不能管理定时任务"
    assert not _pending_drafts(env)


@pytest.mark.asyncio
async def test_feature_switch_off_is_refused(env: Env) -> None:
    from src.infra.scheduler import runner

    env.monkeypatch.setattr(runner, "settings_enabled", lambda: False)
    assert "关闭" in (await env.list())["error"]


# ── create: validation errors ──────────────────


@pytest.mark.asyncio
async def test_create_missing_fields_lists_them(env: Env) -> None:
    result = await env.draft(name="晨报")
    assert "执行内容" in result["error"] and "执行时间" in result["error"]
    assert "任务名称" not in result["error"]
    assert not _pending_drafts(env)


@pytest.mark.asyncio
async def test_invalid_action(env: Env) -> None:
    result = await env.draft(action="delete", task_id="x")
    assert "create" in result["error"] and "update" in result["error"]


@pytest.mark.asyncio
async def test_create_quota_full(env: Env) -> None:
    env.monkeypatch.setattr(settings, "SCHEDULED_TASK_MAX_PER_USER", 1, raising=False)
    await env.make_task("u1", "已有任务")
    result = await env.draft(name="晨报", prompt="整理", schedule=DAILY_0900)
    assert "最多创建 1 个" in result["error"]
    assert not _pending_drafts(env)


@pytest.mark.asyncio
async def test_create_once_in_the_past(env: Env) -> None:
    past = (utc_now() - timedelta(days=1)).replace(tzinfo=None)
    spec = ScheduleSpec(kind=ScheduleKind.ONCE, run_at=past)
    result = await env.draft(name="a", prompt="b", schedule=spec)
    assert "已经过去" in result["error"]


@pytest.mark.asyncio
async def test_create_interval_below_minimum(env: Env) -> None:
    env.monkeypatch.setattr(settings, "SCHEDULED_TASK_MIN_INTERVAL_HOURS", 2, raising=False)
    spec = ScheduleSpec(kind=ScheduleKind.INTERVAL, every_hours=1)
    result = await env.draft(name="a", prompt="b", schedule=spec)
    assert "不能少于 2 小时" in result["error"]


# ── create: success ────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_draft_without_creating_task(env: Env) -> None:
    result = await env.draft(name="晨报", prompt="整理今天的重点", schedule=WEEKDAYS_0830)

    assert result["entity_type"] == "scheduled_task_draft"
    assert result["action"] == "create" and result["task_id"] is None
    assert result["before"] is None and result["changes"] == []
    assert len(result["draft_id"]) == 32 and "尚未生效" in result["message"]
    summary = result["summary"]
    assert summary["name"] == "晨报" and summary["prompt"] == "整理今天的重点"
    assert summary["schedule_text"].startswith("每个工作日") and summary["enabled"] is True
    assert summary["persona"] is None and len(summary["next_runs"]) == 5
    assert result["expires_at"] and "北京时间" in result["now"]

    assert await env.fakes.storage.count_tasks("u1") == 0  # nothing created
    [doc] = _pending_drafts(env)
    assert doc["_id"] == result["draft_id"] and doc["status"] == "pending"
    assert doc["owner_id"] == "u1" and doc["session_id"] == "sess-1"
    assert doc["payload"]["schedule"]["kind"] == "weekdays"
    assert doc["expires_at"] - doc["created_at"] == timedelta(minutes=30)


@pytest.mark.asyncio
async def test_create_accepts_schedule_as_dict(env: Env) -> None:
    result = await env.draft(name="a", prompt="b", schedule={"kind": "daily", "time": "09:00"})
    assert result["summary"]["schedule_text"] == "每天 09:00"


# ── create: persona ────────────────────────────


@pytest.mark.asyncio
async def test_create_defaults_to_session_persona(env: Env) -> None:
    env.execution.personas.add("p1", name="检索专家", avatar="🔍")
    env.session_persona = "p1"

    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900)

    assert result["summary"]["persona"] == {"id": "p1", "name": "检索专家", "avatar": "🔍"}
    assert _pending_drafts(env)[0]["payload"]["persona_preset_id"] == "p1"


@pytest.mark.asyncio
async def test_clear_persona_overrides_session_default(env: Env) -> None:
    env.execution.personas.add("p1")
    env.session_persona = "p1"

    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900, clear_persona=True)

    assert result["summary"]["persona"] is None
    assert _pending_drafts(env)[0]["payload"]["persona_preset_id"] is None


@pytest.mark.asyncio
async def test_unusable_session_persona_falls_back_to_none(env: Env) -> None:
    env.execution.personas.add("p1", status="archived")
    env.session_persona = "p1"

    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900)

    assert result["summary"]["persona"] is None
    assert "专家已不可用" in result["message"] and "尚未生效" in result["message"]


@pytest.mark.asyncio
async def test_persona_name_unique_match_beats_session_default(env: Env) -> None:
    env.execution.personas.add("p1", name="检索专家")
    env.execution.personas.add("p2", name="写作助手", avatar="✍️")
    env.session_persona = "p1"

    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900, persona_name=" 写作助手 ")

    assert result["summary"]["persona"]["id"] == "p2"


@pytest.mark.asyncio
async def test_persona_name_match_is_case_insensitive(env: Env) -> None:
    env.execution.personas.add("p1", name="Analyst")
    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900, persona_name="analyst")
    assert result["summary"]["persona"]["id"] == "p1"


@pytest.mark.asyncio
async def test_persona_name_multiple_candidates(env: Env) -> None:
    env.execution.personas.add("p1", name="数据分析师")
    env.execution.personas.add("p2", name="数据分析助手")

    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900, persona_name="数据分析")

    assert "多个" in result["error"]
    assert sorted(result["candidates"]) == ["数据分析助手", "数据分析师"]
    assert not _pending_drafts(env)


@pytest.mark.asyncio
async def test_persona_name_no_match(env: Env) -> None:
    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900, persona_name="不存在")
    assert "没有找到" in result["error"] and "candidates" not in result


@pytest.mark.asyncio
async def test_persona_name_not_visible(env: Env) -> None:
    # Returned by the search but rejected by the visibility rules used by chat.
    env.execution.personas.add("p1", name="下架专家", status="archived")
    result = await env.draft(name="a", prompt="b", schedule=DAILY_0900, persona_name="下架专家")
    assert "专家" in result["error"] and "无权" in result["error"]
    assert not _pending_drafts(env)


# ── update ─────────────────────────────────────


@pytest.mark.asyncio
async def test_update_name_ambiguous_returns_candidates(env: Env) -> None:
    await env.make_task("u1", "晨报-财经")
    await env.make_task("u1", "晨报-科技", schedule=DAILY_0900)
    await env.make_task("u2", "晨报-别人")

    result = await env.draft(action="update", task_name="晨报", name="新名字")

    assert "多个" in result["error"]
    assert {c["name"] for c in result["candidates"]} == {"晨报-财经", "晨报-科技"}
    assert all({"id", "name", "schedule_text"} == set(c) for c in result["candidates"])
    assert not _pending_drafts(env)


@pytest.mark.asyncio
async def test_update_exact_name_wins_over_partial(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    await env.make_task("u1", "晨报-科技", schedule=DAILY_0900)

    result = await env.draft(action="update", task_name="晨报", prompt="只看财经")

    assert result["task_id"] == task.id and result["changes"] == ["prompt"]


@pytest.mark.asyncio
async def test_update_not_found_or_other_users_task(env: Env) -> None:
    other = await env.make_task("u2", "别人的")
    assert "没有找到" in (await env.draft(action="update", task_id=other.id, name="x"))["error"]
    assert "没有找到" in (await env.draft(action="update", task_name="不存在", name="x"))["error"]
    assert "task_id" in (await env.draft(action="update", name="x"))["error"]


@pytest.mark.asyncio
async def test_update_without_changes_is_rejected(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    nothing = await env.draft(action="update", task_id=task.id)
    assert "修改哪些" in nothing["error"]
    same = await env.draft(action="update", task_id=task.id, name="晨报", schedule=WEEKDAYS_0830)
    assert "无需修改" in same["error"]


@pytest.mark.asyncio
async def test_update_changes_and_before(env: Env) -> None:
    env.execution.personas.add("p1", name="检索专家", avatar="🔍")
    task = await env.make_task("u1", "晨报", persona_preset_id="p1")

    result = await env.draft(
        action="update",
        task_id=task.id,
        name="晨间简报",
        schedule=DAILY_0900,
        clear_persona=True,
    )

    assert result["action"] == "update" and result["task_id"] == task.id
    assert result["changes"] == ["name", "schedule", "persona"]
    before, after = result["before"], result["summary"]
    assert before["name"] == "晨报" and before["schedule_text"].startswith("每个工作日")
    assert before["persona"] == {"id": "p1", "name": "检索专家", "avatar": "🔍"}
    assert after["name"] == "晨间简报" and after["schedule_text"] == "每天 09:00"
    assert after["persona"] is None and after["prompt"] == "整理今天的重点"
    assert set(before) == set(after)

    [doc] = _pending_drafts(env)
    assert doc["payload"] == {
        "name": "晨间简报",
        "schedule": {
            "kind": "daily",
            "time": "09:00",
            "weekdays": None,
            "month_day": None,
            "run_at": None,
            "every_hours": None,
        },
        "persona_preset_id": None,
    }
    assert doc["base_signature"]
    stored = await env.fakes.storage.get_task(task.id, "u1")
    assert stored.name == "晨报"  # untouched until the user confirms


@pytest.mark.asyncio
async def test_update_only_enabled(env: Env) -> None:
    task = await env.make_task("u1", "晨报")

    result = await env.draft(action="update", task_id=task.id, enabled=False)

    assert result["changes"] == ["enabled"]
    assert result["before"]["enabled"] is True and result["summary"]["enabled"] is False
    assert _pending_drafts(env)[0]["payload"] == {"enabled": False}


@pytest.mark.asyncio
async def test_update_persona_by_name(env: Env) -> None:
    env.execution.personas.add("p2", name="写作助手", avatar="✍️")
    task = await env.make_task("u1", "晨报")

    result = await env.draft(action="update", task_id=task.id, persona_name="写作助手")

    assert result["changes"] == ["persona"] and result["before"]["persona"] is None
    assert result["summary"]["persona"] == {"id": "p2", "name": "写作助手", "avatar": "✍️"}


@pytest.mark.asyncio
async def test_update_invalid_new_schedule(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    spec = ScheduleSpec(kind=ScheduleKind.WEEKLY, time="08:30", weekdays=[])
    result = await env.draft(action="update", task_id=task.id, schedule=spec)
    assert "至少选择一天" in result["error"]


# ── never raises ───────────────────────────────


@pytest.mark.asyncio
async def test_internal_errors_become_generic_chinese_error(env: Env) -> None:
    def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("secret internal detail")

    env.monkeypatch.setattr(env.fakes.service, "preview", boom)

    create = await env.draft(name="a", prompt="b", schedule=DAILY_0900)
    task = await env.make_task("u1", "晨报")
    update = await env.draft(action="update", task_id=task.id, schedule=DAILY_0900)

    for result in (create, update):
        assert result["error"] == tool_mod.GENERIC_ERROR_MESSAGE
        assert "secret" not in json.dumps(result, ensure_ascii=False)
        assert "now" in result
    assert not _pending_drafts(env)


@pytest.mark.asyncio
async def test_storage_failure_in_list_does_not_raise(env: Env) -> None:
    async def boom(owner_id: str):
        raise RuntimeError("db down")

    env.monkeypatch.setattr(env.fakes.storage, "list_tasks", boom)
    result = await env.list()
    assert result["error"] == tool_mod.GENERIC_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_tools_are_registered_through_langchain_schema() -> None:
    tools = {t.name: t for t in tool_mod.get_scheduled_task_tools()}
    assert set(tools) == {tool_mod.LIST_TOOL_NAME, tool_mod.DRAFT_TOOL_NAME}
    schema = tools[tool_mod.DRAFT_TOOL_NAME].tool_call_schema.model_json_schema()
    assert "runtime" not in schema["properties"]
    assert schema["required"] == ["action"]
    assert "ScheduleSpec" in json.dumps(schema)
