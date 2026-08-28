"""使用情况报表路由测试：参数透传、CSV 契约、权限。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.infra.analytics.usage_query import UsageFilters
from src.kernel.schemas.analytics import (
    UsageByPersonaItem,
    UsageByUserItem,
    UsageByUserResponse,
    UsageSummaryResponse,
    UsageTrendPoint,
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
    """Records the resolved filters so每个端点的参数透传都可断言。"""

    def __init__(self) -> None:
        self.filter_calls: list[dict[str, Any]] = []
        self.by_user_calls: list[dict[str, Any]] = []

    async def build_usage_filters(
        self,
        start,
        end,
        *,
        persona_preset_id=None,
        agent_id=None,
        role_id=None,
    ) -> UsageFilters:
        self.filter_calls.append(
            {
                "start": start,
                "end": end,
                "persona_preset_id": persona_preset_id,
                "agent_id": agent_id,
                "role_id": role_id,
            }
        )
        # role_id 在真实实现里会解析为 role_user_ids；此处只需保留可断言的痕迹
        return UsageFilters(
            start=start,
            end=end,
            persona_preset_id=persona_preset_id,
            agent_id=agent_id,
            role_user_ids=["u1"] if role_id else None,
        )

    async def get_usage_summary(self, filters: UsageFilters) -> UsageSummaryResponse:
        self.summary_filters = filters
        return UsageSummaryResponse(
            active_users=12,
            new_sessions=34,
            active_sessions=40,
            user_messages=567,
            total_tokens=89_012,
        )

    async def get_usage_trend(self, filters: UsageFilters) -> list[UsageTrendPoint]:
        self.trend_filters = filters
        return [
            UsageTrendPoint(
                date="2026-07-02",
                new_sessions=3,
                active_sessions=4,
                user_messages=21,
                total_tokens=4096,
            )
        ]

    async def get_usage_by_persona(
        self, filters: UsageFilters
    ) -> list[UsageByPersonaItem]:
        self.by_persona_filters = filters
        return [
            UsageByPersonaItem(
                persona_preset_id="p1",
                persona_preset_name="友好助手",
                active_users=5,
                active_sessions=9,
                user_messages=120,
                total_tokens=20_480,
            )
        ]

    async def list_usage_by_user(
        self, filters: UsageFilters, skip: int = 0, limit: int = 20
    ) -> UsageByUserResponse:
        self.by_user_calls.append({"filters": filters, "skip": skip, "limit": limit})
        return UsageByUserResponse(
            total=1,
            skip=skip,
            limit=limit,
            has_more=False,
            items=[
                UsageByUserItem(
                    user_id="u1",
                    username="emp001",
                    display_name="张三",
                    roles=["role-a", "role-b"],
                    persona_preset_id="p1",
                    persona_preset_name="友好助手",
                    new_sessions=18,
                    active_sessions=21,
                    user_messages=92,
                    total_tokens=45_120,
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


_RANGE = {"start": "2026-07-01T00:00:00Z", "end": "2026-07-17T00:00:00Z"}
_FILTERS = {"persona_preset_id": "p1", "agent_id": "fast", "role_id": "role-a"}


@pytest.mark.asyncio
async def test_usage_summary_returns_five_metrics_and_passes_filters() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/summary", params={**_RANGE, **_FILTERS}
        )

    assert response.status_code == 200
    assert response.json() == {
        "active_users": 12,
        "using_users": 0,
        "new_sessions": 34,
        "active_sessions": 40,
        "user_messages": 567,
        "total_tokens": 89_012,
    }
    call = fake.filter_calls[0]
    assert call["persona_preset_id"] == "p1"
    assert call["agent_id"] == "fast"
    assert call["role_id"] == "role-a"
    assert fake.summary_filters.role_user_ids == ["u1"]


@pytest.mark.asyncio
async def test_usage_trend_returns_four_series_per_day() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/trend", params={**_RANGE, "persona_preset_id": "p1"}
        )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["date"] == "2026-07-02"
    assert items[0]["user_messages"] == 21
    assert items[0]["new_sessions"] == 3
    assert fake.trend_filters.persona_preset_id == "p1"


@pytest.mark.asyncio
async def test_usage_by_persona_passes_agent_filter() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/by-persona", params={**_RANGE, "agent_id": "team"}
        )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["persona_preset_name"] == "友好助手"
    assert fake.by_persona_filters.agent_id == "team"


@pytest.mark.asyncio
async def test_usage_by_user_paginates() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/by-user",
            params={**_RANGE, "skip": 20, "limit": 50},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skip"] == 20
    assert payload["limit"] == 50
    assert payload["items"][0]["username"] == "emp001"
    call = fake.by_user_calls[0]
    assert call["skip"] == 20
    assert call["limit"] == 50


@pytest.mark.asyncio
async def test_usage_by_user_rejects_limit_above_cap() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/by-user", params={**_RANGE, "limit": 500}
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_usage_export_csv_bom_headers_and_row_cap() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/export.csv", params={**_RANGE, **_FILTERS}
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "analytics-usage.csv" in response.headers.get("content-disposition", "")
    assert response.headers.get("x-export-row-cap") == "10000"

    body = response.content
    assert body.startswith(b"\xef\xbb\xbf")  # Excel 需要 BOM 才能正确显示中文
    text = body.decode("utf-8-sig")
    assert (
        "user_id,username,display_name,roles,persona_preset_id,persona_preset_name,"
        "new_sessions,active_sessions,user_messages,total_tokens,last_active_at" in text
    )
    assert "emp001" in text
    assert "张三" in text
    assert "友好助手" in text
    assert "role-a;role-b" in text

    call = fake.by_user_calls[0]
    assert call["skip"] == 0
    assert call["limit"] == 10_000
    assert call["filters"].persona_preset_id == "p1"
    assert call["filters"].agent_id == "fast"


@pytest.mark.asyncio
async def test_usage_endpoints_require_settings_manage() -> None:
    def _no_perms() -> TokenPayload:
        return TokenPayload(sub="u1", username="user", roles=["user"], permissions=[])

    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _no_perms
    app.dependency_overrides[get_analytics_manager] = lambda: _FakeManager()

    transport = ASGITransport(app=app)
    paths = [
        "/api/analytics/usage/summary",
        "/api/analytics/usage/trend",
        "/api/analytics/usage/by-persona",
        "/api/analytics/usage/by-user",
        "/api/analytics/usage/export.csv",
    ]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in paths:
            response = await client.get(path, params=_RANGE)
            assert response.status_code == 403, path


@pytest.mark.asyncio
async def test_usage_summary_rejects_invalid_time() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/summary",
            params={"start": "not-a-date", "end": "2026-07-17T00:00:00Z"},
        )
    assert response.status_code == 400
