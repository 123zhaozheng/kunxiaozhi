"""洞察端点 /usage/insights 测试（S3）。

锁住四件事：
1. ``peak`` 只统计有用户消息的 trace（无消息 → null），按 UTC+8 星期×小时分桶；
2. ``top_token_users`` 为 Top3 token 用户，补齐用户名/显示名；
3. ``fastest_growing_persona`` 用本期与上一等长周期的用户消息数环比；
4. ``new_users`` = 首次消息日落在本期的人数（复用 ActivityStorage）。
另含权限 403 与非法日期 400。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.infra.analytics.date_range import CST
from src.infra.analytics.usage_query import UsageFilters
from src.kernel.schemas.analytics import (
    UsageByPersonaItem,
    UsageInsightsFastestGrowingPersona,
    UsageInsightsPeak,
    UsageInsightsResponse,
    UsageInsightsTopTokenUser,
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
    def __init__(self, response: UsageInsightsResponse) -> None:
        self._response = response
        self.insight_filters: list[UsageFilters] = []

    async def build_usage_filters(
        self,
        start,
        end,
        *,
        persona_preset_id=None,
        agent_id=None,
        role_id=None,
    ) -> UsageFilters:
        return UsageFilters(
            start=start,
            end=end,
            persona_preset_id=persona_preset_id,
            agent_id=agent_id,
            role_user_ids=["u1"] if role_id else None,
        )

    async def get_usage_insights(self, filters: UsageFilters) -> UsageInsightsResponse:
        self.insight_filters.append(filters)
        return self._response


def _app(fake: _FakeManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: fake
    return app


_RANGE = {"start": "2026-08-22", "end": "2026-08-28"}


@pytest.mark.asyncio
async def test_insights_returns_four_fields() -> None:
    canned = UsageInsightsResponse(
        peak=UsageInsightsPeak(weekday=5, hour=13, user_messages=128),
        top_token_users=[
            UsageInsightsTopTokenUser(
                user_id="u1", username="emp001", display_name="张三", tokens=5000
            )
        ],
        fastest_growing_persona=UsageInsightsFastestGrowingPersona(
            persona_preset_id="p1",
            persona_preset_name="友好助手",
            current=120,
            previous=60,
            growth_pct=100.0,
        ),
        new_users=3,
    )
    fake = _FakeManager(canned)
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/analytics/usage/insights", params=_RANGE)

    assert response.status_code == 200
    assert response.json() == {
        "peak": {"weekday": 5, "hour": 13, "user_messages": 128},
        "top_token_users": [
            {"user_id": "u1", "username": "emp001", "display_name": "张三", "tokens": 5000}
        ],
        "fastest_growing_persona": {
            "persona_preset_id": "p1",
            "persona_preset_name": "友好助手",
            "current": 120,
            "previous": 60,
            "growth_pct": 100.0,
        },
        "new_users": 3,
    }
    assert len(fake.insight_filters) == 1


@pytest.mark.asyncio
async def test_insights_empty_values_do_not_error() -> None:
    canned = UsageInsightsResponse()  # peak=null / [] / null / 0
    fake = _FakeManager(canned)
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/analytics/usage/insights", params=_RANGE)

    assert response.status_code == 200
    assert response.json() == {
        "peak": None,
        "top_token_users": [],
        "fastest_growing_persona": None,
        "new_users": 0,
    }


@pytest.mark.asyncio
async def test_insights_requires_settings_manage() -> None:
    def _no_perms() -> TokenPayload:
        return TokenPayload(sub="u1", username="user", roles=["user"], permissions=[])

    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _no_perms
    app.dependency_overrides[get_analytics_manager] = lambda: _FakeManager(
        UsageInsightsResponse()
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/analytics/usage/insights", params=_RANGE)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_insights_rejects_invalid_dates() -> None:
    fake = _FakeManager(UsageInsightsResponse())
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/analytics/usage/insights",
            params={"start": "2026-08-28", "end": "2026-08-22"},
        )
    assert response.status_code == 400


# ── Storage 层：四个口径 ────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def to_list(self, length: int | None = None):
        if length is None:
            return list(self._docs)
        return list(self._docs)[:length]


class _FakeCollection:
    """记录每次 aggregate 的 pipeline，并按序返回预置结果。"""

    def __init__(self, results: list[list[dict[str, Any]]]):
        self.pipelines: list[list[dict[str, Any]]] = []
        self._results = list(results)

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        docs = self._results.pop(0) if self._results else []
        return _FakeCursor(docs)

    def find(self, *args, **kwargs):
        return _FakeCursor(self._results.pop(0) if self._results else [])


def _filters() -> UsageFilters:
    return UsageFilters(
        start=datetime(2026, 8, 22, tzinfo=CST),
        end=datetime(2026, 8, 29, tzinfo=CST),
    )


@pytest.mark.asyncio
async def test_peak_buckets_only_traces_with_user_messages() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()
    store._traces = _FakeCollection(
        [[{"_id": {"weekday": 5, "hour": 13}, "user_messages": 128}]]
    )

    peak = await store._insights_peak(_filters())

    assert peak is not None
    assert peak.weekday == 5
    assert peak.hour == 13
    assert peak.user_messages == 128

    # 口径锁：必须过滤 user_messages > 0（只有会话没有消息 → 无桶 → null），
    # 且分桶时区为 Asia/Shanghai（与其它指标同源）
    pipeline = store._traces.pipelines[0]
    assert {"$match": {"user_messages": {"$gt": 0}}} in pipeline
    group_id = next(s for s in pipeline if "$group" in s)["$group"]["_id"]
    assert group_id["hour"]["$hour"]["timezone"] == "Asia/Shanghai"
    day_of_week = group_id["weekday"]["$subtract"][0]
    assert day_of_week["$dayOfWeek"]["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_peak_is_null_without_messages() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()
    store._traces = _FakeCollection([[]])  # 无消息：聚合无结果

    assert await store._insights_peak(_filters()) is None


@pytest.mark.asyncio
async def test_top_token_users_joins_names_and_caps_at_three() -> None:
    from bson import ObjectId

    from src.infra.analytics.storage import AnalyticsStorage

    uid1 = "64b7f1a2c3d4e5f6a7b8c9d0"
    uid2 = "64b7f1a2c3d4e5f6a7b8c9d1"
    store = AnalyticsStorage()
    store._traces = _FakeCollection(
        [[{"_id": uid1, "tokens": 5000}, {"_id": uid2, "tokens": 3000}]]
    )
    store._users = _FakeCollection(
        [
            [
                {"_id": ObjectId(uid1), "username": "emp001", "display_name": "张三"},
                {"_id": ObjectId(uid2), "username": "emp002", "display_name": None},
            ]
        ]
    )

    items = await store._insights_top_token_users(_filters())

    assert [item.tokens for item in items] == [5000, 3000]
    assert items[0].user_id == uid1
    assert items[0].username == "emp001"
    assert items[0].display_name == "张三"
    assert items[1].username == "emp002"
    assert items[1].display_name is None
    # limit 3 落在 pipeline 里
    assert {"$limit": 3} in store._traces.pipelines[0]


@pytest.mark.asyncio
async def test_fastest_growing_persona_picks_max_growth() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()
    calls: list[UsageFilters] = []

    def _item(pid: str, name: str, messages: int) -> UsageByPersonaItem:
        return UsageByPersonaItem(
            persona_preset_id=pid,
            persona_preset_name=name,
            user_messages=messages,
        )

    async def _fake_by_persona(filters: UsageFilters):
        calls.append(filters)
        if len(calls) == 1:
            return [_item("p1", "友好助手", 120), _item("p2", "严谨顾问", 50)]
        return [_item("p1", "友好助手", 60), _item("p2", "严谨顾问", 40)]

    store.get_usage_by_persona = _fake_by_persona  # type: ignore[method-assign]

    result = await store._insights_fastest_growing_persona(_filters())

    assert result is not None
    assert result.persona_preset_id == "p1"  # (120-60)/60 = 100% > (50-40)/40 = 25%
    assert result.current == 120
    assert result.previous == 60
    assert result.growth_pct == 100.0
    # 上一周期为等长前移区间（2026-08-15..2026-08-21）
    assert calls[1].start == datetime(2026, 8, 15, tzinfo=CST)
    assert calls[1].end == datetime(2026, 8, 22, tzinfo=CST)


@pytest.mark.asyncio
async def test_fastest_growing_persona_new_persona_counts_as_growth() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()
    call_count = [0]

    async def _fake_by_persona(filters: UsageFilters):
        call_count[0] += 1
        if call_count[0] == 1:
            return [
                UsageByPersonaItem(persona_preset_id="p1", persona_preset_name="新角色", user_messages=10),
                UsageByPersonaItem(persona_preset_id="p2", persona_preset_name="老角色", user_messages=200),
            ]
        # 上一周期只有 p2
        return [UsageByPersonaItem(persona_preset_id="p2", persona_preset_name="老角色", user_messages=190)]

    store.get_usage_by_persona = _fake_by_persona  # type: ignore[method-assign]

    result = await store._insights_fastest_growing_persona(_filters())

    # p1 上期无数据 → 记 100% 增长；p2 仅 ~5.3%
    assert result is not None
    assert result.persona_preset_id == "p1"
    assert result.previous == 0
    assert result.growth_pct == 100.0


@pytest.mark.asyncio
async def test_fastest_growing_persona_null_without_messages() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()

    async def _fake_by_persona(filters: UsageFilters):
        return []

    store.get_usage_by_persona = _fake_by_persona  # type: ignore[method-assign]

    assert await store._insights_fastest_growing_persona(_filters()) is None


class _FakeActivityStorage:
    def __init__(self, distinct: list[str], first_dates: dict[str, str]):
        self._distinct = distinct
        self._first_dates = first_dates
        self.first_calls: list[list[str] | None] = []

    async def distinct_users(self, start: str, end: str, source: str | None = None):
        assert source == "message"
        return list(self._distinct)

    async def first_message_date(self, user_ids=None):
        self.first_calls.append(user_ids)
        return {
            uid: date
            for uid, date in self._first_dates.items()
            if not user_ids or uid in user_ids
        }


@pytest.mark.asyncio
async def test_new_users_counts_first_message_dates_in_period() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()
    store._activity_storage = _FakeActivityStorage(
        distinct=["u1", "u2", "u3"],
        first_dates={
            "u1": "2026-08-23",  # 首次消息在本期 → 计入
            "u2": "2026-07-01",  # 老用户 → 不计入
            "u3": "2026-08-28",  # 首次消息在本期 → 计入
        },
    )

    assert await store._insights_new_users(_filters()) == 2


@pytest.mark.asyncio
async def test_new_users_respects_role_filter() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()
    store._activity_storage = _FakeActivityStorage(
        distinct=["u1", "u2"],
        first_dates={"u1": "2026-08-23", "u2": "2026-08-23"},
    )

    filters = UsageFilters(
        start=datetime(2026, 8, 22, tzinfo=CST),
        end=datetime(2026, 8, 29, tzinfo=CST),
        role_user_ids=["u1"],
    )
    assert await store._insights_new_users(filters) == 1


@pytest.mark.asyncio
async def test_new_users_zero_for_empty_role_without_query() -> None:
    from src.infra.analytics.storage import AnalyticsStorage

    store = AnalyticsStorage()
    fake = _FakeActivityStorage(distinct=["u1"], first_dates={"u1": "2026-08-23"})
    store._activity_storage = fake

    filters = UsageFilters(
        start=datetime(2026, 8, 22, tzinfo=CST),
        end=datetime(2026, 8, 29, tzinfo=CST),
        role_user_ids=[],  # 角色下无用户 → 直接 0，不得退化为全量查询
    )
    assert await store._insights_new_users(filters) == 0
    assert fake.first_calls == []
