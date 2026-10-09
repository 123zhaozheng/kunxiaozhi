"""Draft confirmation endpoints: only a human click on the chat card creates / changes a task."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Optional

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import scheduled_task as routes
from src.infra.logging.context import TraceContext
from src.infra.scheduler import drafts
from src.infra.scheduler import runner as runner_mod
from src.infra.tool import scheduled_task_tool as tool_mod
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    ScheduledTaskCreate,
    ScheduleKind,
    ScheduleSpec,
)
from src.kernel.schemas.user import TokenPayload
from tests.infra.scheduler.fakes import install_execution_fakes, install_fakes

BASE = "/api/scheduled-tasks"
FULL_PERMS = ["scheduled_task:use", "chat:write"]
WEEKDAYS_0830 = ScheduleSpec(kind=ScheduleKind.WEEKDAYS, time="08:30")
DAILY_0900 = ScheduleSpec(kind=ScheduleKind.DAILY, time="09:00")


def _user(sub: str, permissions: Optional[list[str]] = None) -> TokenPayload:
    return TokenPayload(
        sub=sub,
        username=sub,
        roles=["user"],
        permissions=FULL_PERMS if permissions is None else permissions,
    )


@dataclass
class Env:
    client: AsyncClient
    fakes: Any
    execution: Any
    monkeypatch: pytest.MonkeyPatch
    current: dict = field(default_factory=dict)

    def as_user(self, sub: str, permissions: Optional[list[str]] = None) -> None:
        self.current["user"] = _user(sub, permissions)

    async def draft_create(self, owner: str = "u1", **overrides: Any) -> str:
        payload = ScheduledTaskCreate(
            name=overrides.pop("name", "晨报"),
            prompt=overrides.pop("prompt", "整理今天的重点"),
            schedule=overrides.pop("schedule", WEEKDAYS_0830),
            **overrides,
        ).model_dump(mode="json")
        doc = await drafts.build_draft(
            owner_id=owner,
            session_id="sess-1",
            action="create",
            task_id=None,
            payload=payload,
            summary={"name": payload["name"]},
        )
        return doc["_id"]

    async def make_task(self, owner: str = "u1", name: str = "晨报", **overrides: Any):
        request = ScheduledTaskCreate(
            name=name,
            prompt="整理今天的重点",
            schedule=overrides.pop("schedule", WEEKDAYS_0830),
            **overrides,
        )
        return await self.fakes.service.create_task(request, owner, actor=_user(owner))

    async def draft_update(self, task, payload: dict[str, Any], changes: list[str]) -> str:
        doc = await drafts.build_draft(
            owner_id=task.owner_id,
            session_id="sess-1",
            action="update",
            task_id=task.id,
            payload=payload,
            summary={"name": task.name},
            before={"name": task.name},
            changes=changes,
            base_signature=drafts.task_signature(task),
        )
        return doc["_id"]

    async def confirm(self, draft_id: str):
        return await self.client.post(f"{BASE}/drafts/{draft_id}/confirm")

    async def cancel(self, draft_id: str):
        return await self.client.post(f"{BASE}/drafts/{draft_id}/cancel")

    async def get(self, draft_id: str):
        return await self.client.get(f"{BASE}/drafts/{draft_id}")

    def stored(self, draft_id: str) -> dict[str, Any]:
        return self.fakes.drafts.docs[draft_id]


@pytest_asyncio.fixture
async def env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    fakes = install_fakes(monkeypatch)
    execution = install_execution_fakes(monkeypatch)
    monkeypatch.setattr(routes, "get_scheduled_task_storage", lambda: fakes.storage)
    monkeypatch.setattr(settings, "SCHEDULED_TASK_ENABLED", True, raising=False)

    app = FastAPI()
    app.include_router(routes.router, prefix=BASE)
    current: dict = {}
    app.dependency_overrides[api_deps.get_current_user_required] = lambda: current["user"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        e = Env(
            client=client,
            fakes=fakes,
            execution=execution,
            monkeypatch=monkeypatch,
            current=current,
        )
        e.as_user("u1")
        yield e
    TraceContext.clear_request_context()
    await runner_mod.drain_monitors(timeout=5)


# ── create ─────────────────────────────────────


@pytest.mark.asyncio
async def test_task_is_created_only_after_confirm(env: Env) -> None:
    draft_id = await env.draft_create()
    assert await env.fakes.storage.count_tasks("u1") == 0

    pending = await env.get(draft_id)
    assert pending.status_code == 200
    body = pending.json()
    assert body["status"] == "pending" and body["action"] == "create"
    assert body["task_id"] is None and body["result_task_id"] is None
    assert set(body) == {
        "id", "action", "task_id", "status", "summary", "before",
        "changes", "expires_at", "result_task_id", "last_error",
    }  # fmt: skip

    resp = await env.confirm(draft_id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["draft"]["status"] == "confirmed"
    task = data["task"]
    assert task["name"] == "晨报" and task["created_via"] == "chat"
    assert data["draft"]["result_task_id"] == task["id"]

    stored = await env.fakes.storage.get_task(task["id"], "u1")
    assert stored.created_via == "chat" and stored.owner_id == "u1"
    assert (await env.get(draft_id)).json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_draft_from_tool_end_to_end(env: Env) -> None:
    raw = await tool_mod.draft_scheduled_task.coroutine(
        action="create",
        name="晨报",
        prompt="整理今天的重点",
        schedule=WEEKDAYS_0830,
        runtime=SimpleNamespace(
            config={"configurable": {"context": SimpleNamespace(user_id="u1", session_id="s")}}
        ),
    )
    draft_id = json.loads(raw)["draft_id"]
    assert await env.fakes.storage.count_tasks("u1") == 0

    resp = await env.confirm(draft_id)

    assert resp.status_code == 200
    assert resp.json()["task"]["created_via"] == "chat"
    assert await env.fakes.storage.count_tasks("u1") == 1


@pytest.mark.asyncio
async def test_duplicate_confirm_is_409_and_creates_once(env: Env) -> None:
    draft_id = await env.draft_create()
    assert (await env.confirm(draft_id)).status_code == 200

    again = await env.confirm(draft_id)

    assert again.status_code == 409 and again.json()["detail"] == "草稿已确认"
    assert await env.fakes.storage.count_tasks("u1") == 1


@pytest.mark.asyncio
async def test_concurrent_confirms_create_exactly_one_task(env: Env) -> None:
    draft_id = await env.draft_create()

    results = await asyncio.gather(env.confirm(draft_id), env.confirm(draft_id))

    assert sorted(r.status_code for r in results) == [200, 409]
    assert await env.fakes.storage.count_tasks("u1") == 1


@pytest.mark.asyncio
async def test_other_users_draft_is_404(env: Env) -> None:
    draft_id = await env.draft_create("u1")
    env.as_user("u2")

    assert (await env.get(draft_id)).status_code == 404
    confirm = await env.confirm(draft_id)
    assert confirm.status_code == 404 and confirm.json()["detail"] == "草稿不存在或无权查看"
    assert (await env.cancel(draft_id)).status_code == 404
    assert await env.fakes.storage.count_tasks("u1") == 0
    assert env.stored(draft_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_unknown_draft_is_404(env: Env) -> None:
    assert (await env.get("nope")).status_code == 404
    assert (await env.confirm("nope")).status_code == 404
    assert (await env.cancel("nope")).status_code == 404


@pytest.mark.asyncio
async def test_expired_draft_is_410(env: Env) -> None:
    draft_id = await env.draft_create()
    env.stored(draft_id)["expires_at"] = utc_now() - timedelta(seconds=1)

    view = await env.get(draft_id)
    assert view.json()["status"] == "expired"
    assert env.stored(draft_id)["status"] == "pending"  # expiry is derived, never stored

    resp = await env.confirm(draft_id)
    assert resp.status_code == 410 and resp.json()["detail"] == "草稿已过期，请让助手重新生成"
    assert await env.fakes.storage.count_tasks("u1") == 0


@pytest.mark.asyncio
async def test_cancel_then_confirm_is_409(env: Env) -> None:
    draft_id = await env.draft_create()

    cancelled = await env.cancel(draft_id)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"

    resp = await env.confirm(draft_id)
    assert resp.status_code == 409 and resp.json()["detail"] == "草稿已取消"
    assert (await env.cancel(draft_id)).status_code == 409
    assert await env.fakes.storage.count_tasks("u1") == 0


@pytest.mark.asyncio
async def test_cancel_after_confirm_is_409(env: Env) -> None:
    draft_id = await env.draft_create()
    await env.confirm(draft_id)
    resp = await env.cancel(draft_id)
    assert resp.status_code == 409 and resp.json()["detail"] == "草稿已确认"


@pytest.mark.asyncio
async def test_confirming_status_blocks_second_confirm(env: Env) -> None:
    draft_id = await env.draft_create()
    env.stored(draft_id)["status"] = "confirming"
    resp = await env.confirm(draft_id)
    assert resp.status_code == 409 and resp.json()["detail"] == "草稿正在处理，请稍候"


@pytest.mark.asyncio
async def test_requires_task_permissions(env: Env) -> None:
    draft_id = await env.draft_create()
    env.as_user("u1", ["chat:write"])
    assert (await env.confirm(draft_id)).status_code == 403
    assert env.stored(draft_id)["status"] == "pending"


# ── validation failures keep the draft pending ─


@pytest.mark.asyncio
async def test_quota_full_is_400_then_retry_succeeds(env: Env) -> None:
    env.monkeypatch.setattr(settings, "SCHEDULED_TASK_MAX_PER_USER", 1, raising=False)
    existing = await env.make_task("u1", "已有任务")
    draft_id = await env.draft_create()

    resp = await env.confirm(draft_id)

    assert resp.status_code == 400 and "最多创建 1 个" in resp.json()["detail"]
    stored = env.stored(draft_id)
    assert stored["status"] == "pending" and "最多创建 1 个" in stored["last_error"]
    body = (await env.get(draft_id)).json()
    assert body["status"] == "pending" and body["last_error"] == stored["last_error"]
    assert await env.fakes.storage.count_tasks("u1") == 1

    assert await env.fakes.service.delete_task(existing.id, "u1")
    ok = await env.confirm(draft_id)

    assert ok.status_code == 200 and ok.json()["draft"]["last_error"] is None
    assert await env.fakes.storage.count_tasks("u1") == 1


@pytest.mark.asyncio
async def test_schedule_gone_into_the_past_is_400(env: Env) -> None:
    draft_id = await env.draft_create()
    env.stored(draft_id)["payload"]["schedule"] = {
        "kind": "once",
        "run_at": (utc_now() - timedelta(hours=1)).replace(tzinfo=None).isoformat(),
    }
    resp = await env.confirm(draft_id)
    assert resp.status_code == 400 and "已经过去" in resp.json()["detail"]
    assert env.stored(draft_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_persona_gone_is_400(env: Env) -> None:
    env.execution.personas.add("p1")
    draft_id = await env.draft_create(persona_preset_id="p1")
    env.execution.personas.remove("p1")

    resp = await env.confirm(draft_id)

    assert resp.status_code == 400 and "专家" in resp.json()["detail"]
    assert env.stored(draft_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_feature_switched_off_is_400(env: Env) -> None:
    from src.infra.scheduler import service as service_mod

    draft_id = await env.draft_create()
    env.monkeypatch.setattr(service_mod, "settings_enabled", lambda: False)

    resp = await env.confirm(draft_id)

    assert resp.status_code == 400 and "关闭" in resp.json()["detail"]
    assert env.stored(draft_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_unexpected_error_is_generic_500_and_draft_stays_pending(env: Env) -> None:
    draft_id = await env.draft_create()

    async def boom(*args: Any, **kwargs: Any):
        raise RuntimeError("secret internal detail")

    env.monkeypatch.setattr(env.fakes.service, "create_task", boom)

    resp = await env.confirm(draft_id)

    assert resp.status_code == 500 and resp.json()["detail"] == "确认失败，请稍后重试"
    assert "secret" not in resp.text
    stored = env.stored(draft_id)
    assert stored["status"] == "pending" and stored["last_error"] == "确认失败，请稍后重试"


# ── update drafts ──────────────────────────────


@pytest.mark.asyncio
async def test_update_draft_changes_fields(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    draft_id = await env.draft_update(
        task,
        {"name": "晨间简报", "schedule": DAILY_0900.model_dump(mode="json")},
        ["name", "schedule"],
    )
    assert (await env.fakes.storage.get_task(task.id, "u1")).name == "晨报"

    resp = await env.confirm(draft_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task"]["id"] == task.id and body["task"]["name"] == "晨间简报"
    assert body["task"]["schedule"]["kind"] == "daily"
    assert body["task"]["created_via"] == "web"  # an update does not change the origin
    assert body["draft"]["status"] == "confirmed" and body["draft"]["result_task_id"] == task.id
    stored = await env.fakes.storage.get_task(task.id, "u1")
    assert stored.name == "晨间简报" and stored.prompt == "整理今天的重点"


@pytest.mark.asyncio
async def test_update_draft_clears_persona(env: Env) -> None:
    env.execution.personas.add("p1")
    task = await env.make_task("u1", "晨报", persona_preset_id="p1")
    draft_id = await env.draft_update(task, {"persona_preset_id": None}, ["persona"])

    resp = await env.confirm(draft_id)

    assert resp.status_code == 200
    assert (await env.fakes.storage.get_task(task.id, "u1")).persona_preset_id is None


@pytest.mark.asyncio
async def test_enabled_only_draft_uses_set_enabled(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    calls: list[tuple] = []
    original = env.fakes.service.set_enabled

    async def spy(task_id, owner_id, enabled):
        calls.append((task_id, owner_id, enabled))
        return await original(task_id, owner_id, enabled)

    env.monkeypatch.setattr(env.fakes.service, "set_enabled", spy)
    draft_id = await env.draft_update(task, {"enabled": False}, ["enabled"])

    resp = await env.confirm(draft_id)

    assert resp.status_code == 200 and calls == [(task.id, "u1", False)]
    stored = await env.fakes.storage.get_task(task.id, "u1")
    assert stored.enabled is False and stored.paused_by == "user"
    assert resp.json()["task"]["enabled"] is False


@pytest.mark.asyncio
async def test_mixed_payload_with_enabled_uses_update_task(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    set_enabled_calls: list[Any] = []

    async def no_set_enabled(*args: Any):
        set_enabled_calls.append(args)

    env.monkeypatch.setattr(env.fakes.service, "set_enabled", no_set_enabled)
    draft_id = await env.draft_update(
        task, {"name": "新名字", "enabled": False}, ["name", "enabled"]
    )

    resp = await env.confirm(draft_id)

    assert resp.status_code == 200 and not set_enabled_calls
    stored = await env.fakes.storage.get_task(task.id, "u1")
    assert stored.name == "新名字" and stored.enabled is False


@pytest.mark.asyncio
async def test_signature_conflict_is_409_and_draft_stays_pending(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    draft_id = await env.draft_update(task, {"prompt": "只看财经"}, ["prompt"])
    await env.fakes.service.update_task(task.id, "u1", _rename("被改过的名字"), actor=_user("u1"))

    resp = await env.confirm(draft_id)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "任务已在别处修改，请让助手重新生成草稿"
    stored_task = await env.fakes.storage.get_task(task.id, "u1")
    assert stored_task.prompt == "整理今天的重点"
    assert env.stored(draft_id)["status"] == "pending"
    assert env.stored(draft_id)["last_error"] == "任务已在别处修改，请让助手重新生成草稿"


def _rename(name: str):
    from src.kernel.schemas.scheduled_task import ScheduledTaskUpdate

    return ScheduledTaskUpdate(name=name)


@pytest.mark.asyncio
async def test_run_statistics_do_not_count_as_a_conflict(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    draft_id = await env.draft_update(task, {"prompt": "只看财经"}, ["prompt"])
    # A run refreshes updated_at / last_run_*; the user did not touch the task.
    await env.fakes.storage.record_task_result(
        task.id,
        run_id="r1",
        status=RunStatus.SUCCESS,
        session_id="s",
        finished_at=utc_now(),
    )

    resp = await env.confirm(draft_id)

    assert resp.status_code == 200
    assert (await env.fakes.storage.get_task(task.id, "u1")).prompt == "只看财经"


@pytest.mark.asyncio
async def test_task_deleted_after_draft_is_404(env: Env) -> None:
    task = await env.make_task("u1", "晨报")
    draft_id = await env.draft_update(task, {"name": "新名字"}, ["name"])
    assert await env.fakes.service.delete_task(task.id, "u1")

    resp = await env.confirm(draft_id)

    assert resp.status_code == 404 and resp.json()["detail"] == "定时任务不存在或已被删除"
    assert env.stored(draft_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_update_draft_for_another_users_task_cannot_be_applied(env: Env) -> None:
    other = await env.make_task("u2", "别人的")
    doc = await drafts.build_draft(
        owner_id="u1",
        session_id="s",
        action="update",
        task_id=other.id,
        payload={"name": "被劫持"},
        summary={},
        base_signature=drafts.task_signature(other),
    )

    resp = await env.confirm(doc["_id"])

    assert resp.status_code == 404
    assert (await env.fakes.storage.get_task(other.id, "u2")).name == "别人的"


def test_task_signature_ignores_runtime_fields() -> None:
    from src.kernel.schemas.scheduled_task import ScheduledTask

    base = {
        "_id": "t1",
        "owner_id": "u1",
        "name": "晨报",
        "prompt": "p",
        "schedule": WEEKDAYS_0830.model_dump(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    a = ScheduledTask.model_validate(base)
    b = ScheduledTask.model_validate(
        {**base, "updated_at": utc_now() + timedelta(hours=1), "total_runs": 9, "last_run_id": "x"}
    )
    c = ScheduledTask.model_validate({**base, "prompt": "changed"})
    assert drafts.task_signature(a) == drafts.task_signature(b) != drafts.task_signature(c)
