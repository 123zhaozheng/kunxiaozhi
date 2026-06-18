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
    HeatmapCell,
    OverviewResponse,
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
