from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.kernel.schemas.analytics import PresetAnalyticsResponse
from src.kernel.schemas.user import TokenPayload


def _admin_user() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


class _FakeManager:
    """Fake AnalyticsManager returning canned preset metrics."""

    def __init__(self, response: PresetAnalyticsResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def get_preset_metrics(self, preset_id: str, start, end) -> PresetAnalyticsResponse:
        self.calls.append({"preset_id": preset_id, "start": start, "end": end})
        return self._response


@pytest.mark.asyncio
async def test_get_preset_metrics_route_returns_response() -> None:
    canned = PresetAnalyticsResponse(
        total_messages=42,
        total_sessions=7,
        active_users=5,
        total_tokens=1234,
        up_vote_rate=80.0,
        down_reasons=[],
    )
    fake = _FakeManager(canned)

    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: fake

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/presets/preset-abc",
            params={"start": "2026-06-01T00:00:00Z", "end": "2026-06-18T00:00:00Z"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_messages"] == 42
    assert body["total_sessions"] == 7
    assert body["active_users"] == 5
    assert body["total_tokens"] == 1234
    assert body["up_vote_rate"] == 80.0
    assert body["down_reasons"] == []
    assert fake.calls[0]["preset_id"] == "preset-abc"


@pytest.mark.asyncio
async def test_get_preset_metrics_route_swaps_inverted_range() -> None:
    canned = PresetAnalyticsResponse()
    fake = _FakeManager(canned)

    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: fake

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/presets/preset-abc",
            params={"start": "2026-06-18T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
        )

    assert response.status_code == 200
    call = fake.calls[0]
    assert call["start"] <= call["end"]


@pytest.mark.asyncio
async def test_get_preset_metrics_route_rejects_missing_permissions() -> None:
    def _no_perms_user() -> TokenPayload:
        return TokenPayload(
            sub="user-1",
            username="user",
            roles=["user"],
            permissions=[],
        )

    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _no_perms_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/presets/preset-abc",
            params={
                "start": datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat(),
                "end": datetime(2026, 6, 18, tzinfo=timezone.utc).isoformat(),
            },
        )

    assert response.status_code == 403
