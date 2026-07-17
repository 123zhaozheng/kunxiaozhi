"""
Analytics Storage - 聚合统计查询

使用 MongoDB aggregation pipeline 汇总用户、会话、token、反馈相关指标。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

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
)

logger = get_logger(__name__)

_TOKEN_USAGE_EVENT = "token:usage"
_TOP_PRESET_LIMIT = 10
# 分桶时区：按东八区（Asia/Shanghai）日期聚合趋势/热力图。
# 注意：仅用于 $dateToString/$dayOfWeek/$hour 分桶；$match 的 $gte/$lte 比较仍用 UTC 瞬时。
_BUCKET_TZ = "Asia/Shanghai"


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
    ) -> OverviewResponse:
        """概览卡片数据（4 个指标）"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)

        # Active users: count distinct user_id with updated_at in range
        active_users_pipeline: list[dict[str, Any]] = [
            {"$match": {"updated_at": {"$gte": s, "$lte": e}}},
            {"$group": {"_id": "$_id"}},
            {"$count": "value"},
        ]
        # Total sessions
        total_sessions_pipeline: list[dict[str, Any]] = [
            {"$match": self._date_range_query(s, e)},
            {"$count": "value"},
        ]
        # Total tokens: unwind token events + sum total_tokens
        total_tokens_pipeline: list[dict[str, Any]] = [
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
                    "_id": None,
                    "value": {
                        "$sum": {
                            "$ifNull": ["$events.data.total_tokens", 0],
                        }
                    },
                }
            },
        ]
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

        active_users, sessions_count, tokens_count, feedback_stats = await self._fan_out(
            [
                (self.users, active_users_pipeline),
                (self.sessions, total_sessions_pipeline),
                (self.traces, total_tokens_pipeline),
                (self.feedback, feedback_stats_pipeline),
            ]
        )

        active_users_total = active_users[0]["value"] if active_users else 0
        sessions_total = sessions_count[0]["value"] if sessions_count else 0
        tokens_total = int(tokens_count[0]["value"]) if tokens_count else 0
        if feedback_stats:
            up_fb = int(feedback_stats[0].get("up", 0) or 0)
            down_fb = int(feedback_stats[0].get("down", 0) or 0)
            up_rate = _compute_up_vote_rate(up_fb, down_fb)
        else:
            up_rate = 0.0

        return OverviewResponse(
            active_users=int(active_users_total),
            total_sessions=int(sessions_total),
            total_tokens=tokens_total,
            up_vote_rate=up_rate,
        )

    async def get_active_users_trend(
        self,
        start: datetime,
        end: datetime,
    ) -> list[TrendDataPoint]:
        """按天统计活跃用户数。基于用户的 updated_at。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        pipeline: list[dict[str, Any]] = [
            {"$match": {"updated_at": {"$gte": s, "$lte": e}}},
            {
                "$group": {
                    "_id": self._day_bucket_expr("$updated_at"),
                    "value": {"$addToSet": "$_id"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$_id",
                    "value": {"$size": "$value"},
                }
            },
            {"$sort": {"date": 1}},
        ]
        out: list[TrendDataPoint] = []
        async for doc in self.users.aggregate(pipeline):
            out.append(
                TrendDataPoint(date=str(doc.get("date", "")), value=float(doc.get("value", 0)))
            )
        return out

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
    ) -> SessionsTrendResponse:
        """会话/消息趋势：sessions 集合的创建数 + traces 的事件计数总量。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        sessions_pipeline: list[dict[str, Any]] = [
            {"$match": self._date_range_query(s, e)},
            {
                "$group": {
                    "_id": self._day_bucket_expr("$created_at"),
                    "value": {"$sum": 1},
                }
            },
            {"$project": {"_id": 0, "date": "$_id", "value": 1}},
            {"$sort": {"date": 1}},
        ]
        messages_pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "started_at": {"$gte": s, "$lte": e},
                    "event_count": {"$exists": True, "$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": self._day_bucket_expr("$started_at"),
                    "value": {"$sum": "$event_count"},
                }
            },
            {"$project": {"_id": 0, "date": "$_id", "value": 1}},
            {"$sort": {"date": 1}},
        ]

        sessions_docs, messages_docs = await self._fan_out(
            [
                (self.sessions, sessions_pipeline),
                (self.traces, messages_pipeline),
            ]
        )

        sessions_trend = [
            TrendDataPoint(date=str(d.get("date", "")), value=float(d.get("value", 0)))
            for d in sessions_docs
        ]
        messages_trend = [
            TrendDataPoint(date=str(d.get("date", "")), value=float(d.get("value", 0)))
            for d in messages_docs
        ]
        total_sessions = sum(int(item.value) for item in sessions_trend)
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
        """单角色智能体完整指标：基础 4 指标 + 点赞率 + 点踩原因分布。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        preset_match = {"metadata.persona_preset_id": preset_id}

        # total_tokens（traces，E1 后）
        total_tokens_pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    **preset_match,
                    "events.event_type": _TOKEN_USAGE_EVENT,
                    "started_at": {"$gte": s, "$lte": e},
                }
            },
            {"$unwind": "$events"},
            {"$match": {"events.event_type": _TOKEN_USAGE_EVENT}},
            {
                "$group": {
                    "_id": None,
                    "value": {
                        "$sum": {"$ifNull": ["$events.data.total_tokens", 0]}
                    },
                }
            },
        ]
        # total_messages（traces，event_count 求和）
        total_messages_pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    **preset_match,
                    "started_at": {"$gte": s, "$lte": e},
                    "event_count": {"$exists": True, "$gt": 0},
                }
            },
            {"$group": {"_id": None, "value": {"$sum": "$event_count"}}},
        ]
        # total_sessions + active_users（sessions，一次 $group 出两值）
        sessions_pipeline: list[dict[str, Any]] = [
            {"$match": {**preset_match, "created_at": {"$gte": s, "$lte": e}}},
            {
                "$group": {
                    "_id": None,
                    "total_sessions": {"$sum": 1},
                    "active_user_ids": {"$addToSet": "$user_id"},
                }
            },
        ]

        tokens_docs, messages_docs, sessions_docs = await self._fan_out(
            [
                (self.traces, total_tokens_pipeline),
                (self.traces, total_messages_pipeline),
                (self.sessions, sessions_pipeline),
            ]
        )

        total_tokens = int(tokens_docs[0]["value"]) if tokens_docs else 0
        total_messages = int(messages_docs[0]["value"]) if messages_docs else 0
        total_sessions = 0
        active_users = 0
        if sessions_docs:
            total_sessions = int(sessions_docs[0].get("total_sessions", 0) or 0)
            active_user_ids = sessions_docs[0].get("active_user_ids") or []
            active_users = len(active_user_ids)

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
            total_messages=total_messages,
            total_sessions=total_sessions,
            active_users=active_users,
            total_tokens=total_tokens,
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
        """活跃用户列表（按区间内会话聚合）。

        活跃定义：区间内有会话记录的用户。
        sort:
          - frequency: 按会话数降序（默认）
          - recent: 按最近会话时间降序
        """
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        role_user_ids: list[str] | None = None
        if role_id:
            role_user_ids = await self._session_user_ids_for_role(role_id, s, e)

        match = self._build_session_query(
            s,
            e,
            persona_preset_id=persona_preset_id,
            agent_id=agent_id,
            role_user_ids=role_user_ids,
        )
        # 活跃用户必须有 user_id；角色过滤时已写入 $in
        if "user_id" not in match:
            match["user_id"] = {"$exists": True, "$nin": [None, ""]}

        sort_mode = (sort or "frequency").lower()
        sort_stage = (
            {"$sort": {"last_active_at": -1, "session_count": -1}}
            if sort_mode == "recent"
            else {"$sort": {"session_count": -1, "last_active_at": -1}}
        )
        pipeline: list[dict[str, Any]] = [
            {"$match": match},
            {
                "$group": {
                    "_id": "$user_id",
                    "session_count": {"$sum": 1},
                    "last_active_at": {"$max": "$created_at"},
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
            facet_docs = await self.sessions.aggregate(pipeline).to_list(length=1)
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
