from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import settings as settings_route
from src.infra.agent.wecom.network_config import StoredWeComNetworkConfig
from src.kernel.schemas.user import TokenPayload
from src.kernel.schemas.wecom_network import (
    WeComNetworkConfig,
    WeComNetworkMode,
)


def _settings_admin() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


class _NetworkStorage:
    def __init__(self) -> None:
        self.current = StoredWeComNetworkConfig(
            config=WeComNetworkConfig(),
            revision="rev-old",
            updated_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
            updated_by="admin-0",
        )
        self.promoted: list[str] = []
        self.rolled_back: list[str] = []

    async def get_current(self) -> StoredWeComNetworkConfig:
        return self.current

    async def save_candidate(
        self,
        config: WeComNetworkConfig,
        *,
        updated_by: str,
        expected_revision: str | None = None,
    ) -> tuple[StoredWeComNetworkConfig, StoredWeComNetworkConfig]:
        if expected_revision and expected_revision != self.current.revision:
            raise ValueError("wecom_network_config_revision_conflict")
        old = self.current
        self.current = StoredWeComNetworkConfig(
            config=config,
            revision="rev-new",
            updated_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
            updated_by=updated_by,
        )
        return old, self.current

    async def promote(self, revision: str) -> bool:
        self.promoted.append(revision)
        return True

    async def rollback(
        self,
        candidate_revision: str,
        *,
        updated_by: str,
    ) -> StoredWeComNetworkConfig:
        self.rolled_back.append(candidate_revision)
        self.current = StoredWeComNetworkConfig(
            config=WeComNetworkConfig(),
            revision="rev-rollback",
            updated_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
            updated_by=updated_by,
        )
        return self.current


class _PersonaStorage:
    def __init__(self, bot_count: int) -> None:
        self.bot_count = bot_count

    async def get_all_persona_wecom_configs_raw(self) -> list[dict[str, Any]]:
        return [
            {"preset_id": f"p-{index}", "aibotid": f"bot-{index}", "secret": "secret"}
            for index in range(self.bot_count)
        ]


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_route.router, prefix="/api/settings")
    app.dependency_overrides[api_deps.get_current_user_required] = _settings_admin
    return app


@pytest.mark.asyncio
async def test_get_network_config_masks_proxy_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _NetworkStorage()
    storage.current = StoredWeComNetworkConfig(
        config=WeComNetworkConfig(
            mode=WeComNetworkMode.FORWARD_PROXY,
            forward_proxy_url="http://proxy.example:8080",
            forward_proxy_password="secret",
        ),
        revision="rev-old",
    )
    monkeypatch.setattr(
        "src.infra.agent.wecom.network_config.get_wecom_network_config_storage",
        lambda: storage,
    )

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/settings/wecom-network")

    assert response.status_code == 200
    assert response.json()["has_forward_proxy_password"] is True
    assert "forward_proxy_password" not in response.json()


@pytest.mark.asyncio
async def test_partial_success_keeps_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _NetworkStorage()
    monkeypatch.setattr(
        "src.infra.agent.wecom.network_config.get_wecom_network_config_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "src.infra.agent.config_storage.get_agent_config_storage",
        lambda: _PersonaStorage(2),
    )

    async def _reload(revision: str, **_: Any) -> list[dict[str, Any]]:
        assert revision == "rev-new"
        return [
            {
                "preset_id": "p-0",
                "aibotid": "bot-0",
                "state": "connected",
            },
            {
                "preset_id": "p-1",
                "aibotid": "bot-1",
                "state": "failed",
                "reason_code": "auth_failed",
            },
        ]

    monkeypatch.setattr(
        "src.infra.agent.wecom.control.request_wecom_network_reload",
        _reload,
    )

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/settings/wecom-network",
            json={
                "mode": "forward_proxy",
                "forward_proxy_url": "http://proxy.example:8080",
                "expected_revision": "rev-old",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "partial_failure"
    assert storage.promoted == ["rev-new"]
    assert storage.rolled_back == []


@pytest.mark.asyncio
async def test_total_failure_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _NetworkStorage()
    monkeypatch.setattr(
        "src.infra.agent.wecom.network_config.get_wecom_network_config_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "src.infra.agent.config_storage.get_agent_config_storage",
        lambda: _PersonaStorage(1),
    )
    revisions: list[str] = []

    async def _reload(revision: str, **_: Any) -> list[dict[str, Any]]:
        revisions.append(revision)
        if revision == "rev-new":
            return [
                {
                    "preset_id": "p-0",
                    "aibotid": "bot-0",
                    "state": "failed",
                    "reason_code": "auth_failed",
                }
            ]
        return []

    monkeypatch.setattr(
        "src.infra.agent.wecom.control.request_wecom_network_reload",
        _reload,
    )

    async with AsyncClient(
        transport=ASGITransport(app=_app()),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/settings/wecom-network",
            json={
                "mode": "reverse_gateway",
                "websocket_url": "wss://dmz.example/wecom/ws",
                "media_gateway_url": "https://dmz.example/wecom/media",
                "expected_revision": "rev-old",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "rolled_back"
    assert storage.rolled_back == ["rev-new"]
    assert revisions == ["rev-new", "rev-rollback"]
