"""API routes for persona WeCom notify-targets configuration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


def _no_perms_user() -> TokenPayload:
    return TokenPayload(
        sub="user-1",
        username="user",
        roles=["user"],
        permissions=[],
    )


class _Config:
    def __init__(self, preset_id: str, aibotid: str, targets: list[str]) -> None:
        self.preset_id = preset_id
        self.aibotid = aibotid
        self.feedback_notify_targets = targets


class _Storage:
    def __init__(self, config: _Config | None) -> None:
        self.config = config
        self.set_calls: list[dict[str, Any]] = []

    async def get_persona_wecom_config(
        self, preset_id: str
    ) -> _Config | None:
        return self.config

    async def set_persona_wecom_config(
        self, preset_id: str, aibotid: str, secret: str | None = None, **kwargs: Any
    ) -> _Config | None:
        assert self.config is not None
        self.set_calls.append(
            {"preset_id": preset_id, "aibotid": aibotid, "secret": secret, **kwargs}
        )
        self.config.aibotid = aibotid
        self.config.feedback_notify_targets = kwargs.get(
            "feedback_notify_targets", []
        )
        return self.config


class _Binding:
    def __init__(self, bound: set[str] | None = None) -> None:
        self.bound = bound or set()

    async def list_bound(self, aibotid: str, usernames: list[str]) -> set[str]:
        return {u for u in usernames if u in self.bound}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(persona_preset_route.router, prefix="/api/persona-presets")
    app.dependency_overrides[api_deps.get_current_user_required] = _channel_admin
    return app


def _global_preset() -> PersonaPreset:
    return PersonaPreset(
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


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    storage: _Storage,
    binding: _Binding,
) -> None:
    async def _validate(_preset_id: str) -> PersonaPreset:
        return _global_preset()

    monkeypatch.setattr(persona_preset_route, "_validate_global_preset", _validate)
    monkeypatch.setattr(
        persona_preset_route,
        "get_agent_config_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        persona_preset_route,
        "WeComNotifyBindingStorage",
        lambda: binding,
    )


@pytest.mark.asyncio
async def test_notify_targets_require_channel_manage_permission() -> None:
    app = FastAPI()
    app.include_router(persona_preset_route.router, prefix="/api/persona-presets")
    app.dependency_overrides[api_deps.get_current_user_required] = _no_perms_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/persona-presets/preset-global/wecom/notify-targets"
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_notify_targets_returns_targets_with_bound_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_Config("preset-global", "bot-1", ["10001", "10002", "10003"]))
    binding = _Binding(bound={"10001", "10003"})
    _setup(monkeypatch, storage, binding)

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/persona-presets/preset-global/wecom/notify-targets"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["targets"] == [
        {"username": "10001", "bound": True},
        {"username": "10002", "bound": False},
        {"username": "10003", "bound": True},
    ]


@pytest.mark.asyncio
async def test_get_notify_targets_returns_empty_list_without_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_Config("preset-global", "bot-1", []))
    binding = _Binding()
    _setup(monkeypatch, storage, binding)

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/persona-presets/preset-global/wecom/notify-targets"
        )

    assert response.status_code == 200
    assert response.json()["targets"] == []


@pytest.mark.asyncio
async def test_put_notify_targets_replaces_full_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(_Config("preset-global", "bot-1", ["10001", "10002"]))
    binding = _Binding(bound={"10002", "10003"})
    _setup(monkeypatch, storage, binding)

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/persona-presets/preset-global/wecom/notify-targets",
            json={"targets": ["10002", "10003"]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["targets"] == [
        {"username": "10002", "bound": True},
        {"username": "10003", "bound": True},
    ]
    assert storage.set_calls == [
        {
            "preset_id": "preset-global",
            "aibotid": "bot-1",
            "secret": None,
            "feedback_notify_targets": ["10002", "10003"],
        }
    ]


@pytest.mark.asyncio
async def test_get_notify_targets_returns_404_without_wecom_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(None)
    binding = _Binding()
    _setup(monkeypatch, storage, binding)

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/persona-presets/preset-global/wecom/notify-targets"
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "wecom_config_not_found"


@pytest.mark.asyncio
async def test_put_notify_targets_returns_404_without_wecom_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _Storage(None)
    binding = _Binding()
    _setup(monkeypatch, storage, binding)

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/persona-presets/preset-global/wecom/notify-targets",
            json={"targets": ["10001"]},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "wecom_config_not_found"
