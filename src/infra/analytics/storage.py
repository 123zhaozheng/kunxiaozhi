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
        # Up vote rate (over feedback in range)
        feedback_stats_pipeline: list[dict[str, Any]] = [
            {"$match": self._date_range_query(s, e)},
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "up": {
                        "$sum": {
                            "$cond": [{"$eq": ["$rating", "up"]}, 1, 0]
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
            total_fb = int(feedback_stats[0].get("total", 0) or 0)
            up_fb = int(feedback_stats[0].get("up", 0) or 0)
            up_rate = round((up_fb / total_fb) * 100, 1) if total_fb > 0 else 0.0
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

        total_fb = up_count + down_total
        up_rate = round((up_count / total_fb) * 100, 1) if total_fb > 0 else 0.0

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
        up_percentage = round((up_count / total) * 100, 1) if total > 0 else 0.0
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

    async def list_sessions(
        self,
        start: datetime,
        end: datetime,
        preset_id: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> SessionListResponse:
        """会话明细列表（时间 + preset 筛选 + 分页）。"""
        s = _ensure_datetime(start)
        e = _ensure_datetime(end)
        query: dict[str, Any] = {"created_at": {"$gte": s, "$lte": e}}
        if preset_id:
            query["metadata.persona_preset_id"] = preset_id
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
        try:
            total = await self.sessions.count_documents(query)
            cursor = self.sessions.find(query, projection).sort("created_at", -1)
            docs = await cursor.skip(skip).limit(limit).to_list(length=limit)
        except Exception as ex:
            logger.warning("list sessions failed: %s", ex)
            return SessionListResponse(total=0, skip=skip, limit=limit, has_more=False)

        items: list[SessionListItem] = []
        for doc in docs:
            metadata = doc.get("metadata") or {}
            items.append(
                SessionListItem(
                    id=str(doc.get("session_id") or doc.get("_id") or ""),
                    name=doc.get("name"),
                    user_id=doc.get("user_id"),
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
