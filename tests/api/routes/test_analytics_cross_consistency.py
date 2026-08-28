"""跨层一致性测试：同一份构造数据 + 同一筛选下，五个出口的数字必须互相对得上。

锁住五条等式（父任务 Acceptance Criteria「六张 KPI 卡、三个环图中心数字、
明细表合计、导出 CSV 在同一筛选下互相对得上」的后端投影）：

1. ``usage/summary.active_sessions`` == ``usage/by-persona`` 各项 ``active_sessions`` 之和
2. ``usage/summary.user_messages``  == ``usage/by-user`` 全量各行 ``user_messages`` 之和
3. ``usage/summary.total_tokens``   == ``tokens/by-model`` 各项之和
4. ``usage/by-user`` 的 total       == ``usage/summary.using_users``（按用户去重后）
5. ``usage/export.csv`` 数据行数     == ``usage/by-user`` 的 total（未超上限时）

构造数据覆盖多 persona（3）、多用户（3 人发消息 + 1 人仅登录）、多模型（3），
使"求和 == 合计"类断言不是空转。每个端点的响应由各自独立的聚合路径从同一份
数据算出（按 persona 分组 / 按用户×persona 分组 / 按模型分组 / 去重计数），
任一出口的口径偏离都会打破等式。

注意：``usage/by-user`` 的行粒度为「用户 × Persona」，故第 4 条等式成立的前提
是每个用户只归属一个 persona（本构造数据满足；若同一用户跨多 persona，
total 会大于去重人数，这是行粒度的固有语义）。``tokens/by-model`` 端点
不接受筛选参数，因此五条断言统一使用无筛选的同一日期区间。
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.infra.analytics.date_range import CST
from src.infra.analytics.usage_query import UsageFilters
from src.kernel.schemas.analytics import (
    ByLabelItem,
    UsageByPersonaItem,
    UsageByUserItem,
    UsageByUserResponse,
    UsageSummaryPrevious,
    UsageSummaryResponse,
)
from src.kernel.schemas.user import TokenPayload

_RANGE = {"start": "2026-07-01", "end": "2026-07-16"}


def _admin_user() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


# ── 构造数据：多 persona × 多用户 × 多模型 ─────────────────────────────

_PERSONA_NAMES = {
    "p-alpha": "甲号助手",
    "p-beta": "乙号翻译",
    "p-gamma": "丙号写作",
}

# (user_id, username, display_name, roles)；u4 仅登录、从未发消息
_USERS: dict[str, dict[str, Any]] = {
    "u1": {"username": "emp001", "display_name": "张三", "roles": ["role-a"]},
    "u2": {"username": "emp002", "display_name": "李四", "roles": ["role-a", "role-b"]},
    "u3": {"username": "emp003", "display_name": "王五", "roles": ["role-b"]},
    "u4": {"username": "emp004", "display_name": "赵六", "roles": ["role-b"]},
}
_LOGIN_USER_IDS = ["u1", "u2", "u3", "u4"]

# 每个用户只归属一个 persona（保证 by-user 行数 == 去重用户数）。
# token_events 跨 3 个模型分布，单个 trace 内可出现多条 / 多模型。
_TRACES: list[dict[str, Any]] = [
    {
        "user_id": "u1",
        "session_id": "s-a1",
        "persona_preset_id": "p-alpha",
        "agent_id": "fast",
        "user_messages": 4,
        "token_events": [("gpt-4o", 1200), ("gpt-4o", 300)],
        "started_at": datetime(2026, 7, 2, 9, 30, tzinfo=CST),
    },
    {
        "user_id": "u1",
        "session_id": "s-a2",
        "persona_preset_id": "p-alpha",
        "agent_id": "fast",
        "user_messages": 3,
        "token_events": [("deepseek-chat", 800)],
        "started_at": datetime(2026, 7, 3, 14, 0, tzinfo=CST),
    },
    {
        "user_id": "u2",
        "session_id": "s-b1",
        "persona_preset_id": "p-beta",
        "agent_id": "team",
        "user_messages": 6,
        "token_events": [("gpt-4o", 2000), ("qwen-max", 500)],
        "started_at": datetime(2026, 7, 4, 10, 15, tzinfo=CST),
    },
    {
        "user_id": "u3",
        "session_id": "s-g1",
        "persona_preset_id": "p-gamma",
        "agent_id": "search",
        "user_messages": 2,
        "token_events": [("deepseek-chat", 700)],
        "started_at": datetime(2026, 7, 5, 16, 45, tzinfo=CST),
    },
    {
        "user_id": "u3",
        "session_id": "s-g2",
        "persona_preset_id": "p-gamma",
        "agent_id": "search",
        "user_messages": 5,
        "token_events": [("qwen-max", 1500)],
        "started_at": datetime(2026, 7, 6, 11, 0, tzinfo=CST),
    },
]

# session_id → (user_id, persona_preset_id, created_at)。
# s-a2 创建于区间之前：新建会话数(4) ≠ 活跃会话数(5)，两个口径不互相掩盖。
_SESSIONS: dict[str, dict[str, Any]] = {
    "s-a1": {"user_id": "u1", "persona_preset_id": "p-alpha", "created_at": datetime(2026, 7, 2, 9, 0, tzinfo=CST)},
    "s-a2": {"user_id": "u1", "persona_preset_id": "p-alpha", "created_at": datetime(2026, 6, 20, 8, 0, tzinfo=CST)},
    "s-b1": {"user_id": "u2", "persona_preset_id": "p-beta", "created_at": datetime(2026, 7, 4, 10, 0, tzinfo=CST)},
    "s-g1": {"user_id": "u3", "persona_preset_id": "p-gamma", "created_at": datetime(2026, 7, 5, 16, 0, tzinfo=CST)},
    "s-g2": {"user_id": "u3", "persona_preset_id": "p-gamma", "created_at": datetime(2026, 7, 6, 10, 30, tzinfo=CST)},
}


# ── 同一份数据上的独立聚合路径（每个端点各算各的）─────────────────────


class _ConsistentManager:
    """所有端点共用同一份构造数据，但各自独立聚合。"""

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
        role_user_ids: Optional[list[str]] = None
        if role_id:
            role_user_ids = [
                uid for uid, meta in _USERS.items() if role_id in meta["roles"]
            ]
        return UsageFilters(
            start=start,
            end=end,
            persona_preset_id=persona_preset_id,
            agent_id=agent_id,
            role_user_ids=role_user_ids,
        )

    @staticmethod
    def _filtered_traces(filters: UsageFilters) -> list[dict[str, Any]]:
        traces = [
            t
            for t in _TRACES
            if filters.start <= t["started_at"] < filters.end
        ]
        if filters.persona_preset_id:
            traces = [t for t in traces if t["persona_preset_id"] == filters.persona_preset_id]
        if filters.agent_id:
            traces = [t for t in traces if t["agent_id"] == filters.agent_id]
        if filters.role_user_ids is not None:
            traces = [t for t in traces if t["user_id"] in filters.role_user_ids]
        return traces

    async def get_usage_summary(self, filters: UsageFilters) -> UsageSummaryResponse:
        traces = self._filtered_traces(filters)
        using_users = {t["user_id"] for t in traces if t["user_messages"] > 0}
        active_sessions = {t["session_id"] for t in traces if t["user_messages"] > 0}
        user_messages = sum(t["user_messages"] for t in traces)
        total_tokens = sum(tokens for t in traces for _, tokens in t["token_events"])
        new_sessions = sum(
            1
            for s in _SESSIONS.values()
            if filters.start <= s["created_at"] < filters.end
        )
        active_users = len(set(_LOGIN_USER_IDS) | using_users)
        # 与后端契约一致：带 persona/agent 筛选时登录口径无法归属，活跃=使用
        if filters.persona_preset_id or filters.agent_id:
            active_users = len(using_users)
        return UsageSummaryResponse(
            active_users=active_users,
            using_users=len(using_users),
            new_sessions=new_sessions,
            active_sessions=len(active_sessions),
            user_messages=user_messages,
            total_tokens=total_tokens,
            previous=UsageSummaryPrevious(),
        )

    async def get_usage_by_persona(
        self, filters: UsageFilters
    ) -> list[UsageByPersonaItem]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for trace in self._filtered_traces(filters):
            groups.setdefault(trace["persona_preset_id"], []).append(trace)
        items = [
            UsageByPersonaItem(
                persona_preset_id=preset_id,
                persona_preset_name=_PERSONA_NAMES.get(preset_id, ""),
                active_users=len({t["user_id"] for t in group if t["user_messages"] > 0}),
                active_sessions=len(
                    {t["session_id"] for t in group if t["user_messages"] > 0}
                ),
                user_messages=sum(t["user_messages"] for t in group),
                total_tokens=sum(
                    tokens for t in group for _, tokens in t["token_events"]
                ),
            )
            for preset_id, group in sorted(groups.items())
        ]
        return items

    async def list_usage_by_user(
        self, filters: UsageFilters, skip: int = 0, limit: int = 20
    ) -> UsageByUserResponse:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for trace in self._filtered_traces(filters):
            groups.setdefault((trace["user_id"], trace["persona_preset_id"]), []).append(trace)

        rows: list[dict[str, Any]] = []
        for (user_id, preset_id), group in groups.items():
            user_messages = sum(t["user_messages"] for t in group)
            if user_messages <= 0:
                continue  # 与后端一致：明细只列发过消息的「用户 × Persona」
            meta = _USERS[user_id]
            new_sessions = sum(
                1
                for s in _SESSIONS.values()
                if s["user_id"] == user_id
                and s["persona_preset_id"] == preset_id
                and filters.start <= s["created_at"] < filters.end
            )
            rows.append(
                {
                    "user_id": user_id,
                    "username": meta["username"],
                    "display_name": meta["display_name"],
                    "roles": meta["roles"],
                    "persona_preset_id": preset_id,
                    "persona_preset_name": _PERSONA_NAMES.get(preset_id, ""),
                    "new_sessions": new_sessions,
                    "active_sessions": len(
                        {t["session_id"] for t in group if t["user_messages"] > 0}
                    ),
                    "user_messages": user_messages,
                    "total_tokens": sum(
                        tokens for t in group for _, tokens in t["token_events"]
                    ),
                    "last_active_at": max(t["started_at"] for t in group),
                }
            )
        rows.sort(key=lambda r: (-r["user_messages"], r["user_id"]))

        total = len(rows)
        page = rows[skip : skip + limit]
        return UsageByUserResponse(
            total=total,
            skip=skip,
            limit=limit,
            has_more=(skip + len(page)) < total,
            items=[UsageByUserItem(**row) for row in page],
        )

    async def get_tokens_by_model(self, start, end) -> list[ByLabelItem]:
        """tokens/by-model 端点不带筛选参数：按区间内全部 token 事件分模型求和。"""
        by_model: dict[str, int] = {}
        for trace in _TRACES:
            if not (start <= trace["started_at"] < end):
                continue
            for model, tokens in trace["token_events"]:
                by_model[model] = by_model.get(model, 0) + tokens
        return [
            ByLabelItem(label=model, value=float(total))
            for model, total in sorted(by_model.items(), key=lambda kv: -kv[1])
        ]


def _app(fake: _ConsistentManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: fake
    return app


class _CrossViews:
    """五个出口在同一区间（同一筛选：无额外筛选）下的响应快照。"""

    def __init__(
        self,
        fake: _ConsistentManager,
        summary: dict[str, Any],
        by_persona: list[dict[str, Any]],
        by_user: dict[str, Any],
        tokens_by_model: list[dict[str, Any]],
        csv_data_rows: list[list[str]],
    ) -> None:
        self.fake = fake
        self.summary = summary
        self.by_persona = by_persona
        self.by_user = by_user
        self.tokens_by_model = tokens_by_model
        self.csv_data_rows = csv_data_rows


@pytest.fixture
async def cross_views() -> _CrossViews:
    fake = _ConsistentManager()
    transport = ASGITransport(app=_app(fake))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        summary = (await client.get("/api/analytics/usage/summary", params=_RANGE)).json()
        by_persona = (
            await client.get("/api/analytics/usage/by-persona", params=_RANGE)
        ).json()["items"]
        # limit 取路由上限 100，构造数据行数远小于它 → 全量行
        by_user = (
            await client.get(
                "/api/analytics/usage/by-user", params={**_RANGE, "skip": 0, "limit": 100}
            )
        ).json()
        tokens_by_model = (
            await client.get("/api/analytics/tokens/by-model", params=_RANGE)
        ).json()["items"]
        export = await client.get("/api/analytics/usage/export.csv", params=_RANGE)

    assert export.status_code == 200
    text = export.content.decode("utf-8-sig")
    parsed = list(csv.reader(io.StringIO(text)))
    csv_data_rows = [row for row in parsed[1:] if row]
    return _CrossViews(fake, summary, by_persona, by_user, tokens_by_model, csv_data_rows)


@pytest.mark.asyncio
async def test_constructed_data_covers_multi_persona_user_model(
    cross_views: _CrossViews,
) -> None:
    """前置守卫：构造数据必须真的覆盖多 persona / 多用户 / 多模型，否则断言空转。"""
    assert len(cross_views.by_persona) == 3
    assert cross_views.summary["using_users"] == 3
    assert cross_views.summary["active_users"] == 4  # 含仅登录用户
    assert len(cross_views.tokens_by_model) == 3
    assert cross_views.summary["user_messages"] > 0
    assert cross_views.summary["total_tokens"] > 0


@pytest.mark.asyncio
async def test_all_endpoints_received_the_same_range(cross_views: _CrossViews) -> None:
    """五个出口收到完全一致的展开后区间（同一筛选的最底层保证）。"""
    calls = cross_views.fake.filter_calls
    assert len(calls) == 4  # summary / by-persona / by-user / export.csv（by-model 不走 filters）
    assert all(call == calls[0] for call in calls)
    assert calls[0]["start"] == datetime(2026, 7, 1, tzinfo=CST)
    assert calls[0]["end"] == datetime(2026, 7, 17, tzinfo=CST)


@pytest.mark.asyncio
async def test_summary_active_sessions_equals_by_persona_sum(cross_views: _CrossViews) -> None:
    assert cross_views.summary["active_sessions"] == sum(
        item["active_sessions"] for item in cross_views.by_persona
    )


@pytest.mark.asyncio
async def test_summary_user_messages_equals_by_user_sum(cross_views: _CrossViews) -> None:
    assert cross_views.summary["user_messages"] == sum(
        row["user_messages"] for row in cross_views.by_user["items"]
    )


@pytest.mark.asyncio
async def test_summary_total_tokens_equals_tokens_by_model_sum(cross_views: _CrossViews) -> None:
    assert cross_views.summary["total_tokens"] == int(
        sum(item["value"] for item in cross_views.tokens_by_model)
    )


@pytest.mark.asyncio
async def test_by_user_total_equals_summary_using_users(cross_views: _CrossViews) -> None:
    assert cross_views.by_user["total"] == cross_views.summary["using_users"]


@pytest.mark.asyncio
async def test_export_csv_row_count_equals_by_user_total(cross_views: _CrossViews) -> None:
    assert cross_views.by_user["total"] < 10_000  # 未超导出上限时行数必须等于 total
    assert len(cross_views.csv_data_rows) == cross_views.by_user["total"]


# ── 集成层：真实 MongoDB 上的聚合口径一致性 ─────────────────────────────
# 上面的路由级断言基于同源构造数据；这一段把同一批等式放到真实聚合
# pipeline 上验证（usage_facts_stages + 各端点的 $group），覆盖
# 「两套取数代码是否算出同一个数」这一层。需要本地开发 MongoDB 容器，
# 连不上时跳过（CI 无 Mongo 时由上层单测守护口径）。

_TEST_DB = "lambchat_cross_consistency_test"


def _integration_ts(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=CST)


def _integration_events(
    messages: int, model: str | None, tokens: int
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"event_type": "user:message", "data": {"content": f"q{i}"}}
        for i in range(messages)
    ]
    if model:
        events.append(
            {
                "event_type": "token:usage",
                "data": {"total_tokens": tokens, "model_id": model},
            }
        )
    return events


def _integration_fixtures() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from bson import ObjectId

    u1, u2, u3 = ObjectId(), ObjectId(), ObjectId()

    def session(sid, user, persona, name, agent, created):
        return {
            "session_id": sid,
            "user_id": str(user),
            "agent_id": agent,
            "created_at": created,
            "metadata": {"persona_preset_id": persona, "persona_preset_name": name},
        }

    sessions = [
        session("s1", u1, "p1", "客服助手", "fast", _integration_ts(1, 10)),
        session("s2", u2, "p2", "数据分析师", "team", _integration_ts(1, 14)),
        session("s3", u3, "p1", "客服助手", "fast", _integration_ts(2, 9)),
        session("s4", u2, "p2", "数据分析师", "team", _integration_ts(2, 15)),
        # 有会话无消息：计入新建会话，不计入活跃会话
        session("s5", u3, "p1", "客服助手", "fast", _integration_ts(2, 18)),
    ]

    def trace(tid, sid, user, agent, started, messages, model, tokens):
        events = _integration_events(messages, model, tokens)
        return {
            "trace_id": tid,
            "session_id": sid,
            "user_id": str(user),
            "agent_id": agent,
            "started_at": started,
            "event_count": len(events),
            "events": events,
            "metadata": {},
        }

    traces = [
        trace("t1", "s1", u1, "fast", _integration_ts(1, 10, 5), 2, "m1", 100),
        trace("t2", "s2", u2, "team", _integration_ts(1, 14, 10), 3, "m2", 200),
        trace("t3", "s3", u3, "fast", _integration_ts(2, 9, 20), 1, "m1", 50),
        trace("t4", "s4", u2, "team", _integration_ts(2, 15), 4, "m2", 300),
        # 只有 token 事件没有用户消息：贡献 token，不贡献活跃会话
        trace("t5", "s1", u1, "fast", _integration_ts(2, 10, 30), 0, "m1", 80),
    ]

    users = [
        {"_id": u1, "username": "emp001", "display_name": "张三", "roles": ["r1"]},
        {"_id": u2, "username": "emp002", "display_name": "李四", "roles": ["r1"]},
        {"_id": u3, "username": "emp003", "display_name": "王五", "roles": ["r2"]},
    ]
    return traces, sessions, users


class _ScopedClient:
    """让 AnalyticsStorage 的集合属性全部落到测试库，不动真实业务库。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getitem__(self, _db_name: str) -> Any:
        return self._client[_TEST_DB]


@pytest.fixture
async def real_storage(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.errors import PyMongoError

    from src.infra.analytics import storage as storage_mod
    from src.infra.analytics.storage import AnalyticsStorage
    from src.kernel.config import settings

    # 独立客户端：lru_cache 单例绑定首个测试的事件循环，跨测试复用会报
    # "Event loop is closed"
    client = AsyncIOMotorClient(
        settings.MONGODB_URL, serverSelectionTimeoutMS=2000, tz_aware=True
    )
    try:
        await client.admin.command("ping")
    except PyMongoError:
        client.close()
        pytest.skip("本地 MongoDB 不可用，跳过真实聚合层一致性验证")

    db = client[_TEST_DB]
    await db.drop_collection(settings.MONGODB_TRACES_COLLECTION)
    await db.drop_collection(settings.MONGODB_SESSIONS_COLLECTION)
    await db.drop_collection("users")

    traces, sessions, users = _integration_fixtures()
    await db[settings.MONGODB_TRACES_COLLECTION].insert_many(traces)
    await db[settings.MONGODB_SESSIONS_COLLECTION].insert_many(sessions)
    await db["users"].insert_many(users)

    monkeypatch.setattr(storage_mod, "get_mongo_client", lambda: _ScopedClient(client))

    # 强制实时聚合路径：验证各端点共享的 pipeline 口径本身
    async def _no_snapshot(filters, storage=None):
        raise RuntimeError("snapshot disabled for cross-consistency test")

    monkeypatch.setattr(storage_mod, "read_or_freeze", _no_snapshot)

    yield AnalyticsStorage()

    await client.drop_database(_TEST_DB)
    client.close()


@pytest.mark.asyncio
async def test_real_pipeline_sums_match_summary(real_storage) -> None:
    """真实聚合下断言 1-3：汇总数字能从分组端点逐项加回来。"""
    from src.infra.analytics.date_range import resolve_range

    start, end = resolve_range("2026-08-01", "2026-08-04")
    filters = UsageFilters(start=start, end=end)

    summary = await real_storage.get_usage_summary(filters)
    by_persona = await real_storage.get_usage_by_persona(filters)
    by_user = await real_storage.list_usage_by_user(filters, skip=0, limit=100)
    by_model = await real_storage.get_tokens_by_model(start, end)

    # 钉住构造数据的期望值，防止测试自身口径漂移
    assert summary.user_messages == 10
    assert summary.total_tokens == 730
    assert summary.new_sessions == 5  # s1..s5 均创建于区间内
    assert summary.active_sessions == 4  # s5 无消息
    assert summary.using_users == 3

    assert sum(item.active_sessions for item in by_persona) == summary.active_sessions
    assert sum(item.user_messages for item in by_user.items) == summary.user_messages
    assert int(sum(item.value for item in by_model)) == summary.total_tokens


@pytest.mark.asyncio
async def test_real_pipeline_by_user_total_is_using_users(real_storage) -> None:
    """真实聚合下断言 4：明细 total（每人单 persona）== 使用用户数。"""
    from src.infra.analytics.date_range import resolve_range

    start, end = resolve_range("2026-08-01", "2026-08-04")
    filters = UsageFilters(start=start, end=end)

    summary = await real_storage.get_usage_summary(filters)
    by_user = await real_storage.list_usage_by_user(filters, skip=0, limit=100)

    distinct_users = {item.user_id for item in by_user.items}
    assert len(distinct_users) == summary.using_users
    assert by_user.total == summary.using_users
