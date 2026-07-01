"""API routes for WeCom connection status and reconnect."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import persona_preset as persona_preset_route
from src.kernel.schemas.persona_preset import (
    PersonaPreset,
    PersonaPresetScope,
    PersonaPresetStatus,
    PersonaPresetVisibility,
)
from src.kernel.schemas.user import TokenPayload


def _channel_admin() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["channel:manage", "persona_preset:read"],
    )


@pytest.mark.asyncio
async def test_get_wecom_status_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_preset = PersonaPreset(
        id="preset-global",
        scope=PersonaPresetScope.GLOBAL,
        owner_user_id=None,
        name="Bot",
        system_prompt="Hi",
        visibility=PersonaPresetVisibility.PUBLIC,
        status=PersonaPresetStatus.PUBLISHED,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )

    async def _validate(_preset_id: str) -> PersonaPreset:
        return global_preset

    class _Storage:
        async def preset_has_wecom(self, preset_id: str) -> bool:
            return preset_id == "preset-global"

    monkeypatch.setattr(persona_preset_route, "_validate_global_preset", _validate)
    monkeypatch.setattr(
        persona_preset_route,
        "get_agent_config_storage",
        lambda: _Storage(),
    )

    app = FastAPI()
    app.include_router(persona_preset_route.router, prefix="/api/persona-presets")
    app.dependency_overrides[api_deps.get_current_user_required] = _channel_admin

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/persona-presets/preset-global/wecom/status")

    assert response.status_code == 200
    body = response.json()
    assert body["preset_id"] == "preset-global"
    assert body["state"] == "disconnected"


@pytest.mark.asyncio
async def test_reconnect_calls_reload_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_preset = PersonaPreset(
        id="preset-global",
        scope=PersonaPresetScope.GLOBAL,
        owner_user_id=None,
        name="Bot",
        system_prompt="Hi",
        visibility=PersonaPresetVisibility.PUBLIC,
        status=PersonaPresetStatus.PUBLISHED,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )

    async def _validate(_preset_id: str) -> PersonaPreset:
        return global_preset

    class _Storage:
        async def preset_has_wecom(self, preset_id: str) -> bool:
            return True

    reloaded: list[str] = []

    class _Manager:
        async def reload_preset(self, preset_id: str) -> bool:
            reloaded.append(preset_id)
            return True

    monkeypatch.setattr(persona_preset_route, "_validate_global_preset", _validate)
    monkeypatch.setattr(
        persona_preset_route,
        "get_agent_config_storage",
        lambda: _Storage(),
    )
    monkeypatch.setattr(
        "src.infra.agent.wecom.manager.get_wecom_bot_manager",
        lambda: _Manager(),
    )

    app = FastAPI()
    app.include_router(persona_preset_route.router, prefix="/api/persona-presets")
    app.dependency_overrides[api_deps.get_current_user_required] = _channel_admin

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/persona-presets/preset-global/wecom/reconnect")

    assert response.status_code == 200
    assert reloaded == ["preset-global"]


@pytest.mark.asyncio
async def test_batch_wecom_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Storage:
        async def preset_has_wecom(self, preset_id: str) -> bool:
            return preset_id == "p-with-wecom"

    monkeypatch.setattr(
        persona_preset_route,
        "get_agent_config_storage",
        lambda: _Storage(),
    )

    app = FastAPI()
    app.include_router(persona_preset_route.router, prefix="/api/persona-presets")
    app.dependency_overrides[api_deps.get_current_user_required] = _channel_admin

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/persona-presets/wecom/status",
            json={"preset_ids": ["p-with-wecom", "p-none"]},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["statuses"]) == 2
    by_id = {s["preset_id"]: s for s in data["statuses"]}
    assert by_id["p-with-wecom"]["state"] == "disconnected"
    assert by_id["p-none"]["reason_detail"] == "wecom_not_configured"