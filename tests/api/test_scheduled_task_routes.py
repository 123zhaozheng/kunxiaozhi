"""Route-level coverage for /api/scheduled-tasks (fake storage, scheduler and Redis)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import scheduled_task as routes
from src.infra.scheduler import runner as runner_mod
from src.infra.scheduler import service as service_mod
from src.infra.scheduler.runner import ScheduledTaskRunner
from src.kernel.schemas.scheduled_task import RunStatus
from src.kernel.schemas.user import TokenPayload
from tests.infra.scheduler.fakes import install_fakes

BASE = "/api/scheduled-tasks"
DISABLED_MESSAGE = "定时任务功能已被管理员关闭"
FULL_PERMS = ["scheduled_task:use", "chat:write"]

BODY = {
    "name": "晨报",
    "prompt": "整理今天的重点",
    "schedule": {"kind": "weekdays", "time": "08:30"},
}


def _user(sub: str, permissions: list[str]) -> TokenPayload:
    return TokenPayload(sub=sub, username=sub, roles=["user"], permissions=permissions)


@dataclass
class Env:
    client: AsyncClient
    fakes: object
    monkeypatch: pytest.MonkeyPatch
    current: dict = field(default_factory=dict)

    def as_user(self, sub: str, permissions: list[str] | None = None) -> None:
        self.current["user"] = _user(sub, FULL_PERMS if permissions is None else permissions)

    def switch(self, enabled: bool) -> None:
        self.monkeypatch.setattr(service_mod, "settings_enabled", lambda: enabled)
        self.monkeypatch.setattr(runner_mod, "settings_enabled", lambda: enabled)

    async def create(self, **overrides) -> dict:
        resp = await self.client.post(BASE, json={**BODY, **overrides})
        assert resp.status_code == 201, resp.text
        return resp.json()


@pytest_asyncio.fixture
async def env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    fakes = install_fakes(monkeypatch)
    monkeypatch.setattr(routes, "get_scheduled_task_storage", lambda: fakes.storage)

    app = FastAPI()
    app.include_router(routes.router, prefix=BASE)
    current: dict = {}
    app.dependency_overrides[api_deps.get_current_user_required] = lambda: current["user"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        e = Env(client=client, fakes=fakes, monkeypatch=monkeypatch, current=current)
        e.as_user("u1")
        yield e
    await runner_mod.drain_monitors(timeout=5)


# ── CRUD ───────────────────────────────────────


@pytest.mark.asyncio
async def test_crud_roundtrip(env: Env) -> None:
    created = await env.create(persona_preset_id="")
    task_id = created["id"]
    assert created["schedule_text"].startswith("每个工作日")
    assert created["agent_id"] == "fast"
    assert created["persona_preset_id"] is None
    assert created["paused_by"] is None and created["created_via"] == "web"
    assert created["persona_name"] is None and created["persona_available"] is None
    assert "owner_id" not in created

    listing = (await env.client.get(BASE)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == task_id
    assert listing["limit"] >= 1 and listing["min_interval_hours"] >= 1

    got = await env.client.get(f"{BASE}/{task_id}")
    assert got.status_code == 200 and got.json()["name"] == "晨报"

    patched = await env.client.patch(f"{BASE}/{task_id}", json={"name": "晨间简报"})
    assert patched.status_code == 200 and patched.json()["name"] == "晨间简报"

    paused = await env.client.post(f"{BASE}/{task_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False and paused.json()["paused_by"] == "user"

    resumed = await env.client.post(f"{BASE}/{task_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["enabled"] is True and resumed.json()["paused_by"] is None

    runs = await env.client.get(f"{BASE}/{task_id}/runs")
    assert runs.status_code == 200 and runs.json() == {"items": [], "total": 0}

    deleted = await env.client.delete(f"{BASE}/{task_id}")
    assert deleted.status_code == 204
    assert (await env.client.get(f"{BASE}/{task_id}")).status_code == 404


@pytest.mark.asyncio
async def test_validation_errors(env: Env) -> None:
    bad_agent = await env.client.post(BASE, json={**BODY, "agent_id": "team"})
    assert bad_agent.status_code == 422
    bad_schedule = await env.client.post(
        BASE, json={**BODY, "schedule": {"kind": "interval", "every_hours": 0}}
    )
    assert bad_schedule.status_code == 400
    assert bad_schedule.json()["detail"]


@pytest.mark.asyncio
async def test_templates_and_preview(env: Env) -> None:
    assert (await env.client.get(f"{BASE}/templates")).status_code == 200
    preview = await env.client.post(BASE + "/preview", json={"schedule": BODY["schedule"]})
    assert preview.status_code == 200 and preview.json()["valid"] is True


# ── isolation & permissions ────────────────────


@pytest.mark.asyncio
async def test_other_users_tasks_are_hidden(env: Env) -> None:
    task_id = (await env.create())["id"]
    env.as_user("u2")

    assert (await env.client.get(BASE)).json()["items"] == []
    for method, path in [
        ("GET", f"{BASE}/{task_id}"),
        ("PATCH", f"{BASE}/{task_id}"),
        ("POST", f"{BASE}/{task_id}/pause"),
        ("POST", f"{BASE}/{task_id}/resume"),
        ("POST", f"{BASE}/{task_id}/run"),
        ("DELETE", f"{BASE}/{task_id}"),
    ]:
        resp = await env.client.request(method, path, json={"name": "x"} if method == "PATCH" else None)
        assert resp.status_code == 404, (method, path, resp.text)
    runs = await env.client.get(f"{BASE}/{task_id}/runs")
    assert runs.json()["total"] == 0

    env.as_user("u1")
    assert (await env.client.get(f"{BASE}/{task_id}")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permissions,missing",
    [
        (["chat:write"], "scheduled_task:use"),
        (["scheduled_task:use"], "chat:write"),
        ([], "scheduled_task:use"),
    ],
)
async def test_missing_permission_is_forbidden(env: Env, permissions, missing) -> None:
    task_id = (await env.create())["id"]
    env.as_user("u1", permissions)

    for method, path in [
        ("GET", BASE),
        ("POST", BASE),
        ("GET", f"{BASE}/{task_id}"),
        ("POST", f"{BASE}/{task_id}/run"),
        ("DELETE", f"{BASE}/{task_id}"),
        ("GET", f"{BASE}/templates"),
    ]:
        resp = await env.client.request(method, path, json=BODY if method == "POST" else None)
        assert resp.status_code == 403, (method, path)
        assert missing in resp.json()["detail"]
    # nothing was touched
    assert task_id in env.fakes.storage.tasks


# ── master switch ──────────────────────────────


@pytest.mark.asyncio
async def test_switch_off_blocks_create_update_resume_run(env: Env) -> None:
    active_id = (await env.create())["id"]
    paused_id = (await env.create(name="周报", enabled=False))["id"]
    env.switch(False)

    blocked = [
        await env.client.post(BASE, json=BODY),
        await env.client.patch(f"{BASE}/{active_id}", json={"name": "改名"}),
        await env.client.patch(f"{BASE}/{paused_id}", json={"enabled": True}),
        await env.client.post(f"{BASE}/{paused_id}/resume"),
        await env.client.post(f"{BASE}/{active_id}/run"),
    ]
    for resp in blocked:
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == DISABLED_MESSAGE
    assert env.fakes.storage.tasks[active_id].name == "晨报"
    assert len(env.fakes.storage.tasks) == 2


@pytest.mark.asyncio
async def test_switch_off_keeps_read_pause_delete_working(env: Env) -> None:
    first = (await env.create())["id"]
    second = (await env.create(name="周报"))["id"]
    third = (await env.create(name="月报"))["id"]
    env.switch(False)

    assert (await env.client.get(BASE)).json()["total"] == 3
    assert (await env.client.get(f"{BASE}/{first}")).status_code == 200
    assert (await env.client.get(f"{BASE}/{first}/runs")).status_code == 200
    assert (await env.client.get(f"{BASE}/templates")).status_code == 200
    assert (
        await env.client.post(BASE + "/preview", json={"schedule": BODY["schedule"]})
    ).status_code == 200

    paused = await env.client.post(f"{BASE}/{first}/pause")
    assert paused.status_code == 200 and paused.json()["paused_by"] == "user"
    patched = await env.client.patch(f"{BASE}/{second}", json={"enabled": False})
    assert patched.status_code == 200 and patched.json()["enabled"] is False

    assert (await env.client.delete(f"{BASE}/{third}")).status_code == 204
    assert third not in env.fakes.storage.tasks


# ── run now ────────────────────────────────────


@pytest.mark.asyncio
async def test_run_now_submits_and_conflicts_when_already_running(env: Env) -> None:
    async def fake_execute(self, *args):
        return RunStatus.SUCCESS, None

    async def fake_preview(self, session_id, run_id):
        return ""

    async def fake_notify(self, *args, **kwargs):
        return None

    env.monkeypatch.setattr(ScheduledTaskRunner, "_execute", fake_execute)
    env.monkeypatch.setattr(ScheduledTaskRunner, "_load_preview", fake_preview)
    env.monkeypatch.setattr(ScheduledTaskRunner, "_notify", fake_notify)
    task_id = (await env.create())["id"]

    started = await env.client.post(f"{BASE}/{task_id}/run")
    assert started.status_code == 200
    assert started.json()["status"] == "submitted" and started.json()["run_id"]
    await asyncio.sleep(0)
    await runner_mod.drain_monitors(timeout=5)
    assert env.fakes.storage.tasks[task_id].total_runs == 1

    env.fakes.redis.data[f"scheduler:task_lock:{task_id}"] = "someone-else"
    busy = await env.client.post(f"{BASE}/{task_id}/run")
    assert busy.status_code == 409
    assert busy.json()["detail"] == "这个任务正在执行中，请等它完成后再试"
