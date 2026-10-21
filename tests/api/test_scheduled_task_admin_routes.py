"""Route-level coverage for /api/admin/scheduled-tasks and the owner-side admin-pause guards."""

from __future__ import annotations

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
from src.api.routes import scheduled_task as user_routes
from src.api.routes import scheduled_task_admin as admin_routes
from src.infra.logging.context import TraceContext
from src.infra.scheduler import drafts
from src.infra.scheduler import runner as runner_mod
from src.infra.scheduler import service as service_mod
from src.infra.scheduler.service import job_id_for
from src.infra.tool import scheduled_task_tool as tool_mod
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.scheduled_task import (
    RunStatus,
    ScheduledTaskCreate,
    ScheduleKind,
    ScheduleSpec,
    TaskRunRecord,
)
from src.kernel.schemas.user import TokenPayload
from tests.infra.scheduler.fakes import install_execution_fakes, install_fakes

USER_BASE = "/api/scheduled-tasks"
ADMIN_BASE = "/api/admin/scheduled-tasks"
USER_PERMS = ["scheduled_task:use", "chat:write"]
ADMIN_PERMS = ["scheduled_task:admin"]
WEEKDAYS_0830 = ScheduleSpec(kind=ScheduleKind.WEEKDAYS, time="08:30")
DAILY_0900 = ScheduleSpec(kind=ScheduleKind.DAILY, time="09:00")
USERNAMES = {"u1": "zhangsan", "u2": "lisi", "u3": "wangwu"}
ADMIN_MESSAGE = "该任务已被管理员暂停（原因：{reason}），如需恢复请联系管理员"


def _user(sub: str, permissions: list[str]) -> TokenPayload:
    return TokenPayload(
        sub=sub, username=USERNAMES.get(sub, sub), roles=["user"], permissions=permissions
    )


@dataclass
class Env:
    client: AsyncClient
    fakes: Any
    execution: Any
    monkeypatch: pytest.MonkeyPatch
    current: dict = field(default_factory=dict)

    def as_user(self, sub: str = "u1", permissions: Optional[list[str]] = None) -> None:
        self.current["user"] = _user(sub, USER_PERMS if permissions is None else permissions)

    def as_admin(self) -> None:
        self.current["user"] = _user("root", ADMIN_PERMS)

    async def make_task(self, owner: str = "u1", name: str = "晨报", **overrides: Any):
        request = ScheduledTaskCreate(
            name=name,
            prompt="整理今天的重点",
            schedule=overrides.pop("schedule", WEEKDAYS_0830),
            **overrides,
        )
        task = await self.fakes.service.create_task(request, owner, actor=_user(owner, USER_PERMS))
        return task

    async def admin_pause(self, task, reason: str = "内容不合规"):
        """Pause as an administrator through the HTTP API and restore the previous caller."""
        previous = self.current["user"]
        self.as_admin()
        resp = await self.client.post(f"{ADMIN_BASE}/{task.id}/pause", json={"reason": reason})
        self.current["user"] = previous
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def add_run(self, task, run_id: str, status: RunStatus = RunStatus.SUCCESS) -> None:
        await self.fakes.storage.create_run(
            TaskRunRecord(
                _id=run_id,
                task_id=task.id,
                owner_id=task.owner_id,
                task_name=task.name,
                status=status,
                started_at=utc_now(),
            )
        )

    async def set_task(self, task, **fields: Any) -> None:
        await self.fakes.storage.update_task(task.id, fields)

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


@pytest_asyncio.fixture
async def env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    fakes = install_fakes(monkeypatch)
    execution = install_execution_fakes(monkeypatch)
    monkeypatch.setattr(user_routes, "get_scheduled_task_storage", lambda: fakes.storage)
    monkeypatch.setattr(admin_routes, "get_scheduled_task_storage", lambda: fakes.storage)
    monkeypatch.setattr(settings, "SCHEDULED_TASK_ENABLED", True, raising=False)

    async def fake_load_usernames(user_ids: list[str]) -> dict[str, str]:
        return {uid: USERNAMES[uid] for uid in user_ids if uid in USERNAMES}

    async def fake_user_ids_matching(search: str) -> list[str]:
        needle = search.strip().casefold()
        return [uid for uid, name in USERNAMES.items() if needle in name.casefold()]

    monkeypatch.setattr(admin_routes, "load_usernames", fake_load_usernames)
    monkeypatch.setattr(admin_routes, "user_ids_matching_username", fake_user_ids_matching)

    app = FastAPI()
    app.include_router(user_routes.router, prefix=USER_BASE)
    app.include_router(admin_routes.router, prefix=ADMIN_BASE)
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
        e.as_admin()
        yield e
    TraceContext.clear_request_context()
    await runner_mod.drain_monitors(timeout=5)


# ── permission ─────────────────────────────────


@pytest.mark.asyncio
async def test_use_permission_alone_cannot_reach_admin_endpoints(env: Env) -> None:
    task = await env.make_task("u1")
    env.as_user("u1")

    for method, path, kwargs in (
        ("GET", "", {}),
        ("POST", f"/{task.id}/pause", {"json": {"reason": "x"}}),
        ("POST", f"/{task.id}/resume", {}),
        ("DELETE", f"/{task.id}", {}),
        ("GET", f"/{task.id}/runs", {}),
    ):
        resp = await env.client.request(method, f"{ADMIN_BASE}{path}", **kwargs)
        assert resp.status_code == 403, (method, path, resp.text)
    assert (await env.fakes.storage.get_task(task.id)) is not None
    assert (await env.fakes.storage.get_task(task.id)).enabled is True


# ── list / filters / paging ────────────────────


@pytest.mark.asyncio
async def test_lists_tasks_across_users_with_owner_name(env: Env) -> None:
    t1 = await env.make_task("u1", "晨报")
    t2 = await env.make_task("u2", "周报")

    resp = await env.client.get(ADMIN_BASE)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2 and body["skip"] == 0 and body["limit"] == 20
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[t1.id]["owner_id"] == "u1" and by_id[t1.id]["owner_name"] == "zhangsan"
    assert by_id[t2.id]["owner_id"] == "u2" and by_id[t2.id]["owner_name"] == "lisi"
    assert {"schedule_text", "next_run_at", "paused_by", "persona_available"} <= set(by_id[t1.id])


@pytest.mark.asyncio
async def test_owner_name_is_null_when_user_unknown(env: Env) -> None:
    await env.make_task("ghost", "孤儿任务")

    items = (await env.client.get(ADMIN_BASE)).json()["items"]

    assert items[0]["owner_id"] == "ghost" and items[0]["owner_name"] is None


@pytest.mark.asyncio
async def test_filter_by_name_is_case_insensitive_and_escaped(env: Env) -> None:
    await env.make_task("u1", "Daily Report")
    await env.make_task("u1", "周报 (v2)")
    await env.make_task("u2", "其他")

    by_name = (await env.client.get(ADMIN_BASE, params={"q": "DAILY"})).json()
    assert [i["name"] for i in by_name["items"]] == ["Daily Report"] and by_name["total"] == 1

    # Regex metacharacters are literal, not a pattern.
    literal = (await env.client.get(ADMIN_BASE, params={"q": "(v2)"})).json()
    assert [i["name"] for i in literal["items"]] == ["周报 (v2)"]
    assert (await env.client.get(ADMIN_BASE, params={"q": ".*"})).json()["total"] == 0


@pytest.mark.asyncio
async def test_filter_by_owner_username(env: Env) -> None:
    await env.make_task("u1", "晨报")
    await env.make_task("u2", "周报")

    hit = (await env.client.get(ADMIN_BASE, params={"owner": "LIS"})).json()
    assert [i["owner_name"] for i in hit["items"]] == ["lisi"] and hit["total"] == 1

    miss = (await env.client.get(ADMIN_BASE, params={"owner": "nobody"})).json()
    assert miss["items"] == [] and miss["total"] == 0


@pytest.mark.asyncio
async def test_filter_by_status_and_last_run_status(env: Env) -> None:
    active = await env.make_task("u1", "运行中")
    paused = await env.make_task("u1", "已暂停")
    failed = await env.make_task("u2", "失败的")
    await env.admin_pause(paused)
    await env.set_task(failed, last_run_status=RunStatus.FAILED)
    await env.set_task(active, last_run_status=RunStatus.SUCCESS)

    by_status = (await env.client.get(ADMIN_BASE, params={"status": "paused"})).json()
    assert [i["id"] for i in by_status["items"]] == [paused.id]

    by_run = (await env.client.get(ADMIN_BASE, params={"last_run_status": "failed"})).json()
    assert [i["id"] for i in by_run["items"]] == [failed.id]

    combined = (
        await env.client.get(ADMIN_BASE, params={"status": "active", "last_run_status": "success"})
    ).json()
    assert [i["id"] for i in combined["items"]] == [active.id]

    assert (await env.client.get(ADMIN_BASE, params={"status": "bogus"})).status_code == 422
    assert (
        await env.client.get(ADMIN_BASE, params={"last_run_status": "bogus"})
    ).status_code == 422


@pytest.mark.asyncio
async def test_pagination_skip_limit_total(env: Env) -> None:
    for index in range(5):
        await env.make_task("u1", f"任务{index}")

    first = (await env.client.get(ADMIN_BASE, params={"skip": 0, "limit": 2})).json()
    second = (await env.client.get(ADMIN_BASE, params={"skip": 2, "limit": 2})).json()
    last = (await env.client.get(ADMIN_BASE, params={"skip": 4, "limit": 2})).json()

    assert first["total"] == second["total"] == last["total"] == 5
    assert (first["skip"], first["limit"]) == (0, 2) and (second["skip"], last["skip"]) == (2, 4)
    assert [len(p["items"]) for p in (first, second, last)] == [2, 2, 1]
    ids = [i["id"] for p in (first, second, last) for i in p["items"]]
    assert len(set(ids)) == 5

    assert (await env.client.get(ADMIN_BASE, params={"limit": 101})).status_code == 422
    assert (await env.client.get(ADMIN_BASE, params={"limit": 0})).status_code == 422
    assert (await env.client.get(ADMIN_BASE, params={"skip": -1})).status_code == 422


@pytest.mark.asyncio
async def test_persona_availability_is_from_the_owners_point_of_view(env: Env) -> None:
    env.execution.personas.add("p-ok", name="分析师")
    env.execution.personas.add("p-gone", name="已下架专家")
    ok = await env.make_task("u1", "有专家", persona_preset_id="p-ok")
    gone = await env.make_task("u2", "专家下架", persona_preset_id="p-gone")
    plain = await env.make_task("u2", "无专家")

    items = {i["id"]: i for i in (await env.client.get(ADMIN_BASE)).json()["items"]}

    assert items[ok.id]["persona_available"] is True and items[ok.id]["persona_name"] == "分析师"
    assert items[gone.id]["persona_available"] is True
    assert items[plain.id]["persona_available"] is None and items[plain.id]["persona_name"] is None

    # Archived after the task was created: the owner loses access.
    env.execution.personas.docs["p-gone"]["status"] = "archived"
    items = {i["id"]: i for i in (await env.client.get(ADMIN_BASE)).json()["items"]}
    assert items[gone.id]["persona_available"] is False
    # The administrator can still see which persona it was.
    assert items[gone.id]["persona_name"] == "已下架专家"


# ── pause ──────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_requires_a_reason(env: Env) -> None:
    task = await env.make_task("u1")
    path = f"{ADMIN_BASE}/{task.id}/pause"

    for payload in ({}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 201}):
        assert (await env.client.post(path, json=payload)).status_code == 422, payload
    stored = await env.fakes.storage.get_task(task.id)
    assert stored.enabled is True and stored.paused_by is None


@pytest.mark.asyncio
async def test_pause_marks_task_and_stops_schedule(env: Env) -> None:
    task = await env.make_task("u1")
    assert env.fakes.scheduler.has_job(job_id_for(task.id))

    resp = await env.client.post(f"{ADMIN_BASE}/{task.id}/pause", json={"reason": "  内容不合规 "})

    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["paused_by"] == "admin" and item["pause_reason"] == "管理员已暂停：内容不合规"
    assert item["enabled"] is False and item["status"] == "paused"
    assert item["owner_id"] == "u1" and item["owner_name"] == "zhangsan"
    stored = await env.fakes.storage.get_task(task.id)
    assert stored.paused_by == "admin" and stored.enabled is False
    assert not env.fakes.scheduler.has_job(job_id_for(task.id))


@pytest.mark.asyncio
async def test_pause_unknown_task_is_404(env: Env) -> None:
    resp = await env.client.post(f"{ADMIN_BASE}/missing/pause", json={"reason": "x"})
    assert resp.status_code == 404


# ── owner side of an admin pause ───────────────


@pytest.mark.asyncio
async def test_owner_cannot_resume_run_or_enable_admin_paused_task(env: Env) -> None:
    task = await env.make_task("u1")
    await env.admin_pause(task, "内容不合规")
    env.as_user("u1")
    expected = ADMIN_MESSAGE.format(reason="内容不合规")

    resume = await env.client.post(f"{USER_BASE}/{task.id}/resume")
    patch = await env.client.patch(f"{USER_BASE}/{task.id}", json={"enabled": True})
    run = await env.client.post(f"{USER_BASE}/{task.id}/run")

    for resp in (resume, patch, run):
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == expected
    stored = await env.fakes.storage.get_task(task.id)
    assert stored.enabled is False and stored.paused_by == "admin"
    assert env.execution.task_manager.submits == []
    assert not env.fakes.scheduler.has_job(job_id_for(task.id))


@pytest.mark.asyncio
async def test_owner_pausing_again_keeps_admin_hold_and_can_still_rename(env: Env) -> None:
    task = await env.make_task("u1")
    await env.admin_pause(task, "内容不合规")
    env.as_user("u1")

    paused = await env.client.post(f"{USER_BASE}/{task.id}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["paused_by"] == "admin"
    patched = await env.client.patch(f"{USER_BASE}/{task.id}", json={"enabled": False})
    assert patched.status_code == 200 and patched.json()["paused_by"] == "admin"

    renamed = await env.client.patch(
        f"{USER_BASE}/{task.id}",
        json={"name": "新名字", "schedule": {"kind": "daily", "time": "09:00"}},
    )

    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "新名字" and body["enabled"] is False
    assert body["paused_by"] == "admin" and body["pause_reason"] == "管理员已暂停：内容不合规"
    assert not env.fakes.scheduler.has_job(job_id_for(task.id))


# ── resume ─────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_resume_restores_the_task(env: Env) -> None:
    task = await env.make_task("u1")
    await env.admin_pause(task)

    resp = await env.client.post(f"{ADMIN_BASE}/{task.id}/resume")

    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["enabled"] is True and item["status"] == "active"
    assert item["paused_by"] is None and item["pause_reason"] is None
    assert item["owner_name"] == "zhangsan"
    assert env.fakes.scheduler.has_job(job_id_for(task.id))
    # Once released, the owner regains control.
    env.as_user("u1")
    assert (await env.client.post(f"{USER_BASE}/{task.id}/pause")).json()["paused_by"] == "user"
    assert (await env.client.post(f"{USER_BASE}/{task.id}/resume")).status_code == 200


@pytest.mark.asyncio
async def test_admin_resume_is_400_when_feature_is_off(env: Env) -> None:
    task = await env.make_task("u1")
    await env.admin_pause(task)
    env.monkeypatch.setattr(service_mod, "settings_enabled", lambda: False)
    env.monkeypatch.setattr(runner_mod, "settings_enabled", lambda: False)

    resp = await env.client.post(f"{ADMIN_BASE}/{task.id}/resume")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "定时任务功能已被管理员关闭"
    assert (await env.fakes.storage.get_task(task.id)).paused_by == "admin"


@pytest.mark.asyncio
async def test_admin_resume_is_400_for_a_past_one_shot(env: Env) -> None:
    task = await env.make_task(
        "u1",
        "一次性",
        schedule=ScheduleSpec(kind=ScheduleKind.ONCE, run_at=utc_now() + timedelta(days=1)),
    )
    await env.admin_pause(task)
    past = (utc_now() - timedelta(days=1)).replace(tzinfo=None)
    await env.set_task(task, schedule={"kind": "once", "run_at": past})

    resp = await env.client.post(f"{ADMIN_BASE}/{task.id}/resume")

    assert resp.status_code == 400 and resp.json()["detail"]
    assert (await env.fakes.storage.get_task(task.id)).enabled is False


@pytest.mark.asyncio
async def test_admin_resume_unknown_task_is_404(env: Env) -> None:
    assert (await env.client.post(f"{ADMIN_BASE}/missing/resume")).status_code == 404


# ── delete ─────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_delete_removes_task_and_runs(env: Env) -> None:
    task = await env.make_task("u1")
    other = await env.make_task("u2", "别人的")
    await env.add_run(task, "r1")
    await env.add_run(task, "r2")
    await env.add_run(other, "r3")

    resp = await env.client.delete(f"{ADMIN_BASE}/{task.id}")

    assert resp.status_code == 204
    assert await env.fakes.storage.get_task(task.id) is None
    assert set(env.fakes.storage.runs) == {"r3"}
    assert not env.fakes.scheduler.has_job(job_id_for(task.id))
    assert (await env.client.delete(f"{ADMIN_BASE}/{task.id}")).status_code == 404
    assert (await env.client.get(f"{ADMIN_BASE}/{task.id}/runs")).status_code == 404


# ── runs ───────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_list_runs_of_any_users_task(env: Env) -> None:
    task = await env.make_task("u2")
    await env.add_run(task, "r1", RunStatus.SUCCESS)
    await env.add_run(task, "r2", RunStatus.FAILED)

    resp = await env.client.get(f"{ADMIN_BASE}/{task.id}/runs", params={"skip": 0, "limit": 1})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"items", "total"} and body["total"] == 2 and len(body["items"]) == 1
    assert body["items"][0]["task_id"] == task.id and body["items"][0]["owner_id"] == "u2"


@pytest.mark.asyncio
async def test_runs_of_unknown_task_is_404(env: Env) -> None:
    assert (await env.client.get(f"{ADMIN_BASE}/missing/runs")).status_code == 404


# ── chat drafts ────────────────────────────────


def _runtime(user_id: str = "u1") -> SimpleNamespace:
    return SimpleNamespace(
        config={"configurable": {"context": SimpleNamespace(user_id=user_id, session_id="s")}}
    )


@pytest.mark.asyncio
async def test_tool_refuses_to_draft_a_resume_of_an_admin_paused_task(env: Env) -> None:
    task = await env.make_task("u1")
    await env.admin_pause(task, "内容不合规")

    raw = await tool_mod.draft_scheduled_task.coroutine(
        action="update", task_id=task.id, enabled=True, runtime=_runtime()
    )

    result = json.loads(raw)
    assert result["error"] == ADMIN_MESSAGE.format(reason="内容不合规")
    assert "draft_id" not in result
    assert env.fakes.drafts.docs == {}


@pytest.mark.asyncio
async def test_tool_refuses_reschedule_that_would_reactivate_a_completed_admin_paused_task(
    env: Env,
) -> None:
    task = await env.make_task("u1")
    await env.admin_pause(task, "停用整改")
    await env.set_task(task, status="completed")

    raw = await tool_mod.draft_scheduled_task.coroutine(
        action="update", task_id=task.id, schedule=DAILY_0900, runtime=_runtime()
    )

    assert json.loads(raw)["error"] == ADMIN_MESSAGE.format(reason="停用整改")
    assert env.fakes.drafts.docs == {}


@pytest.mark.asyncio
async def test_tool_still_drafts_a_rename_of_an_admin_paused_task(env: Env) -> None:
    task = await env.make_task("u1")
    await env.admin_pause(task)

    raw = await tool_mod.draft_scheduled_task.coroutine(
        action="update", task_id=task.id, name="新名字", runtime=_runtime()
    )

    assert json.loads(raw)["entity_type"] == "scheduled_task_draft"
    assert len(env.fakes.drafts.docs) == 1


@pytest.mark.asyncio
async def test_confirming_a_resume_draft_after_admin_pause_is_403_and_stays_pending(
    env: Env,
) -> None:
    task = await env.make_task("u1")
    await env.set_task(task, enabled=False, status="paused", paused_by="user")
    paused = await env.fakes.storage.get_task(task.id)
    draft_id = await env.draft_update(paused, {"enabled": True}, ["enabled"])

    # The administrator steps in after the draft was generated. Keep the signature in sync
    # so the rejection comes from the admin guard rather than the stale-draft check.
    env.as_admin()
    resp = await env.client.post(f"{ADMIN_BASE}/{task.id}/pause", json={"reason": "违规"})
    assert resp.status_code == 200
    env.fakes.drafts.docs[draft_id]["base_signature"] = drafts.task_signature(
        await env.fakes.storage.get_task(task.id)
    )

    env.as_user("u1")
    confirm = await env.client.post(f"{USER_BASE}/drafts/{draft_id}/confirm")

    assert confirm.status_code == 403, confirm.text
    assert confirm.json()["detail"] == ADMIN_MESSAGE.format(reason="违规")
    stored = env.fakes.drafts.docs[draft_id]
    assert stored["status"] == "pending"
    assert stored["last_error"] == ADMIN_MESSAGE.format(reason="违规")
    task_now = await env.fakes.storage.get_task(task.id)
    assert task_now.enabled is False and task_now.paused_by == "admin"
    body = (await env.client.get(f"{USER_BASE}/drafts/{draft_id}")).json()
    assert body["status"] == "pending" and body["last_error"] == ADMIN_MESSAGE.format(reason="违规")
