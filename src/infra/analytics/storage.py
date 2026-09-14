"""
Analytics Storage - 聚合统计查询

使用 MongoDB aggregation pipeline 汇总用户、会话、token、反馈相关指标。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from src.infra.analytics.date_range import (
    _BUCKET_TZ,
    previous_range,
    range_to_date_strings,
    resolve_range,
)
from src.infra.analytics.snapshot import SNAPSHOT_COLLECTION_NAME, read_or_freeze
from src.infra.analytics.usage_query import (
    UsageFilters,
    new_sessions_match,
    usage_facts_stages,
)
from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.kernel.config import settings
from src.kernel.schemas.analytics import (
    ActiveUserListItem,
    ActiveUserListResponse,
    ByLabelItem,
    ByPresetFeedbackItem,
    ByPresetFeedbackResponse,
    FeedbackListItem,
    FeedbackListResponse,
    FeedbackSummaryResponse,
    OverviewResponse,
    PresetAnalyticsResponse,
    RunListItem,
    RunListResponse,
    SessionListItem,
    SessionListResponse,
    SessionsTrendResponse,
    TrendDataPoint,
    UsageByPersonaItem,
    UsageByUserItem,
    UsageByUserResponse,
    UsageInsightsFastestGrowingPersona,
    UsageInsightsPeak,
    UsageInsightsResponse,
    UsageInsightsTopTokenUser,
    UsageSummaryPrevious,
    UsageSummaryResponse,
    UsageTrendPoint,
)

logger = get_logger(__name__)

_TOKEN_USAGE_EVENT = "token:usage"
_TOP_PRESET_LIMIT = 10


def _model_label(data: dict[str, Any]) -> str:
    """Resolve one token event to the label used by model analytics."""
    model_id = data.get("model_id")
    if model_id not in (None, ""):
        return str(model_id)
    model = data.get("model")
    if model not in (None, ""):
        return str(model)
    return "unknown"


def _model_label_expr(data_ref: str = "$events.data") -> dict[str, Any]:
    """Mongo expression matching :func:`_model_label`, including legacy data."""
    model_id = {"$ifNull": [f"{data_ref}.model_id", ""]}
    model = {"$ifNull": [f"{data_ref}.model", ""]}
    return {
        "$let": {
            "vars": {"model_id": model_id, "model": model},
            "in": {
                "$cond": [
                    {"$ne": ["$$model_id", ""]},
                    "$$model_id",
                    {"$cond": [{"$ne": ["$$model", ""]}, "$$model", "unknown"]},
                ]
            },
        }
    }


def _model_event_match_expr(model: str) -> dict[str, Any]:
    """Return a query expression using the same model ownership rule."""
    return {
        "$expr": {
            "$gt": [
                {
                    "$size": {
                        "$filter": {
                            "input": {"$ifNull": ["$events", []]},
                            "as": "event",
                            "cond": {
                                "$and": [
                                    {"$eq": ["$$event.event_type", _TOKEN_USAGE_EVENT]},
                                    {"$eq": [_model_label_expr("$$event.data"), model]},
                                ]
                            },
                        }
                    }
                },
                0,
            ]
        }
    }


def _ensure_datetime(value: datetime) -> datetime:
    """确保 datetime 携带 UTC 时区信息，供 MongoDB 比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _compute_up_vote_rate(up_count: int, down_count: int) -> float:
    """点赞率 = up / (up + down)；无反馈时返回 0。

    Feedback.rating 仅允许 ``up`` / ``down``，禁止使用历史错误值 ``like``。
    返回 0-100 的百分比，保留 1 位小数。
    """
    total = int(up_count) + int(down_count)
    if total <= 0:
        return 0.0
    return round((int(up_count) / total) * 100, 1)


def _user_object_ids(user_ids: list[str]) -> list[Any]:
    """Convert string user ids to ObjectId; skip invalid values.

    User documents are keyed by Mongo ``_id`` (ObjectId). API/session layers
    expose ``str(_id)`` as ``user_id`` / ``id``.
    """
    from bson import ObjectId
    from bson.errors import InvalidId

    object_ids: list[Any] = []
    for uid in user_ids:
        if not uid:
            continue
        try:
            object_ids.append(ObjectId(str(uid)))
        except (InvalidId, TypeError, ValueError):
            continue
    return object_ids


class AnalyticsStorage:
    """Analytics MongoDB 聚合查询"""

    def __init__(self):
        self._traces = None
        self._sessions = None
        self._users = None
        self._feedback = None
        self._persona_presets = None
        self._trace_storage = None
        self._snapshot = None
        self._activity = None
        self._activity_storage = None

    # ── Collection accessors ────────────────────────────────────────

    @property
    def traces(self):
        if self._traces is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._traces = db[settings.MONGODB_TRACES_COLLECTION]
        return self._traces

    @property
    def sessions(self):
        if self._sessions is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._sessions = db[settings.MONGODB_SESSIONS_COLLECTION]
        return self._sessions

    @property
    def users(self):
        if self._users is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._users = db["users"]
        return self._users

    @property
    def feedback(self):
        if self._feedback is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._feedback = db["feedback"]
        return self._feedback

    @property
    def persona_presets(self):
        if self._persona_presets is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._persona_presets = db["persona_presets"]
        return self._persona_presets

    @property
    def trace_storage(self):
        """延迟获取 TraceStorage，用于 runs/list 的 token 汇总。"""
        if self._trace_storage is None:
            from src.infra.session.trace_storage import TraceStorage

            self._trace_storage = TraceStorage()
        return self._trace_storage

    @property
    def snapshot(self):
        if self._snapshot is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._snapshot = db[SNAPSHOT_COLLECTION_NAME]
        return self._snapshot

    @property
    def activity(self):
        if self._activity is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._activity = db["user_daily_activity"]
        return self._activity

    @property
    def activity_storage(self):
        """延迟获取 ActivityStorage（子1 的 user_daily_activity 封装）。"""
        if self._activity_storage is None:
            from src.infra.analytics.activity_storage import ActivityStorage

            self._activity_storage = ActivityStorage()
        return self._activity_storage

    # ── Indexes ─────────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """为支撑聚合查询创建/补足索引。"""
        try:
            await self.feedback.create_index([("created_at", -1)], background=True)
            await self.sessions.create_index([("created_at", -1)], background=True)
            # traces event 时间戳联合索引（仅在事件数组上扫描 token 事件）
            await self.traces.create_index(
                [("events.event_type", 1), ("events.timestamp", -1)],
                background=True,
                name="events_event_type_ts_idx",
            )
            # 按角色智能体统计 token/消息/运行明细（E1 后 metadata.persona_preset_id 落库）
            await self.traces.create_index(
                [("metadata.persona_preset_id", 1), ("started_at", -1)],
                background=True,
                name="metadata_preset_started_at_idx",
                sparse=True,
            )
            # 按角色智能体分反馈的两步法：sessions.metadata.persona_preset_id + session_id
            await self.sessions.create_index(
                [("metadata.persona_preset_id", 1), ("session_id", 1)],
                background=True,
                name="metadata_preset_session_id_idx",
                sparse=True,
            )
            # by-agent / by-persona 聚合 + 列表筛选
            await self.sessions.create_index(
                [("agent_id", 1), ("created_at", -1)],
                background=True,
                name="agent_id_created_at_idx",
            )
            await self.sessions.create_index(
                [("metadata.persona_preset_id", 1), ("created_at", -1)],
                background=True,
                name="metadata_preset_created_at_idx",
                sparse=True,
            )
            await self.sessions.create_index(
                [("user_id", 1), ("created_at", -1)],
                background=True,
                name="user_id_created_at_idx",
            )
            # S3: 日快照层索引
            await self.snapshot.create_index(
                [("date", 1), ("user_id", 1), ("persona_preset_id", 1), ("agent_id", 1)],
                unique=True,
                background=True,
            )
            await self.snapshot.create_index(
                [("date", 1), ("persona_preset_id", 1)], background=True
            )
            await self.snapshot.create_index(
                [("date", 1), ("agent_id", 1)], background=True
            )
            # S2: 用户日活跃记录索引
            await self.activity.create_index(
                [("user_id", 1), ("date", 1)],
                unique=True,
                background=True,
                name="user_id_date_unique",
            )
            await self.activity.create_index(
                [("date", 1), ("user_id", 1)],
                background=True,
                name="date_user_id_idx",
            )
            # 使用情况报表依赖：traces 侧 events_event_type_ts_idx（上方）筛事件类型，
            # sessions 侧 session_id_idx（SessionStorage 建）支撑 persona 归属 $lookup。
            logger.info("Analytics indexes ensured")
        except Exception as e:
            logger.warning("Failed to ensure analytics indexes: %s", e)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _date_range_query(start: datetime, end: datetime) -> dict[str, Any]:
        return {"created_at": {"$gte": start, "$lt": end}}

    @staticmethod
    def _day_bucket_expr(field: str = "$created_at") -> dict[str, Any]:
        return {
            "$dateToString": {
                "format": "%Y-%m-%d",
                "date": field,
                "timezone": _BUCKET_TZ,
            }
        }

    # ── Aggregation pipelines ──────────────────────────────────────

    async def get_overview(
        self,
        start: datetime,
        end: datetime,
        filters: UsageFilters | None = None,
    ) -> OverviewResponse:
        """概览卡片数据（4 个指标）

        active_users / total_sessions / total_tokens 三项走统一使用情况层，
        与 /usage/summary、/users/list 同源；up_vote_rate 仍按反馈表独立统计。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        usage_filters = filters or UsageFilters(start=s, end=e)

        # Up vote rate: count rating in {up, down} only (never "like")
        feedback_stats_pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    **self._date_range_query(s, e),
                    "rating": {"$in": ["up", "down"]},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "up": {
                        "$sum": {
                            "$cond": [{"$eq": ["$rating", "up"]}, 1, 0]
                        }
                    },
                    "down": {
                        "$sum": {
                            "$cond": [{"$eq": ["$rating", "down"]}, 1, 0]
                        }
                    },
                }
            },
        ]

        usage_summary, (feedback_stats,) = await asyncio.gather(
            self.get_usage_summary(usage_filters),
            self._fan_out([(self.feedback, feedback_stats_pipeline)]),
        )

        if feedback_stats:
            up_fb = int(feedback_stats[0].get("up", 0) or 0)
            down_fb = int(feedback_stats[0].get("down", 0) or 0)
            up_rate = _compute_up_vote_rate(up_fb, down_fb)
        else:
            up_rate = 0.0

        return OverviewResponse(
            active_users=usage_summary.active_users,
            total_sessions=usage_summary.new_sessions,
            total_tokens=usage_summary.total_tokens,
            up_vote_rate=up_rate,
        )

    async def get_active_users_trend(
        self,
        start: datetime,
        end: datetime,
        filters: UsageFilters | None = None,
    ) -> list[TrendDataPoint]:
        """按天统计活跃用户数（区间内发过消息的用户）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        return await self.get_active_users_by_day(
            filters or UsageFilters(start=s, end=e)
        )

    async def get_sessions_trend(
        self,
        start: datetime,
        end: datetime,
        filters: UsageFilters | None = None,
    ) -> SessionsTrendResponse:
        """会话/消息趋势。messages 为用户发送的消息数（user:message 事件）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        usage_filters = filters or UsageFilters(start=s, end=e)
        trend = await self.get_usage_trend(usage_filters)

        sessions_trend = [
            TrendDataPoint(date=point.date, value=float(point.new_sessions))
            for point in trend
        ]
        messages_trend = [
            TrendDataPoint(date=point.date, value=float(point.user_messages))
            for point in trend
        ]
        total_sessions = sum(point.new_sessions for point in trend)
        return SessionsTrendResponse(
            sessions=sessions_trend,
            messages=messages_trend,
            total_sessions=total_sessions,
        )

    async def get_tokens_by_model(
        self,
        start: datetime,
        end: datetime,
        filters: UsageFilters | None = None,
    ) -> list[ByLabelItem]:
        """按模型聚合 token 消耗。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        if filters is None:
            pipeline: list[dict[str, Any]] = [
                {
                    "$match": {
                        "events.event_type": _TOKEN_USAGE_EVENT,
                        "started_at": {"$gte": s, "$lt": e},
                    }
                },
            ]
        else:
            # Keep persona fallback and role/agent filtering in the shared
            # usage-facts stages.  The final project removes events, so token
            # events are unwound immediately before that project stage.
            pipeline = usage_facts_stages(filters)[:-1]
        pipeline.extend(
            [
                {"$unwind": "$events"},
                {"$match": {"events.event_type": _TOKEN_USAGE_EVENT}},
                {
                    "$group": {
                        "_id": _model_label_expr(),
                        "value": {
                            "$sum": {
                                "$ifNull": ["$events.data.total_tokens", 0],
                            }
                        },
                    }
                },
                {"$match": {"_id": {"$ne": None}}},
                {"$sort": {"value": -1}},
                {"$limit": 50},
                {"$project": {"_id": 0, "label": "$_id", "value": 1}},
            ]
        )
        out: list[ByLabelItem] = []
        async for doc in self.traces.aggregate(pipeline):
            out.append(
                ByLabelItem(
                    label=str(doc.get("label", "unknown")), value=float(doc.get("value", 0))
                )
            )
        return out

    async def get_sessions_by_agent(
        self,
        start: datetime,
        end: datetime,
        limit: int = _TOP_PRESET_LIMIT,
        filters: UsageFilters | None = None,
    ) -> list[ByLabelItem]:
        """按 agent_id 聚合活跃会话数（by-agent 环图）。

        与会话 KPI 卡及环图中心同源（``active_sessions``），因此切片口径与中心
        数字一致；数据来自快照层，历史值同样不可变。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        resolved = filters or UsageFilters(start=s, end=e)
        return await self._sessions_by_dimension(
            resolved, dimension="by_agent", key="agent_id", limit=limit
        )

    async def _sessions_by_dimension(
        self,
        filters: UsageFilters,
        *,
        dimension: str,
        key: str,
        limit: int,
        name_key: str | None = None,
    ) -> list[ByLabelItem]:
        """Top-N active sessions for one snapshot dimension."""
        try:
            result = await read_or_freeze(filters, storage=self)
            rows = list(result.get(dimension, []) or [])
        except Exception as ex:
            logger.warning("sessions by %s from snapshot failed: %s", key, ex)
            return []

        missing_name_ids = [
            str(row.get(key))
            for row in rows
            if name_key and row.get(key) and not row.get(name_key)
        ]
        names = await self._preset_names(missing_name_ids) if missing_name_ids else {}

        items: list[ByLabelItem] = []
        for row in rows:
            identifier = row.get(key)
            value = int(row.get("active_sessions", 0) or 0)
            if value <= 0:
                continue
            if name_key:
                if not identifier:
                    continue
                label = row.get(name_key) or names.get(str(identifier)) or str(identifier)
                items.append(
                    ByLabelItem(
                        label=str(label),
                        value=float(value),
                        id=str(identifier),
                    )
                )
                continue
            label = str(identifier or "default")
            items.append(ByLabelItem(label=label, value=float(value)))
        items.sort(key=lambda item: (-item.value, item.label))
        return items[:limit]

    async def get_sessions_by_persona(
        self,
        start: datetime,
        end: datetime,
        limit: int = _TOP_PRESET_LIMIT,
        filters: UsageFilters | None = None,
    ) -> list[ByLabelItem]:
        """按 persona_preset_id 聚合活跃会话数（by-persona 环图）。

        与会话 KPI 卡及环图中心同源（``active_sessions``）。persona 归属沿用
        统一口径：会话 metadata 优先、缺失回退 trace metadata。
        label 优先使用 persona_preset_name，缺失时回退 preset_id。
        """
        s_dt = _ensure_datetime(start)
        e_dt = _ensure_datetime(end)
        resolved = filters or UsageFilters(start=s_dt, end=e_dt)
        return await self._sessions_by_dimension(
            resolved,
            dimension="by_persona",
            key="persona_preset_id",
            limit=limit,
            name_key="persona_preset_name",
        )

    # ── PR2: 按角色智能体 + 反馈 + 钻取明细 ─────────────────────────

    @staticmethod
    def _reason_distribution(reasons: list[Any]) -> list[ByLabelItem]:
        """把 $push 出的 reason 列表 reduce 成 [{reason, count}]，过滤 None。"""
        counts: dict[str, int] = {}
        for r in reasons:
            if not r:
                continue
            counts[str(r)] = counts.get(str(r), 0) + 1
        return [
            ByLabelItem(label=reason, value=float(count))
            for reason, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]

    async def _preset_session_ids(
        self,
        preset_id: str | None,
        start: datetime,
        end: datetime,
    ) -> list[str]:
        """取该 preset 在区间内创建的会话 session_id 集合（两步法 Step1）。

        preset_id 为 None 时返回空（表示不过滤）。
        """
        if not preset_id:
            return []
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        try:
            return await self.sessions.distinct(
                "session_id",
                {
                    "metadata.persona_preset_id": preset_id,
                    "created_at": {"$gte": s, "$lt": e},
                },
            )
        except Exception as ex:
            logger.warning("distinct session_id for preset failed: %s", ex)
            return []

    async def _preset_names(self, preset_ids: list[str]) -> dict[str, str]:
        """批量取 persona_presets 名称（_id 是 ObjectId，preset_id 是字符串）。"""
        if not preset_ids:
            return {}
        from bson import ObjectId

        object_ids: list[Any] = []
        for pid in preset_ids:
            try:
                object_ids.append(ObjectId(pid))
            except Exception:
                continue
        if not object_ids:
            return {}
        out: dict[str, str] = {}
        try:
            async for doc in self.persona_presets.find(
                {"_id": {"$in": object_ids}}, {"name": 1}
            ):
                out[str(doc["_id"])] = str(doc.get("name", "") or "")
        except Exception as ex:
            logger.warning("preset names lookup failed: %s", ex)
        return out

    async def get_preset_metrics(
        self,
        preset_id: str,
        start: datetime,
        end: datetime,
    ) -> PresetAnalyticsResponse:
        """单角色智能体完整指标：基础 4 指标 + 点赞率 + 点踩原因分布。

        基础指标走统一使用情况层，与 /usage/summary 对同一 persona 严格一致。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        usage = await self.get_usage_summary(
            UsageFilters(start=s, end=e, persona_preset_id=preset_id)
        )

        # 反馈：两步法（先取该 preset 的 session_ids，再聚合 feedback）
        session_ids = await self._preset_session_ids(preset_id, start, end)
        up_count = 0
        down_total = 0
        reasons: list[Any] = []
        if session_ids:
            feedback_pipeline: list[dict[str, Any]] = [
                {
                    "$match": {
                        "created_at": {"$gte": s, "$lt": e},
                        "session_id": {"$in": session_ids},
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "up": {
                            "$sum": {"$cond": [{"$eq": ["$rating", "up"]}, 1, 0]}
                        },
                        "down": {
                            "$sum": {"$cond": [{"$eq": ["$rating", "down"]}, 1, 0]}
                        },
                        "reasons": {"$push": "$reason"},
                    }
                },
            ]
            try:
                async for doc in self.feedback.aggregate(feedback_pipeline):
                    up_count = int(doc.get("up", 0) or 0)
                    down_total = int(doc.get("down", 0) or 0)
                    reasons = doc.get("reasons") or []
            except Exception as ex:
                logger.warning("preset feedback aggregation failed: %s", ex)

        up_rate = _compute_up_vote_rate(up_count, down_total)

        return PresetAnalyticsResponse(
            total_messages=usage.user_messages,
            total_sessions=usage.new_sessions,
            active_users=usage.active_users,
            total_tokens=usage.total_tokens,
            up_vote_rate=up_rate,
            down_reasons=self._reason_distribution(reasons),
        )

    async def get_feedback_summary(
        self,
        start: datetime,
        end: datetime,
    ) -> FeedbackSummaryResponse:
        """全局反馈汇总（含点踩原因分布）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {"$match": {"created_at": {"$gte": s, "$lt": e}}},
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "up": {"$sum": {"$cond": [{"$eq": ["$rating", "up"]}, 1, 0]}},
                    "down": {"$sum": {"$cond": [{"$eq": ["$rating", "down"]}, 1, 0]}},
                    "reasons": {"$push": "$reason"},
                }
            },
        ]
        total = 0
        up_count = 0
        down_count = 0
        reasons: list[Any] = []
        try:
            async for doc in self.feedback.aggregate(pipeline):
                total = int(doc.get("total", 0) or 0)
                up_count = int(doc.get("up", 0) or 0)
                down_count = int(doc.get("down", 0) or 0)
                reasons = doc.get("reasons") or []
        except Exception as ex:
            logger.warning("feedback summary aggregation failed: %s", ex)
        # 点赞率仅基于 up/down，与 get_overview 公式一致
        up_percentage = _compute_up_vote_rate(up_count, down_count)
        return FeedbackSummaryResponse(
            total=total,
            up_count=up_count,
            down_count=down_count,
            up_percentage=up_percentage,
            reason_distribution=self._reason_distribution(reasons),
        )

    async def get_feedback_by_preset(
        self,
        start: datetime,
        end: datetime,
    ) -> ByPresetFeedbackResponse:
        """按角色智能体分反馈（两步法 + persona_presets 名称 $lookup）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        # Step1：preset_id → session_ids
        sessions_pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "created_at": {"$gte": s, "$lt": e},
                    "metadata.persona_preset_id": {"$exists": True, "$ne": None},
                }
            },
            {
                "$group": {
                    "_id": "$metadata.persona_preset_id",
                    "session_ids": {"$addToSet": "$session_id"},
                }
            },
        ]
        preset_to_session_ids: dict[str, list[str]] = {}
        try:
            async for doc in self.sessions.aggregate(sessions_pipeline):
                pid = doc.get("_id")
                if pid:
                    preset_to_session_ids[str(pid)] = list(doc.get("session_ids") or [])
        except Exception as ex:
            logger.warning("feedback by-preset sessions aggregation failed: %s", ex)

        if not preset_to_session_ids:
            return ByPresetFeedbackResponse(items=[])

        # Step2：一次聚合按 session_id 分组 rating，Python 侧再归到 preset
        all_session_ids = [s_id for ids in preset_to_session_ids.values() for s_id in ids]
        session_id_to_preset: dict[str, str] = {}
        for pid, ids in preset_to_session_ids.items():
            for s_id in ids:
                session_id_to_preset[s_id] = pid
        feedback_pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "created_at": {"$gte": s, "$lt": e},
                    "session_id": {"$in": all_session_ids},
                }
            },
            {
                "$group": {
                    "_id": {"session_id": "$session_id", "rating": "$rating"},
                    "count": {"$sum": 1},
                }
            },
        ]
        # preset_id → {up, down}
        preset_counts: dict[str, dict[str, int]] = {}
        try:
            async for doc in self.feedback.aggregate(feedback_pipeline):
                key: dict[str, Any] = doc.get("_id") or {}
                sid: Any = key.get("session_id")
                rating: Any = key.get("rating")
                pid = session_id_to_preset.get(sid) if sid else None
                if not pid or rating not in ("up", "down"):
                    continue
                bucket = preset_counts.setdefault(pid, {"up": 0, "down": 0})
                bucket[rating] += int(doc.get("count", 0) or 0)
        except Exception as ex:
            logger.warning("feedback by-preset aggregation failed: %s", ex)

        # Step3：取角色名称
        names = await self._preset_names(list(preset_counts.keys()))

        items: list[ByPresetFeedbackItem] = []
        for pid, bucket in preset_counts.items():
            up_count = int(bucket.get("up", 0))
            down_count = int(bucket.get("down", 0))
            total = up_count + down_count
            up_percentage = round((up_count / total) * 100, 1) if total > 0 else 0.0
            items.append(
                ByPresetFeedbackItem(
                    preset_id=pid,
                    preset_name=names.get(pid, ""),
                    up_count=up_count,
                    down_count=down_count,
                    total=total,
                    up_percentage=up_percentage,
                )
            )
        items.sort(key=lambda it: it.total, reverse=True)
        return ByPresetFeedbackResponse(items=items)

    async def _session_user_ids_for_role(
        self,
        role_id: str,
        start: datetime,
        end: datetime,
    ) -> list[str]:
        """返回持有指定 RBAC 角色的全部用户，不依赖会话创建时间。"""
        try:
            cursor = self.users.find({"roles": role_id}, {"_id": 1})
            docs = await cursor.to_list(length=None)
            return [str(doc["_id"]) for doc in docs if doc.get("_id") is not None]
        except Exception as ex:
            logger.warning("filter users by role failed: %s", ex)
            return []

    def _build_session_query(
        self,
        start: datetime,
        end: datetime,
        *,
        persona_preset_id: str | None = None,
        agent_id: str | None = None,
        role_user_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """构建会话列表查询条件。

        role_user_ids 为 None 表示不按角色过滤；空列表表示无匹配用户。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        query: dict[str, Any] = {"created_at": {"$gte": s, "$lt": e}}
        if persona_preset_id:
            query["metadata.persona_preset_id"] = persona_preset_id
        if agent_id:
            query["agent_id"] = agent_id
        if role_user_ids is not None:
            query["user_id"] = {"$in": role_user_ids}
        return query

    async def list_sessions(
        self,
        start: datetime,
        end: datetime,
        skip: int = 0,
        limit: int = 20,
        *,
        agent_id: str | None = None,
        persona_preset_id: str | None = None,
        role_id: str | None = None,
        sort: str = "recent",
    ) -> SessionListResponse:
        """会话明细列表（时间 + agent/persona/角色筛选 + 排序 + 分页）。

        sort:
          - recent: 按 created_at 降序（默认，兼容现网）
          - frequency: 按同 user_id 会话频次降序，再按 created_at 降序
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        role_user_ids: list[str] | None = None
        if role_id:
            role_user_ids = await self._session_user_ids_for_role(role_id, s, e)

        query = self._build_session_query(
            s,
            e,
            persona_preset_id=persona_preset_id,
            agent_id=agent_id,
            role_user_ids=role_user_ids,
        )
        projection = {
            "session_id": 1,
            "name": 1,
            "user_id": 1,
            "agent_id": 1,
            "created_at": 1,
            "updated_at": 1,
            "is_active": 1,
            "task_status": 1,
            "unread_count": 1,
            "metadata.persona_preset_id": 1,
            "metadata.persona_preset_name": 1,
        }
        sort_mode = (sort or "recent").lower()
        try:
            total = await self.sessions.count_documents(query)
            if sort_mode == "frequency":
                pipeline: list[dict[str, Any]] = [
                    {"$match": query},
                    {
                        "$addFields": {
                            "_user_key": {
                                "$ifNull": ["$user_id", ""]
                            }
                        }
                    },
                    {
                        "$setWindowFields": {
                            "partitionBy": "$_user_key",
                            "output": {
                                "_freq": {"$count": {}},
                            },
                        }
                    },
                    {"$sort": {"_freq": -1, "created_at": -1}},
                    {"$skip": skip},
                    {"$limit": limit},
                    # inclusion projection cannot mix with field exclusion;
                    # drop window helpers in a separate $unset stage.
                    {"$project": projection},
                    {"$unset": ["_freq", "_user_key"]},
                ]
                docs = await self.sessions.aggregate(pipeline).to_list(length=limit)
            else:
                cursor = self.sessions.find(query, projection).sort("created_at", -1)
                docs = await cursor.skip(skip).limit(limit).to_list(length=limit)
        except Exception as ex:
            logger.warning("list sessions failed: %s", ex)
            return SessionListResponse(total=0, skip=skip, limit=limit, has_more=False)

        # Batch-load username (employee id) to avoid N+1 and long ObjectId-only UI.
        # Real users collection keys by ``_id`` ObjectId (mapped to ``id`` on read).
        user_ids = [
            str(doc.get("user_id"))
            for doc in docs
            if doc.get("user_id")
        ]
        username_by_id: dict[str, str] = {}
        object_ids = _user_object_ids(user_ids)
        if object_ids:
            try:
                async for udoc in self.users.find(
                    {"_id": {"$in": object_ids}},
                    {"_id": 1, "username": 1},
                ):
                    uid = str(udoc.get("_id") or "")
                    if uid:
                        username_by_id[uid] = str(udoc.get("username") or "")
            except Exception as ex:
                logger.warning("list sessions username lookup failed: %s", ex)

        items: list[SessionListItem] = []
        for doc in docs:
            metadata = doc.get("metadata") or {}
            uid = doc.get("user_id")
            uid_str = str(uid) if uid else ""
            username = username_by_id.get(uid_str) or None
            items.append(
                SessionListItem(
                    id=str(doc.get("session_id") or doc.get("_id") or ""),
                    name=doc.get("name"),
                    user_id=uid_str or None,
                    username=username if username else None,
                    agent_id=str(doc.get("agent_id") or "default"),
                    created_at=doc.get("created_at"),
                    updated_at=doc.get("updated_at"),
                    is_active=bool(doc.get("is_active", True)),
                    task_status=doc.get("task_status"),
                    unread_count=int(doc.get("unread_count", 0) or 0),
                    persona_preset_id=metadata.get("persona_preset_id"),
                    persona_preset_name=metadata.get("persona_preset_name"),
                )
            )
        has_more = (skip + len(items)) < total
        return SessionListResponse(
            items=items, total=total, skip=skip, limit=limit, has_more=has_more
        )

    async def _restrict_to_first_use(self, filters: UsageFilters) -> UsageFilters:
        """Narrow ``role_user_ids`` to users whose first message day is in range.

        Shares ``_insights_new_users``'s source so the drill-down list matches
        the insight number it was opened from.
        """
        start_str, end_str = range_to_date_strings(filters.start, filters.end)
        candidates = await self.activity_storage.distinct_users(
            start_str, end_str, source="message"
        )
        if filters.role_user_ids is not None:
            allowed = set(filters.role_user_ids)
            candidates = [uid for uid in candidates if uid in allowed]
        first_dates = await self.activity_storage.first_message_date(candidates)
        new_user_ids = [
            uid
            for uid in candidates
            if (first := first_dates.get(uid)) and start_str <= first <= end_str
        ]
        return UsageFilters(
            start=filters.start,
            end=filters.end,
            persona_preset_id=filters.persona_preset_id,
            agent_id=filters.agent_id,
            role_user_ids=new_user_ids,
        )

    async def list_active_users(
        self,
        start: datetime,
        end: datetime,
        skip: int = 0,
        limit: int = 20,
        *,
        agent_id: str | None = None,
        persona_preset_id: str | None = None,
        role_id: str | None = None,
        sort: str = "frequency",
        first_use: bool = False,
    ) -> ActiveUserListResponse:
        """活跃用户列表。

        活跃定义：区间内发过 user:message 的用户，与 /overview 的 active_users
        同源，因此本列表的 total 必然等于概览卡片数字。
        session_count 为该用户在区间内有消息往来的会话去重数。
        sort:
          - frequency: 按会话数降序（默认）
          - recent: 按最近活跃时间降序
        first_use:
          仅保留首次使用日落在本区间的用户，与洞察栏「本期新增使用者」同源。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        filters = await self.build_usage_filters(
            s,
            e,
            persona_preset_id=persona_preset_id,
            agent_id=agent_id,
            role_id=role_id,
        )
        if first_use:
            filters = await self._restrict_to_first_use(filters)

        sort_mode = (sort or "frequency").lower()
        sort_stage = (
            {"$sort": {"last_active_at": -1, "session_count": -1}}
            if sort_mode == "recent"
            else {"$sort": {"session_count": -1, "last_active_at": -1}}
        )
        pipeline = usage_facts_stages(filters) + [
            {"$match": {"user_messages": {"$gt": 0}, "user_id": {"$nin": [None, ""]}}},
            {
                "$group": {
                    "_id": "$user_id",
                    "active_session_ids": {"$addToSet": "$session_id"},
                    "last_active_at": {"$max": "$started_at"},
                }
            },
            {
                "$addFields": {
                    "session_count": {
                        "$size": {
                            "$filter": {
                                "input": "$active_session_ids",
                                "as": "session_id",
                                "cond": {
                                    "$and": [
                                        {"$ne": ["$$session_id", None]},
                                        {"$ne": ["$$session_id", ""]},
                                    ]
                                },
                            }
                        }
                    },
                }
            },
            {
                "$facet": {
                    "total": [{"$count": "count"}],
                    "items": [
                        sort_stage,
                        {"$skip": skip},
                        {"$limit": limit},
                    ],
                }
            },
        ]
        try:
            facet_docs = await self.traces.aggregate(pipeline).to_list(length=1)
        except Exception as ex:
            logger.warning("list active users failed: %s", ex)
            return ActiveUserListResponse(total=0, skip=skip, limit=limit, has_more=False)

        facet = facet_docs[0] if facet_docs else {}
        total_arr = facet.get("total") or []
        total = int(total_arr[0].get("count", 0)) if total_arr else 0
        raw_items = facet.get("items") or []

        user_ids = [str(doc.get("_id")) for doc in raw_items if doc.get("_id")]
        user_meta: dict[str, dict[str, Any]] = {}
        object_ids = _user_object_ids(user_ids)
        if object_ids:
            try:
                cursor = self.users.find(
                    {"_id": {"$in": object_ids}},
                    {"_id": 1, "username": 1, "display_name": 1, "roles": 1},
                )
                async for udoc in cursor:
                    uid = str(udoc.get("_id") or "")
                    if uid:
                        user_meta[uid] = udoc
            except Exception as ex:
                logger.warning("load active user meta failed: %s", ex)

        items: list[ActiveUserListItem] = []
        for doc in raw_items:
            uid = str(doc.get("_id") or "")
            meta = user_meta.get(uid) or {}
            roles_raw = meta.get("roles") or []
            roles = [str(r) for r in roles_raw if r]
            items.append(
                ActiveUserListItem(
                    user_id=uid,
                    username=str(meta.get("username") or ""),
                    display_name=meta.get("display_name"),
                    roles=roles,
                    session_count=int(doc.get("session_count", 0) or 0),
                    last_active_at=doc.get("last_active_at"),
                )
            )
        has_more = (skip + len(items)) < total
        return ActiveUserListResponse(
            items=items, total=total, skip=skip, limit=limit, has_more=has_more
        )

    async def list_feedback(
        self,
        start: datetime,
        end: datetime,
        preset_id: str | None = None,
        rating: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> FeedbackListResponse:
        """反馈明细列表（时间 + preset + rating 筛选 + 分页）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        query: dict[str, Any] = {"created_at": {"$gte": s, "$lt": e}}
        if rating in ("up", "down"):
            query["rating"] = rating
        if preset_id:
            session_ids = await self._preset_session_ids(preset_id, start, end)
            query["session_id"] = {"$in": session_ids} if session_ids else {"$in": []}

        projection = {
            "user_id": 1,
            "username": 1,
            "session_id": 1,
            "run_id": 1,
            "rating": 1,
            "comment": 1,
            "reason": 1,
            "created_at": 1,
        }
        try:
            total = await self.feedback.count_documents(query)
            cursor = self.feedback.find(query, projection).sort("created_at", -1)
            docs = await cursor.skip(skip).limit(limit).to_list(length=limit)
        except Exception as ex:
            logger.warning("list feedback failed: %s", ex)
            return FeedbackListResponse(total=0, skip=skip, limit=limit, has_more=False)

        # 批量取 session 元数据（persona_preset_id/name）以显示所属角色
        session_ids = [str(doc.get("session_id")) for doc in docs if doc.get("session_id")]
        session_meta: dict[str, dict[str, Any]] = {}
        if session_ids:
            try:
                async for sdoc in self.sessions.find(
                    {"session_id": {"$in": session_ids}},
                    {"session_id": 1, "metadata.persona_preset_id": 1, "metadata.persona_preset_name": 1},
                ):
                    sm = sdoc.get("metadata") or {}
                    session_meta[str(sdoc.get("session_id"))] = {
                        "persona_preset_id": sm.get("persona_preset_id"),
                        "persona_preset_name": sm.get("persona_preset_name"),
                    }
            except Exception as ex:
                logger.warning("feedback list session lookup failed: %s", ex)

        items: list[FeedbackListItem] = []
        for doc in docs:
            sid = str(doc.get("session_id") or "")
            meta = session_meta.get(sid, {})
            items.append(
                FeedbackListItem(
                    id=str(doc.get("_id")),
                    user_id=str(doc.get("user_id") or ""),
                    username=str(doc.get("username") or ""),
                    session_id=sid,
                    run_id=str(doc.get("run_id") or ""),
                    rating=str(doc.get("rating") or ""),
                    comment=doc.get("comment"),
                    reason=doc.get("reason"),
                    created_at=doc.get("created_at"),
                    persona_preset_id=meta.get("persona_preset_id"),
                    persona_preset_name=meta.get("persona_preset_name"),
                )
            )
        has_more = (skip + len(items)) < total
        return FeedbackListResponse(
            items=items, total=total, skip=skip, limit=limit, has_more=has_more
        )

    async def list_runs(
        self,
        start: datetime,
        end: datetime,
        preset_id: str | None = None,
        skip: int = 0,
        limit: int = 20,
        model: str | None = None,
    ) -> RunListResponse:
        """运行明细列表（含 token 用量，分页）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        query: dict[str, Any] = {"started_at": {"$gte": s, "$lt": e}}
        if preset_id:
            query["metadata.persona_preset_id"] = preset_id
        if model:
            # Use the same ownership expression as the model donut.  This
            # also treats missing/null/empty legacy fields as ``unknown``.
            query.update(_model_event_match_expr(model))
        projection = {
            "events": 0,  # 避免一次取出大事件数组，token 单独查
        }
        try:
            total = await self.traces.count_documents(query)
            cursor = self.traces.find(query, projection).sort("started_at", -1)
            docs = await cursor.skip(skip).limit(limit).to_list(length=limit)
        except Exception as ex:
            logger.warning("list runs failed: %s", ex)
            return RunListResponse(total=0, skip=skip, limit=limit, has_more=False)

        items: list[RunListItem] = []
        for doc in docs:
            trace_id = doc.get("trace_id")
            total_tokens = 0
            if trace_id:
                try:
                    events = await self.trace_storage.get_trace_events(
                        trace_id, event_types=[_TOKEN_USAGE_EVENT]
                    )
                    for ev in events:
                        data = ev.get("data") or {}
                        if model and _model_label(data) != model:
                            continue
                        total_tokens += int(data.get("total_tokens", 0) or 0)
                except Exception as ex:
                    logger.warning("token sum for trace %s failed: %s", trace_id, ex)
            metadata = doc.get("metadata") or {}
            items.append(
                RunListItem(
                    run_id=str(doc.get("run_id") or ""),
                    trace_id=trace_id,
                    session_id=str(doc.get("session_id") or ""),
                    agent_id=str(doc.get("agent_id") or "default"),
                    user_id=doc.get("user_id"),
                    started_at=doc.get("started_at"),
                    completed_at=doc.get("completed_at"),
                    status=str(doc.get("status") or "running"),
                    event_count=int(doc.get("event_count", 0) or 0),
                    total_tokens=total_tokens,
                    persona_preset_id=metadata.get("persona_preset_id"),
                )
            )
        has_more = (skip + len(items)) < total
        return RunListResponse(
            items=items, total=total, skip=skip, limit=limit, has_more=has_more
        )

    # ── 使用情况报表（统一口径）─────────────────────────────────────

    async def build_usage_filters(
        self,
        start: datetime,
        end: datetime,
        *,
        persona_preset_id: str | None = None,
        agent_id: str | None = None,
        role_id: str | None = None,
    ) -> UsageFilters:
        """把路由层参数解析为 UsageFilters（role_id 需要一次用户表查询）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        role_user_ids: list[str] | None = None
        if role_id:
            role_user_ids = await self._session_user_ids_for_role(role_id, s, e)
        return UsageFilters(
            start=s,
            end=e,
            persona_preset_id=persona_preset_id or None,
            agent_id=agent_id or None,
            role_user_ids=role_user_ids,
        )

    @staticmethod
    def _active_set_expr(field: str) -> dict[str, Any]:
        """仅把发过用户消息的 trace 计入去重集合；其余产出 null，Python 侧过滤。"""
        return {"$addToSet": {"$cond": [{"$gt": ["$user_messages", 0]}, field, None]}}

    @staticmethod
    def _count_active(values: Any) -> int:
        return len({v for v in (values or []) if v})

    async def _usage_summary_numbers(self, filters: UsageFilters) -> dict[str, int]:
        """单一区间的汇总数值（快照优先，失败回落实时）。

        返回六个字段的 dict；带 persona/agent 筛选时 ``active_users = using_users``
        （登录活跃无 persona/agent 归属）。
        """
        numbers: dict[str, int]
        try:
            result = await read_or_freeze(filters, storage=self)

            total = result.get("total", {})
            numbers = {
                "active_users": int(result.get("active_users", 0) or 0),
                "using_users": int(result.get("using_users", 0) or 0),
                "new_sessions": int(total.get("new_sessions", 0) or 0),
                "active_sessions": int(total.get("active_sessions", 0) or 0),
                "user_messages": int(total.get("user_messages", 0) or 0),
                "total_tokens": int(total.get("tokens", 0) or 0),
            }
        except Exception as ex:
            logger.warning("get_usage_summary from snapshot failed: %s", ex)
            # Fallback to real-time computation
            pipeline = usage_facts_stages(filters) + [
                {
                    "$group": {
                        "_id": None,
                        "user_messages": {"$sum": "$user_messages"},
                        "total_tokens": {"$sum": "$tokens"},
                        "active_user_ids": self._active_set_expr("$user_id"),
                        "active_session_ids": self._active_set_expr("$session_id"),
                    }
                },
            ]
            try:
                docs, new_sessions = await asyncio.gather(
                    self.traces.aggregate(pipeline).to_list(length=1),
                    self.sessions.count_documents(new_sessions_match(filters)),
                )
            except Exception as ex2:
                logger.warning("get_usage_summary fallback failed: %s", ex2)
                return {
                    "active_users": 0,
                    "using_users": 0,
                    "new_sessions": 0,
                    "active_sessions": 0,
                    "user_messages": 0,
                    "total_tokens": 0,
                }

            doc = docs[0] if docs else {}
            using_users = self._count_active(doc.get("active_user_ids"))
            numbers = {
                "active_users": using_users,
                "using_users": using_users,
                "new_sessions": int(new_sessions or 0),
                "active_sessions": self._count_active(doc.get("active_session_ids")),
                "user_messages": int(doc.get("user_messages", 0) or 0),
                "total_tokens": int(doc.get("total_tokens", 0) or 0),
            }

        # Contract: persona/agent 筛选下，登录口径无法归属，活跃=使用
        if filters.persona_preset_id or filters.agent_id:
            numbers["active_users"] = numbers["using_users"]
        return numbers

    async def get_usage_summary(self, filters: UsageFilters) -> UsageSummaryResponse:
        """使用情况汇总：活跃用户 / 新建会话 / 活跃会话 / 用户消息 / token。

        同时返回紧邻的上一等长周期（``previous``），筛选条件保持一致。
        """
        numbers = await self._usage_summary_numbers(filters)

        # 上一等长周期：把当前区间整体前移，筛选条件不变
        start_str, end_str = range_to_date_strings(filters.start, filters.end)
        prev_start_str, prev_end_str = previous_range(start_str, end_str)
        prev_start, prev_end = resolve_range(prev_start_str, prev_end_str)
        prev_filters = UsageFilters(
            start=_ensure_datetime(prev_start),
            end=_ensure_datetime(prev_end),
            persona_preset_id=filters.persona_preset_id,
            agent_id=filters.agent_id,
            role_user_ids=filters.role_user_ids,
        )
        prev_numbers = await self._usage_summary_numbers(prev_filters)

        return UsageSummaryResponse(
            active_users=numbers["active_users"],
            using_users=numbers["using_users"],
            new_sessions=numbers["new_sessions"],
            active_sessions=numbers["active_sessions"],
            user_messages=numbers["user_messages"],
            total_tokens=numbers["total_tokens"],
            previous=UsageSummaryPrevious(**prev_numbers),
        )

    async def get_usage_trend(self, filters: UsageFilters) -> list[UsageTrendPoint]:
        """使用情况按天趋势。新建会话来自 sessions，其余来自 usage facts。"""
        try:
            result = await read_or_freeze(filters, storage=self)
            trend_data = result.get("trend", [])
            return [
                UsageTrendPoint(
                    date=point.get("date", ""),
                    new_sessions=int(point.get("new_sessions", 0)),
                    active_sessions=int(point.get("active_sessions", 0)),
                    user_messages=int(point.get("user_messages", 0)),
                    total_tokens=int(point.get("tokens", 0)),
                )
                for point in trend_data
            ]
        except Exception as ex:
            logger.warning("get_usage_trend from snapshot failed: %s", ex)
            # Fallback to real-time computation
            return await self._get_usage_trend_realtime(filters)

    async def _get_usage_trend_realtime(self, filters: UsageFilters) -> list[UsageTrendPoint]:
        """Real-time fallback for get_usage_trend."""
        facts_pipeline = usage_facts_stages(filters) + [
            {
                "$group": {
                    "_id": self._day_bucket_expr("$started_at"),
                    "user_messages": {"$sum": "$user_messages"},
                    "total_tokens": {"$sum": "$tokens"},
                    "active_session_ids": self._active_set_expr("$session_id"),
                }
            },
        ]
        sessions_pipeline: list[dict[str, Any]] = [
            {"$match": new_sessions_match(filters)},
            {
                "$group": {
                    "_id": self._day_bucket_expr("$created_at"),
                    "new_sessions": {"$sum": 1},
                }
            },
        ]

        facts_docs, sessions_docs = await self._fan_out(
            [
                (self.traces, facts_pipeline),
                (self.sessions, sessions_pipeline),
            ]
        )

        by_date: dict[str, dict[str, int]] = {}
        for doc in facts_docs:
            date = str(doc.get("_id") or "")
            if not date:
                continue
            bucket = by_date.setdefault(date, {})
            bucket["user_messages"] = int(doc.get("user_messages", 0) or 0)
            bucket["total_tokens"] = int(doc.get("total_tokens", 0) or 0)
            bucket["active_sessions"] = self._count_active(doc.get("active_session_ids"))
        for doc in sessions_docs:
            date = str(doc.get("_id") or "")
            if not date:
                continue
            by_date.setdefault(date, {})["new_sessions"] = int(
                doc.get("new_sessions", 0) or 0
            )

        return [
            UsageTrendPoint(
                date=date,
                new_sessions=values.get("new_sessions", 0),
                active_sessions=values.get("active_sessions", 0),
                user_messages=values.get("user_messages", 0),
                total_tokens=values.get("total_tokens", 0),
            )
            for date, values in sorted(by_date.items())
        ]

    async def get_usage_by_persona(
        self, filters: UsageFilters
    ) -> list[UsageByPersonaItem]:
        """使用情况按 Persona 分组。名称缺失时回查 persona_presets。"""
        try:
            result = await read_or_freeze(filters, storage=self)
            raw_items = result.get("by_persona", [])

            # Build names lookup (same pattern as real-time path)
            missing_name_ids = [
                item["persona_preset_id"]
                for item in raw_items
                if item.get("persona_preset_id") and not item.get("persona_preset_name")
            ]
            names = await self._preset_names(missing_name_ids)

            items: list[UsageByPersonaItem] = []
            for item in raw_items:
                preset_id = item.get("persona_preset_id")
                preset_id_str = str(preset_id) if preset_id else None
                name = str(item.get("persona_preset_name") or "")
                if not name and preset_id_str:
                    name = names.get(preset_id_str, "")
                items.append(
                    UsageByPersonaItem(
                        persona_preset_id=preset_id_str,
                        persona_preset_name=name,
                        active_users=item.get("active_users", 0),
                        active_sessions=item.get("active_sessions", 0),
                        user_messages=item.get("user_messages", 0),
                        total_tokens=item.get("tokens", 0),
                    )
                )
            return items
        except Exception as ex:
            logger.warning("get_usage_by_persona from snapshot failed: %s", ex)
            # Fallback to real-time computation
            return await self._get_usage_by_persona_realtime(filters)

    async def _get_usage_by_persona_realtime(
        self, filters: UsageFilters
    ) -> list[UsageByPersonaItem]:
        """Real-time fallback for get_usage_by_persona."""
        pipeline = usage_facts_stages(filters) + [
            {
                "$group": {
                    "_id": "$persona_preset_id",
                    "persona_preset_name": {"$first": "$persona_preset_name"},
                    "user_messages": {"$sum": "$user_messages"},
                    "total_tokens": {"$sum": "$tokens"},
                    "active_user_ids": self._active_set_expr("$user_id"),
                    "active_session_ids": self._active_set_expr("$session_id"),
                }
            },
            {"$sort": {"user_messages": -1}},
        ]
        try:
            docs = await self.traces.aggregate(pipeline).to_list(length=None)
        except Exception as ex:
            logger.warning("get_usage_by_persona realtime failed: %s", ex)
            return []

        missing_name_ids = [
            str(doc["_id"])
            for doc in docs
            if doc.get("_id") and not doc.get("persona_preset_name")
        ]
        names = await self._preset_names(missing_name_ids)

        items: list[UsageByPersonaItem] = []
        for doc in docs:
            preset_id = doc.get("_id")
            preset_id_str = str(preset_id) if preset_id else None
            name = str(doc.get("persona_preset_name") or "")
            if not name and preset_id_str:
                name = names.get(preset_id_str, "")
            items.append(
                UsageByPersonaItem(
                    persona_preset_id=preset_id_str,
                    persona_preset_name=name,
                    active_users=self._count_active(doc.get("active_user_ids")),
                    active_sessions=self._count_active(doc.get("active_session_ids")),
                    user_messages=int(doc.get("user_messages", 0) or 0),
                    total_tokens=int(doc.get("total_tokens", 0) or 0),
                )
            )
        return items

    async def _usage_by_user_persona_realtime(
        self, filters: UsageFilters
    ) -> list[dict[str, Any]]:
        """Real-time user×persona rows shaped like the snapshot contract."""
        trace_pipeline = usage_facts_stages(filters) + [
            {
                "$group": {
                    "_id": {
                        "user_id": "$user_id",
                        "persona_preset_id": "$persona_preset_id",
                    },
                    "persona_preset_name": {"$first": "$persona_preset_name"},
                    "user_messages": {"$sum": "$user_messages"},
                    "tokens": {"$sum": "$tokens"},
                    "active_session_ids": self._active_set_expr("$session_id"),
                    "last_active_at": {"$max": "$started_at"},
                }
            },
            {"$match": {"_id.user_id": {"$nin": [None, ""]}}},
        ]
        session_pipeline = [
            {"$match": new_sessions_match(filters)},
            {
                "$group": {
                    "_id": {
                        "user_id": "$user_id",
                        "persona_preset_id": "$metadata.persona_preset_id",
                    },
                    "new_session_ids": {
                        "$addToSet": {"$ifNull": ["$session_id", {"$toString": "$_id"}]}
                    },
                }
            },
        ]
        try:
            trace_docs, session_docs = await asyncio.gather(
                self.traces.aggregate(trace_pipeline).to_list(length=None),
                self.sessions.aggregate(session_pipeline).to_list(length=None),
            )
        except Exception as ex:
            logger.warning("usage by-user realtime fallback failed: %s", ex)
            return []

        by_key: dict[tuple[str, str | None], dict[str, Any]] = {}
        for doc in trace_docs:
            identifier = doc.get("_id") or {}
            user_id = identifier.get("user_id")
            if user_id in (None, ""):
                continue
            persona_id = identifier.get("persona_preset_id")
            key = (str(user_id), str(persona_id) if persona_id else None)
            by_key[key] = {
                "user_id": key[0],
                "persona_preset_id": key[1],
                "persona_preset_name": doc.get("persona_preset_name"),
                "user_messages": int(doc.get("user_messages", 0) or 0),
                "tokens": int(doc.get("tokens", 0) or 0),
                "active_sessions": self._count_active(doc.get("active_session_ids")),
                "new_sessions": 0,
                "last_active_at": doc.get("last_active_at"),
            }

        for doc in session_docs:
            identifier = doc.get("_id") or {}
            user_id = identifier.get("user_id")
            if user_id in (None, ""):
                continue
            persona_id = identifier.get("persona_preset_id")
            key = (str(user_id), str(persona_id) if persona_id else None)
            session_ids = {
                str(value) for value in (doc.get("new_session_ids") or []) if value
            }
            row = by_key.setdefault(
                key,
                {
                    "user_id": key[0],
                    "persona_preset_id": key[1],
                    "persona_preset_name": None,
                    "user_messages": 0,
                    "tokens": 0,
                    "active_sessions": 0,
                    "new_sessions": 0,
                    "last_active_at": None,
                },
            )
            row["new_sessions"] = len(session_ids)
        return list(by_key.values())

    async def list_usage_by_user(
        self,
        filters: UsageFilters,
        skip: int = 0,
        limit: int = 20,
    ) -> UsageByUserResponse:
        """使用明细，行粒度为「用户 × Persona」，仅含区间内发过消息的用户。"""
        try:
            result = await read_or_freeze(filters, storage=self)
            if result.get("snapshot_complete", True) is False:
                raise RuntimeError("analytics snapshot is incomplete")
            raw_items = list(result.get("by_user_persona", []) or [])
        except Exception as ex:
            # Summary degrades to real-time here, so the detail must too;
            # returning an empty page would contradict the KPI cards.
            logger.warning("list_usage_by_user from snapshot failed: %s", ex)
            raw_items = await self._usage_by_user_persona_realtime(filters)

        # Contract: only users who actually sent messages appear here. The
        # snapshot also carries new-session rows that have no trace yet.
        raw_items = [
            item for item in raw_items if int(item.get("user_messages", 0) or 0) > 0
        ]
        # Slicing an unordered aggregate would drift page contents between
        # requests, so order by traffic with a deterministic tiebreaker.
        raw_items.sort(
            key=lambda item: (
                -int(item.get("user_messages", 0) or 0),
                -int(item.get("total_tokens", item.get("tokens", 0)) or 0),
                str(item.get("user_id") or ""),
                str(item.get("persona_preset_id") or ""),
            )
        )
        total = len(raw_items)
        # Snapshot aggregation is deliberately not paginated in Mongo.  Keep
        # pagination here so summary, detail and CSV all consume one frozen set.
        page_items = raw_items[skip : skip + limit]
        user_ids = [
            str(doc.get("user_id") or (doc.get("_id") or {}).get("user_id"))
            for doc in page_items
            if (doc.get("user_id") or (doc.get("_id") or {}).get("user_id"))
        ]
        user_meta: dict[str, dict[str, Any]] = {}
        object_ids = _user_object_ids(user_ids)
        if object_ids:
            try:
                cursor = self.users.find(
                    {"_id": {"$in": object_ids}},
                    {"_id": 1, "username": 1, "display_name": 1, "roles": 1},
                )
                async for udoc in cursor:
                    uid = str(udoc.get("_id") or "")
                    if uid:
                        user_meta[uid] = udoc
            except Exception as ex:
                logger.warning("list_usage_by_user user lookup failed: %s", ex)

        missing_name_ids = [
            str(doc.get("persona_preset_id") or (doc.get("_id") or {}).get("persona_preset_id"))
            for doc in page_items
            if (doc.get("persona_preset_id") or (doc.get("_id") or {}).get("persona_preset_id"))
            and not doc.get("persona_preset_name")
        ]
        names = await self._preset_names(missing_name_ids)

        items: list[UsageByUserItem] = []
        for doc in page_items:
            key = doc.get("_id") or {}
            uid = str(doc.get("user_id") or key.get("user_id") or "")
            preset_id = doc.get("persona_preset_id") or key.get("persona_preset_id")
            preset_id_str = str(preset_id) if preset_id else None
            name = str(doc.get("persona_preset_name") or "")
            if not name and preset_id_str:
                name = names.get(preset_id_str, "")
            meta = user_meta.get(uid) or {}
            roles = [str(r) for r in (meta.get("roles") or []) if r]
            items.append(
                UsageByUserItem(
                    user_id=uid,
                    username=str(meta.get("username") or ""),
                    display_name=meta.get("display_name"),
                    roles=roles,
                    persona_preset_id=preset_id_str,
                    persona_preset_name=name,
                    new_sessions=int(doc.get("new_sessions", 0) or 0),
                    active_sessions=int(doc.get("active_sessions", 0) or 0),
                    user_messages=int(doc.get("user_messages", 0) or 0),
                    total_tokens=int(doc.get("tokens", doc.get("total_tokens", 0)) or 0),
                    last_active_at=doc.get("last_active_at"),
                )
            )

        has_more = (skip + len(items)) < total
        return UsageByUserResponse(
            items=items, total=total, skip=skip, limit=limit, has_more=has_more
        )

    # ── 洞察（insights）：四个结论一次给全 ───────────────────────────

    async def get_usage_insights(self, filters: UsageFilters) -> UsageInsightsResponse:
        """洞察栏：峰值时段 / Top3 token 用户 / 增长最快 Persona / 新增用户。

        数据不足时返回 null / 空数组 / 0，不抛错。
        """
        try:
            peak = await self._insights_peak(filters)
        except Exception as ex:
            logger.warning("insights peak failed: %s", ex)
            peak = None
        try:
            top_token_users = await self._insights_top_token_users(filters)
        except Exception as ex:
            logger.warning("insights top_token_users failed: %s", ex)
            top_token_users = []
        try:
            fastest = await self._insights_fastest_growing_persona(filters)
        except Exception as ex:
            logger.warning("insights fastest_growing_persona failed: %s", ex)
            fastest = None
        try:
            new_users = await self._insights_new_users(filters)
        except Exception as ex:
            logger.warning("insights new_users failed: %s", ex)
            new_users = 0

        return UsageInsightsResponse(
            peak=peak,
            top_token_users=top_token_users,
            fastest_growing_persona=fastest,
            new_users=new_users,
        )

    async def _insights_peak(self, filters: UsageFilters) -> UsageInsightsPeak | None:
        """峰值时段：只统计有用户消息的 trace，按 UTC+8 星期×小时分桶。

        峰值按用户消息自身的事件时间归属，而不是 trace 的开始时间。
        无消息时返回 None。
        """
        pipeline = usage_facts_stages(filters)[:-1] + [
            {"$match": {"user_messages": {"$gt": 0}}},
            {"$unwind": "$events"},
            {
                "$match": {
                    "events.event_type": "user:message",
                    "events.timestamp": {
                        "$gte": filters.start,
                        "$lt": filters.end,
                    },
                }
            },
            {
                "$group": {
                    "_id": {
                        "weekday": {
                            "$subtract": [
                                {
                                    "$dayOfWeek": {
                                        "date": "$events.timestamp",
                                        "timezone": _BUCKET_TZ,
                                    }
                                },
                                1,
                            ]
                        },
                        "hour": {
                            "$hour": {
                                "date": "$events.timestamp",
                                "timezone": _BUCKET_TZ,
                            }
                        },
                    },
                    "user_messages": {"$sum": 1},
                }
            },
            {"$sort": {"user_messages": -1}},
            {"$limit": 1},
        ]
        try:
            docs = await self.traces.aggregate(pipeline).to_list(length=1)
        except Exception as ex:
            logger.warning("_insights_peak aggregate failed: %s", ex)
            return None
        if not docs:
            return None
        doc = docs[0]
        key = doc.get("_id") or {}
        return UsageInsightsPeak(
            weekday=int(key.get("weekday", 0) or 0),
            hour=int(key.get("hour", 0) or 0),
            user_messages=int(doc.get("user_messages", 0) or 0),
        )

    async def _insights_top_token_users(
        self, filters: UsageFilters, limit: int = 3
    ) -> list[UsageInsightsTopTokenUser]:
        """Token 消耗 Top N 用户（默认 3），补齐用户名/显示名。"""
        pipeline = usage_facts_stages(filters) + [
            {"$match": {"user_id": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$user_id", "tokens": {"$sum": "$tokens"}}},
            {"$match": {"tokens": {"$gt": 0}}},
            {"$sort": {"tokens": -1}},
            {"$limit": limit},
        ]
        try:
            docs = await self.traces.aggregate(pipeline).to_list(length=limit)
        except Exception as ex:
            logger.warning("_insights_top_token_users aggregate failed: %s", ex)
            return []
        if not docs:
            return []

        user_ids = [str(doc.get("_id")) for doc in docs if doc.get("_id")]
        user_meta: dict[str, dict[str, Any]] = {}
        object_ids = _user_object_ids(user_ids)
        if object_ids:
            try:
                cursor = self.users.find(
                    {"_id": {"$in": object_ids}},
                    {"_id": 1, "username": 1, "display_name": 1},
                )
                async for udoc in cursor:
                    uid = str(udoc.get("_id") or "")
                    if uid:
                        user_meta[uid] = udoc
            except Exception as ex:
                logger.warning("_insights_top_token_users user lookup failed: %s", ex)

        return [
            UsageInsightsTopTokenUser(
                user_id=str(doc.get("_id")),
                username=str((user_meta.get(str(doc.get("_id"))) or {}).get("username") or ""),
                display_name=(user_meta.get(str(doc.get("_id"))) or {}).get("display_name"),
                tokens=int(doc.get("tokens", 0) or 0),
            )
            for doc in docs
            if doc.get("_id")
        ]

    async def _insights_fastest_growing_persona(
        self, filters: UsageFilters
    ) -> UsageInsightsFastestGrowingPersona | None:
        """环比增长最快的 Persona（按用户消息数，与 /usage/by-persona 同口径）。"""
        current_items = await self.get_usage_by_persona(filters)

        start_str, end_str = range_to_date_strings(filters.start, filters.end)
        prev_start_str, prev_end_str = previous_range(start_str, end_str)
        prev_start, prev_end = resolve_range(prev_start_str, prev_end_str)
        prev_filters = UsageFilters(
            start=_ensure_datetime(prev_start),
            end=_ensure_datetime(prev_end),
            persona_preset_id=filters.persona_preset_id,
            agent_id=filters.agent_id,
            role_user_ids=filters.role_user_ids,
        )
        previous_items = await self.get_usage_by_persona(prev_filters)
        prev_map = {
            item.persona_preset_id: item.user_messages
            for item in previous_items
            if item.persona_preset_id
        }

        best: tuple[float, int, UsageByPersonaItem, int] | None = None
        for item in current_items:
            if not item.persona_preset_id or item.user_messages <= 0:
                continue
            current = item.user_messages
            previous = prev_map.get(item.persona_preset_id, 0)
            growth = ((current - previous) / previous * 100.0) if previous > 0 else 100.0
            if best is None or (growth, current) > (best[0], best[1]):
                best = (growth, current, item, previous)

        if best is None:
            return None
        growth, current, item, previous = best
        return UsageInsightsFastestGrowingPersona(
            persona_preset_id=item.persona_preset_id or "",
            persona_preset_name=item.persona_preset_name,
            current=current,
            previous=previous,
            growth_pct=round(growth, 1),
        )

    async def _insights_new_users(self, filters: UsageFilters) -> int:
        """首次使用日（最早消息日）落在本期的人数。

        复用 ActivityStorage；role 筛选生效，persona/agent 筛选不适用
        （活跃记录无 persona 维度）。
        """
        if filters.role_user_ids is not None and not filters.role_user_ids:
            return 0

        start_str, end_str = range_to_date_strings(filters.start, filters.end)
        candidates = await self.activity_storage.distinct_users(
            start_str, end_str, source="message"
        )
        if filters.role_user_ids is not None:
            allowed = set(filters.role_user_ids)
            candidates = [uid for uid in candidates if uid in allowed]
        if not candidates:
            return 0

        first_dates = await self.activity_storage.first_message_date(candidates)
        count = 0
        for uid in candidates:
            first = first_dates.get(uid)
            if first and start_str <= first <= end_str:
                count += 1
        return count

    async def get_active_users_by_day(self, filters: UsageFilters) -> list[TrendDataPoint]:
        """按天统计活跃用户（区间内发过消息的用户），与概览卡同源。"""
        pipeline = usage_facts_stages(filters) + [
            {
                "$group": {
                    "_id": self._day_bucket_expr("$started_at"),
                    "active_user_ids": self._active_set_expr("$user_id"),
                }
            },
            {"$sort": {"_id": 1}},
        ]
        try:
            docs = await self.traces.aggregate(pipeline).to_list(length=None)
        except Exception as ex:
            logger.warning("get_active_users_by_day failed: %s", ex)
            return []
        return [
            TrendDataPoint(
                date=str(doc.get("_id") or ""),
                value=float(self._count_active(doc.get("active_user_ids"))),
            )
            for doc in docs
            if doc.get("_id")
        ]

    # ── Internals ────────────────────────────────────────────────

    async def _fan_out(
        self, jobs: list[tuple[Any, list[dict[str, Any]]]]
    ) -> tuple[Any, ...]:
        """并发执行多个独立聚合，返回每个 cursor 的列表副本。

        `motor` 不允许在跨任务之间共享 cursor，所以把每个 cursor 物化为 list。
        """

        async def _run(collection, pipeline):
            try:
                cursor = collection.aggregate(pipeline)
                return [doc async for doc in cursor]
            except Exception as e:
                logger.warning("Aggregation failed: %s | %s", collection.name, e)
                return []

        results = await asyncio.gather(*(_run(c, p) for c, p in jobs))
        return tuple(results)
