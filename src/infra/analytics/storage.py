"""
Analytics Storage - 聚合统计查询

使用 MongoDB aggregation pipeline 汇总用户、会话、token、反馈相关指标。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from src.infra.analytics.date_range import _BUCKET_TZ
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
    HeatmapCell,
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
    UsageSummaryResponse,
    UsageTrendPoint,
)

logger = get_logger(__name__)

_TOKEN_USAGE_EVENT = "token:usage"
_TOP_PRESET_LIMIT = 10


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

    # ── Indexes ─────────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """为支撑聚合查询创建/补足索引。"""
        try:
            await self.feedback.create_index([("created_at", -1)], background=True)
            await self.sessions.create_index([("created_at", -1)], background=True)
            # users.updated_at 用于活跃用户判定（自动 id-based 索引辅助）
            await self.users.create_index([("updated_at", -1)], background=True)
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
        return {"created_at": {"$gte": start, "$lte": end}}

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

    async def get_users_heatmap(
        self,
        start: datetime,
        end: datetime,
    ) -> list[HeatmapCell]:
        """星期 × 小时 热力图。基于 sessions 的 created_at。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "created_at": {"$gte": s, "$lte": e},
                    "user_id": {"$ne": None, "$exists": True},
                }
            },
            {
                "$group": {
                    "_id": {
                        "weekday": {
                            "$dayOfWeek": {
                                "date": "$created_at",
                                "timezone": _BUCKET_TZ,
                            }
                        },
                        "hour": {
                            "$hour": {
                                "date": "$created_at",
                                "timezone": _BUCKET_TZ,
                            }
                        },
                    },
                    "count": {"$sum": 1},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "weekday": {
                        "$subtract": ["$_id.weekday", 1]
                    },  # $dayOfWeek returns 1..7
                    "hour": "$_id.hour",
                    "count": 1,
                }
            },
        ]
        cells: list[HeatmapCell] = []
        async for doc in self.sessions.aggregate(pipeline):
            cells.append(
                HeatmapCell(
                    weekday=int(doc.get("weekday", 0)),
                    hour=int(doc.get("hour", 0)),
                    count=int(doc.get("count", 0)),
                )
            )
        return cells

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
    ) -> list[ByLabelItem]:
        """按模型聚合 token 消耗。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "events.event_type": _TOKEN_USAGE_EVENT,
                    "started_at": {"$gte": s, "$lte": e},
                }
            },
            {"$unwind": "$events"},
            {"$match": {"events.event_type": _TOKEN_USAGE_EVENT}},
            {
                "$group": {
                    "_id": {
                        "$ifNull": [
                            "$events.data.model_id",
                            "$events.data.model",
                            "unknown",
                        ]
                    },
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
        out: list[ByLabelItem] = []
        async for doc in self.traces.aggregate(pipeline):
            out.append(
                ByLabelItem(
                    label=str(doc.get("label", "unknown")), value=float(doc.get("value", 0))
                )
            )
        return out

    async def get_tokens_by_preset(
        self,
        start: datetime,
        end: datetime,
        limit: int = _TOP_PRESET_LIMIT,
    ) -> list[ByLabelItem]:
        """按 Agent 类型聚合 token 消耗，Top N。

        traces.agent_id 存的是 Agent factory ID（如 "search"/"fast"/"team"），
        无法关联到 persona_presets（后者无 agent_id 字段），故降级为按 Agent 类型聚合。
        label 直接用 agent_id 字符串。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "events.event_type": _TOKEN_USAGE_EVENT,
                    "started_at": {"$gte": s, "$lte": e},
                    "agent_id": {"$exists": True, "$ne": None},
                }
            },
            {"$unwind": "$events"},
            {"$match": {"events.event_type": _TOKEN_USAGE_EVENT}},
            {
                "$group": {
                    "_id": "$agent_id",
                    "value": {
                        "$sum": {
                            "$ifNull": ["$events.data.total_tokens", 0],
                        }
                    },
                }
            },
            {"$project": {"_id": 0, "label": "$_id", "value": 1}},
            {"$sort": {"value": -1}},
            {"$limit": max(int(limit), 1)},
        ]
        out: list[ByLabelItem] = []
        async for doc in self.traces.aggregate(pipeline):
            out.append(
                ByLabelItem(
                    label=str(doc.get("label", "") or "—"), value=float(doc.get("value", 0))
                )
            )
        return out

    async def get_sessions_by_agent(
        self,
        start: datetime,
        end: datetime,
        limit: int = _TOP_PRESET_LIMIT,
    ) -> list[ByLabelItem]:
        """按 agent_id 聚合会话数（by-agent 维度）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {"$match": {"created_at": {"$gte": s, "$lte": e}}},
            {
                "$group": {
                    "_id": {"$ifNull": ["$agent_id", "default"]},
                    "value": {"$sum": 1},
                }
            },
            {"$project": {"_id": 0, "label": "$_id", "value": 1}},
            {"$sort": {"value": -1}},
            {"$limit": max(int(limit), 1)},
        ]
        out: list[ByLabelItem] = []
        try:
            async for doc in self.sessions.aggregate(pipeline):
                out.append(
                    ByLabelItem(
                        label=str(doc.get("label", "") or "—"),
                        value=float(doc.get("value", 0)),
                    )
                )
        except Exception as ex:
            logger.warning("get_sessions_by_agent failed: %s", ex)
        return out

    async def get_sessions_by_persona(
        self,
        start: datetime,
        end: datetime,
        limit: int = _TOP_PRESET_LIMIT,
    ) -> list[ByLabelItem]:
        """按 persona_preset_id 聚合会话数（by-persona 维度）。

        label 优先使用 metadata.persona_preset_name，缺失时回退 preset_id。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "created_at": {"$gte": s, "$lte": e},
                    "metadata.persona_preset_id": {"$exists": True, "$nin": [None, ""]},
                }
            },
            {
                "$group": {
                    "_id": "$metadata.persona_preset_id",
                    "name": {"$first": "$metadata.persona_preset_name"},
                    "value": {"$sum": 1},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "id": "$_id",
                    "label": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$ne": ["$name", None]},
                                    {"$ne": ["$name", ""]},
                                ]
                            },
                            "$name",
                            "$_id",
                        ]
                    },
                    "value": 1,
                }
            },
            {"$sort": {"value": -1}},
            {"$limit": max(int(limit), 1)},
        ]
        out: list[ByLabelItem] = []
        try:
            async for doc in self.sessions.aggregate(pipeline):
                preset_id = doc.get("id")
                out.append(
                    ByLabelItem(
                        label=str(doc.get("label", "") or "—"),
                        value=float(doc.get("value", 0)),
                        id=str(preset_id) if preset_id else None,
                    )
                )
        except Exception as ex:
            logger.warning("get_sessions_by_persona failed: %s", ex)
        return out

    async def get_tokens_trend(
        self,
        start: datetime,
        end: datetime,
    ) -> list[TrendDataPoint]:
        """按天统计 token 消耗。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "events.event_type": _TOKEN_USAGE_EVENT,
                    "started_at": {"$gte": s, "$lte": e},
                }
            },
            {"$unwind": "$events"},
            {"$match": {"events.event_type": _TOKEN_USAGE_EVENT}},
            {
                "$group": {
                    "_id": self._day_bucket_expr("$started_at"),
                    "value": {
                        "$sum": {"$ifNull": ["$events.data.total_tokens", 0]}
                    },
                }
            },
            {"$project": {"_id": 0, "date": "$_id", "value": 1}},
            {"$sort": {"date": 1}},
        ]
        out: list[TrendDataPoint] = []
        async for doc in self.traces.aggregate(pipeline):
            out.append(
                TrendDataPoint(
                    date=str(doc.get("date", "")), value=float(doc.get("value", 0))
                )
            )
        return out

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
                    "created_at": {"$gte": s, "$lte": e},
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
                        "created_at": {"$gte": s, "$lte": e},
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
            {"$match": {"created_at": {"$gte": s, "$lte": e}}},
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
                    "created_at": {"$gte": s, "$lte": e},
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
                    "created_at": {"$gte": s, "$lte": e},
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
        """在时间范围内，返回持有指定 RBAC 角色的 user_id 列表。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        try:
            session_user_ids = await self.sessions.distinct(
                "user_id",
                {
                    "created_at": {"$gte": s, "$lte": e},
                    "user_id": {"$exists": True, "$nin": [None, ""]},
                },
            )
        except Exception as ex:
            logger.warning("distinct session user_ids failed: %s", ex)
            return []
        cleaned = [str(uid) for uid in session_user_ids if uid]
        if not cleaned:
            return []
        object_ids = _user_object_ids(cleaned)
        if not object_ids:
            return []
        try:
            cursor = self.users.find(
                {"_id": {"$in": object_ids}, "roles": role_id},
                {"_id": 1},
            )
            docs = await cursor.to_list(length=len(object_ids))
            return [str(doc["_id"]) for doc in docs if doc.get("_id") is not None]
        except Exception as ex:
            logger.warning("filter users by role failed: %s", ex)
            return []

    def _build_session_query(
        self,
        start: datetime,
        end: datetime,
        *,
        preset_id: str | None = None,
        persona_preset_id: str | None = None,
        agent_id: str | None = None,
        role_user_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """构建会话列表查询条件。

        兼容旧参数 preset_id；新参数 persona_preset_id 优先。
        role_user_ids 为 None 表示不按角色过滤；空列表表示无匹配用户。
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        query: dict[str, Any] = {"created_at": {"$gte": s, "$lte": e}}
        effective_preset = persona_preset_id or preset_id
        if effective_preset:
            query["metadata.persona_preset_id"] = effective_preset
        if agent_id:
            query["agent_id"] = agent_id
        if role_user_ids is not None:
            query["user_id"] = {"$in": role_user_ids}
        return query

    async def list_sessions(
        self,
        start: datetime,
        end: datetime,
        preset_id: str | None = None,
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
            preset_id=preset_id,
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
    ) -> ActiveUserListResponse:
        """活跃用户列表。

        活跃定义：区间内发过 user:message 的用户，与 /overview 的 active_users
        同源，因此本列表的 total 必然等于概览卡片数字。
        session_count 为该用户在区间内有消息往来的会话去重数。
        sort:
          - frequency: 按会话数降序（默认）
          - recent: 按最近活跃时间降序
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
                    "session_count": {"$size": "$active_session_ids"},
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
        query: dict[str, Any] = {"created_at": {"$gte": s, "$lte": e}}
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
    ) -> RunListResponse:
        """运行明细列表（含 token 用量，分页）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        query: dict[str, Any] = {"started_at": {"$gte": s, "$lte": e}}
        if preset_id:
            query["metadata.persona_preset_id"] = preset_id
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

    async def get_usage_summary(self, filters: UsageFilters) -> UsageSummaryResponse:
        """使用情况汇总：活跃用户 / 新建会话 / 活跃会话 / 用户消息 / token。"""
        try:
            result = await read_or_freeze(filters, storage=self)

            # Extract metrics from merge result
            total = result.get("total", {})
            active_users = result.get("active_users", 0)
            using_users = result.get("using_users", 0)

            summary = UsageSummaryResponse(
                active_users=active_users,
                using_users=using_users,
                new_sessions=int(total.get("new_sessions", 0)),
                active_sessions=int(total.get("active_sessions", 0)),
                user_messages=int(total.get("user_messages", 0)),
                total_tokens=int(total.get("tokens", 0)),
            )
            # If snapshot layer returned all zeros, it may have failed silently.
            # Fall through to direct real-time computation as safety net.
            if summary.user_messages == 0 and summary.total_tokens == 0 and summary.new_sessions == 0:
                raise ValueError("Snapshot layer returned empty result, falling back to real-time")
            return summary
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
                return UsageSummaryResponse()

            doc = docs[0] if docs else {}
            return UsageSummaryResponse(
                active_users=self._count_active(doc.get("active_user_ids")),
                new_sessions=int(new_sessions or 0),
                active_sessions=self._count_active(doc.get("active_session_ids")),
                user_messages=int(doc.get("user_messages", 0) or 0),
                total_tokens=int(doc.get("total_tokens", 0) or 0),
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

    async def list_usage_by_user(
        self,
        filters: UsageFilters,
        skip: int = 0,
        limit: int = 20,
    ) -> UsageByUserResponse:
        """使用明细，行粒度为「用户 × Persona」，仅含区间内发过消息的用户。"""
        # Note: Snapshot layer does not yet support list-level pagination.
        # This remains a real-time query path. Will be integrated in future iteration
        # when snapshot supports granular user-level filtering with pagination.

        facts_pipeline = usage_facts_stages(filters) + [
            {
                "$group": {
                    "_id": {
                        "user_id": "$user_id",
                        "persona_preset_id": "$persona_preset_id",
                    },
                    "persona_preset_name": {"$first": "$persona_preset_name"},
                    "user_messages": {"$sum": "$user_messages"},
                    "total_tokens": {"$sum": "$tokens"},
                    "active_session_ids": self._active_set_expr("$session_id"),
                    "last_active_at": {"$max": "$started_at"},
                }
            },
            {"$match": {"user_messages": {"$gt": 0}, "_id.user_id": {"$nin": [None, ""]}}},
            {
                "$facet": {
                    "total": [{"$count": "count"}],
                    "items": [
                        {"$sort": {"user_messages": -1, "last_active_at": -1}},
                        {"$skip": skip},
                        {"$limit": limit},
                    ],
                }
            },
        ]
        new_sessions_pipeline: list[dict[str, Any]] = [
            {"$match": new_sessions_match(filters)},
            {
                "$group": {
                    "_id": {
                        "user_id": "$user_id",
                        "persona_preset_id": "$metadata.persona_preset_id",
                    },
                    "new_sessions": {"$sum": 1},
                }
            },
        ]

        facts_docs, new_sessions_docs = await self._fan_out(
            [
                (self.traces, facts_pipeline),
                (self.sessions, new_sessions_pipeline),
            ]
        )

        facet = facts_docs[0] if facts_docs else {}
        total_arr = facet.get("total") or []
        total = int(total_arr[0].get("count", 0)) if total_arr else 0
        raw_items = facet.get("items") or []

        new_sessions_by_key: dict[tuple[str, str], int] = {}
        for doc in new_sessions_docs:
            key = doc.get("_id") or {}
            new_sessions_by_key[
                (str(key.get("user_id") or ""), str(key.get("persona_preset_id") or ""))
            ] = int(doc.get("new_sessions", 0) or 0)

        user_ids = [
            str((doc.get("_id") or {}).get("user_id"))
            for doc in raw_items
            if (doc.get("_id") or {}).get("user_id")
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
            str((doc.get("_id") or {}).get("persona_preset_id"))
            for doc in raw_items
            if (doc.get("_id") or {}).get("persona_preset_id")
            and not doc.get("persona_preset_name")
        ]
        names = await self._preset_names(missing_name_ids)

        items: list[UsageByUserItem] = []
        for doc in raw_items:
            key = doc.get("_id") or {}
            uid = str(key.get("user_id") or "")
            preset_id = key.get("persona_preset_id")
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
                    new_sessions=new_sessions_by_key.get((uid, preset_id_str or ""), 0),
                    active_sessions=self._count_active(doc.get("active_session_ids")),
                    user_messages=int(doc.get("user_messages", 0) or 0),
                    total_tokens=int(doc.get("total_tokens", 0) or 0),
                    last_active_at=doc.get("last_active_at"),
                )
            )

        has_more = (skip + len(items)) < total
        return UsageByUserResponse(
            items=items, total=total, skip=skip, limit=limit, has_more=has_more
        )

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
