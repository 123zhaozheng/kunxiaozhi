"""analytics 日期参数统一测试（S1）。

锁住三件事：
1. 所有端点的 start/end 均为 ``YYYY-MM-DD``，展开为
   ``[start 00:00+08:00, end+1d 00:00+08:00)`` 的半开区间；
2. 非法格式（``abc``、``2026-13-01``）或 ``end < start`` 一律 400；
3. 展开后的边界传给下游的是 timezone-aware datetime（UTC+8）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.infra.analytics.date_range import CST
from src.infra.analytics.usage_query import UsageFilters
from src.kernel.schemas.analytics import UsageSummaryResponse
from src.kernel.schemas.user import TokenPayload


def _admin_user() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


class _FakeManager:
    """记录 build_usage_filters 收到的展开后区间，供边界断言。"""

    def __init__(self) -> None:
        self.filter_calls: list[dict[str, Any]] = []

    async def build_usage_filters(
        self,
        start,
        end,
        *,
        persona_preset_id=None,
        agent_id=None,
        role_id=None,
    ) -> UsageFilters:
        self.filter_calls.append({"start": start, "end": end})
        return UsageFilters(start=start, end=end)

    async def get_usage_summary(self, filters: UsageFilters) -> UsageSummaryResponse:
        return UsageSummaryResponse()


def _app(fake: _FakeManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: fake
    return app


@pytest.mark.asyncio
async def test_date_range_expands_to_half_open_utc8_interval() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/summary",
            params={"start": "2026-08-22", "end": "2026-08-28"},
        )

    assert response.status_code == 200
    call = fake.filter_calls[0]
    start_dt = call["start"]
    end_dt = call["end"]

    # 时区感知且落在 UTC+8
    assert start_dt.tzinfo is not None
    assert start_dt.utcoffset() == timedelta(hours=8)
    # [2026-08-22 00:00+08:00, 2026-08-29 00:00+08:00)
    assert start_dt == datetime(2026, 8, 22, 0, 0, 0, tzinfo=CST)
    assert end_dt == datetime(2026, 8, 29, 0, 0, 0, tzinfo=CST)
    # 半开：end 是排他上界，恰好比最后一天多一天
    assert end_dt == start_dt + timedelta(days=7)


@pytest.mark.asyncio
async def test_single_day_range_is_one_full_day() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/summary",
            params={"start": "2026-08-22", "end": "2026-08-22"},
        )

    assert response.status_code == 200
    call = fake.filter_calls[0]
    assert call["start"] == datetime(2026, 8, 22, 0, 0, 0, tzinfo=CST)
    assert call["end"] == datetime(2026, 8, 23, 0, 0, 0, tzinfo=CST)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start,end",
    [
        ("abc", "2026-08-28"),
        ("2026-08-22", "abc"),
        ("2026-13-01", "2026-13-05"),  # 非法月份
        ("2026-02-30", "2026-03-01"),  # 非法日期
        ("2026-08-28", "2026-08-22"),  # end < start
        ("2026/08/22", "2026-08-28"),  # 分隔符错误
        ("2026-8-1", "2026-08-28"),  # 非零填充
        ("9999-12-31", "9999-12-31"),  # end + 1 day 溢出
    ],
)
async def test_invalid_dates_rejected_with_400(start: str, end: str) -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/summary", params={"start": start, "end": end}
        )
    assert response.status_code == 400, f"start={start} end={end}: {response.text}"
    # 非法参数不应触达下游
    assert fake.filter_calls == []


@pytest.mark.asyncio
async def test_query_range_over_366_days_rejected_before_manager() -> None:
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/summary",
            params={"start": "2026-01-01", "end": "2027-01-02"},
        )

    assert response.status_code == 400
    assert "366" in response.json()["detail"]
    assert fake.filter_calls == []


@pytest.mark.asyncio
async def test_missing_date_params_rejected_with_422() -> None:
    """缺少必填 start/end 属于 FastAPI 参数缺失，返回 422（非业务 400）。"""
    fake = _FakeManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/analytics/usage/summary")
    assert response.status_code == 422
