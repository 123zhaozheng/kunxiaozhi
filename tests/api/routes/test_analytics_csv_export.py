"""Analytics CSV export route tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.kernel.schemas.analytics import (
    ActiveUserListItem,
    ActiveUserListResponse,
    SessionListItem,
    SessionListResponse,
)
from src.kernel.schemas.user import TokenPayload


def _admin_user() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


class _FakeManager:
    def __init__(self) -> None:
        self.session_calls: list[dict[str, Any]] = []
        self.user_calls: list[dict[str, Any]] = []

    async def list_sessions(self, start, end, **kwargs) -> SessionListResponse:
        self.session_calls.append({"start": start, "end": end, **kwargs})
        return SessionListResponse(
            total=1,
            skip=0,
            limit=kwargs.get("limit", 20),
            has_more=False,
            items=[
                SessionListItem(
                    id="s1",
                    name="会话甲",
                    user_id="u1",
                    username="emp001",
                    agent_id="fast",
                    created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
                    is_active=True,
                    task_status="idle",
                    unread_count=0,
                    persona_preset_id="p1",
                    persona_preset_name="友好助手",
                )
            ],
        )

    async def list_active_users(self, start, end, **kwargs) -> ActiveUserListResponse:
        self.user_calls.append({"start": start, "end": end, **kwargs})
        return ActiveUserListResponse(
            total=1,
            skip=0,
            limit=kwargs.get("limit", 20),
            has_more=False,
            items=[
                ActiveUserListItem(
                    user_id="u1",
                    username="alice",
                    display_name="Alice",
                    roles=["role-a", "role-b"],
                    session_count=3,
                    last_active_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
                )
            ],
        )


def _app(fake: _FakeManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: fake
    return app


@pytest.mark.asyncio
async def test_export_sessions_csv_uses_same_filters_and_bom() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/sessions/export.csv",
            params={
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-17T00:00:00Z",
                "agent_id": "fast",
                "persona_preset_id": "p1",
                "role_id": "role-a",
                "sort": "frequency",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "analytics-sessions.csv" in response.headers.get("content-disposition", "")
    assert response.headers.get("x-export-row-cap") == "10000"

    body = response.content
    assert body.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = body.decode("utf-8-sig")
    assert "id,name,username,user_id,agent_id" in text
    assert "s1" in text
    assert "emp001" in text
    assert "友好助手" in text
    assert "fast" in text

    call = fake.session_calls[0]
    assert call["agent_id"] == "fast"
    assert call["persona_preset_id"] == "p1"
    assert call["role_id"] == "role-a"
    assert call["sort"] == "frequency"
    assert call["skip"] == 0
    assert call["limit"] == 10_000


@pytest.mark.asyncio
async def test_export_users_csv_filters_and_roles_joined() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/users/export.csv",
            params={
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-17T00:00:00Z",
                "agent_id": "search",
                "sort": "recent",
            },
        )

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "username,display_name,user_id,roles,session_count" in text
    assert "role-a;role-b" in text
    assert "alice" in text

    call = fake.user_calls[0]
    assert call["agent_id"] == "search"
    assert call["sort"] == "recent"
    assert call["limit"] == 10_000
    assert call["skip"] == 0


@pytest.mark.asyncio
async def test_export_sessions_csv_rejects_invalid_sort() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/sessions/export.csv",
            params={
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-17T00:00:00Z",
                "sort": "invalid",
            },
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_export_sessions_csv_requires_settings_manage() -> None:
    def _no_perms() -> TokenPayload:
        return TokenPayload(
            sub="u1",
            username="user",
            roles=["user"],
            permissions=[],
        )

    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _no_perms
    app.dependency_overrides[get_analytics_manager] = lambda: _FakeManager()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/sessions/export.csv",
            params={
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-07-17T00:00:00Z",
            },
        )
    assert response.status_code == 403
