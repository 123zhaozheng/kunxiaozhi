from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes import settings as settings_route
from src.infra.sandbox.node_storage import StoredOpenSandboxNodes
from src.kernel.schemas.opensandbox import OpenSandboxMode
from src.kernel.schemas.user import TokenPayload


def _user(*, admin: bool) -> TokenPayload:
    return TokenPayload(
        sub="admin-1" if admin else "user-1",
        username="admin" if admin else "user",
        roles=["admin"] if admin else [],
        permissions=["settings:manage"] if admin else [],
    )


def _app(user: TokenPayload | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(settings_route.router, prefix="/api/settings")
    if user is not None:
        app.dependency_overrides[api_deps.get_current_user_required] = lambda: user
    return app


class FakeNodeStorage:
    def __init__(self) -> None:
        self.current = StoredOpenSandboxNodes(
            OpenSandboxMode.MULTI_NODE,
            [
                {
                    "id": "node-a",
                    "domain": "https://node-a.example",
                    "api_key": "top-secret",
                    "image": "ubuntu",
                    "timeout": 3600,
                    "work_dir": "/root",
                    "use_server_proxy": True,
                    "max_sandboxes": 2,
                    "enabled": True,
                    "priority": 100,
                    "health_state": "healthy",
                    "last_health_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
                    "used_sandboxes": 1,
                }
            ],
            "rev-1",
        )
        self.draining: list[tuple[str, bool]] = []

    async def get_current(self) -> StoredOpenSandboxNodes:
        return self.current

    async def save(self, update, *, updated_by: str) -> StoredOpenSandboxNodes:
        if update.expected_revision != self.current.revision:
            raise ValueError("opensandbox_nodes_revision_conflict")
        assert updated_by == "admin-1"
        nodes = [
            {
                **node.model_dump(exclude={"api_key", "clear_api_key"}),
                "api_key": node.api_key or "top-secret",
            }
            for node in update.nodes
        ]
        self.current = StoredOpenSandboxNodes(update.mode, nodes, "rev-2")
        return self.current

    async def set_draining(self, node_id: str, draining: bool) -> bool:
        self.draining.append((node_id, draining))
        return node_id == "node-a"


@pytest.mark.asyncio
async def test_node_settings_require_settings_manage_permission() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app(_user(admin=False))),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/settings/opensandbox-nodes")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_nodes_redacts_secret_and_transports_health_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeNodeStorage()
    monkeypatch.setattr(
        "src.infra.sandbox.node_storage.get_opensandbox_node_storage",
        lambda: storage,
    )

    async with AsyncClient(
        transport=ASGITransport(app=_app(_user(admin=True))),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/settings/opensandbox-nodes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"][0]["has_api_key"] is True
    assert payload["nodes"][0]["last_health_at"].startswith("2026-08-19")
    assert "top-secret" not in response.text
    assert "api_key" not in payload["nodes"][0]


@pytest.mark.asyncio
async def test_put_nodes_enforces_revision_and_publishes_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeNodeStorage()
    resets: list[str] = []
    publications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.infra.sandbox.node_storage.get_opensandbox_node_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "src.infra.sandbox.session_manager.reset_session_sandbox_manager",
        lambda: resets.append("reset"),
    )

    async def publish(key: str, revision: str) -> None:
        publications.append((key, revision))

    monkeypatch.setattr(settings_route.SettingsService, "_publish_change", publish)
    payload: dict[str, Any] = {
        "mode": "multi_node",
        "expected_revision": "stale",
        "nodes": [
            {
                "id": "node-a",
                "domain": "https://node-a.example",
                "api_key": "replacement",
                "max_sandboxes": 3,
            }
        ],
    }

    async with AsyncClient(
        transport=ASGITransport(app=_app(_user(admin=True))),
        base_url="http://testserver",
    ) as client:
        conflict = await client.put("/api/settings/opensandbox-nodes", json=payload)
        payload["expected_revision"] = "rev-1"
        saved = await client.put("/api/settings/opensandbox-nodes", json=payload)

    assert conflict.status_code == 409
    assert saved.status_code == 200
    assert saved.json()["revision"] == "rev-2"
    assert "replacement" not in saved.text
    assert resets == ["reset"]
    assert publications == [("OPENSANDBOX_NODES", "rev-2")]


@pytest.mark.asyncio
async def test_probe_and_drain_return_stable_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeNodeStorage()
    health_updates: list[tuple[dict, dict, bool]] = []

    class FakeAdapter:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["api_key"] == "top-secret"

        def probe(self) -> float:
            return 12.345

    class FakeCollection:
        async def update_one(self, query: dict, update: dict, *, upsert: bool) -> None:
            health_updates.append((query, update, upsert))

    class FakeCapacity:
        def _collection(self) -> FakeCollection:
            return FakeCollection()

    monkeypatch.setattr(
        "src.infra.sandbox.node_storage.get_opensandbox_node_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "src.infra.sandbox.session_manager.OpenSandboxSandboxAdapter",
        FakeAdapter,
    )
    monkeypatch.setattr(
        "src.infra.sandbox.capacity_storage.OpenSandboxCapacityStorage",
        FakeCapacity,
    )

    async with AsyncClient(
        transport=ASGITransport(app=_app(_user(admin=True))),
        base_url="http://testserver",
    ) as client:
        probe = await client.post(
            "/api/settings/opensandbox-nodes/node-a/probe"
        )
        drain = await client.post(
            "/api/settings/opensandbox-nodes/node-a/drain?draining=true"
        )

    assert probe.status_code == 200
    assert probe.json() == {
        "node_id": "node-a",
        "health_state": "healthy",
        "latency_ms": 12.35,
        "detail": None,
    }
    assert health_updates[0][1]["$set"]["health_state"] == "healthy"
    assert drain.status_code == 200
    assert storage.draining == [("node-a", True)]
