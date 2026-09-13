"""
用户日活跃记录 Storage

负责 `user_daily_activity` 集合的 CRUD 操作，记录用户的登录和发消息行为。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorCollection

from src.infra.analytics.date_range import CST
from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.analytics_activity import Activity

logger = get_logger(__name__)


class ActivityStorage:
    """用户日活跃记录 MongoDB Storage"""

    def __init__(self):
        self._collection: Optional[AsyncIOMotorCollection] = None

    # ── Collection accessors ────────────────────────────────────────

    @property
    def collection(self) -> AsyncIOMotorCollection:
        if self._collection is None:
            db = get_mongo_client()[settings.MONGODB_DB]
            self._collection = db["user_daily_activity"]
        return self._collection

    # ── Indexes ─────────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """确保索引存在：(user_id, date) 唯一索引；(date, user_id) 查询索引"""
        from pymongo.operations import IndexModel

        indexes_to_create = [
            IndexModel(
                [("user_id", 1), ("date", 1)],
                name="user_id_date_unique",
                unique=True,
            ),
            IndexModel([("date", 1), ("user_id", 1)], name="date_user_id_idx"),
        ]

        for index in indexes_to_create:  # type: ignore[arg-type]
            try:
                await self.collection.create_index(index, background=True)  # type: ignore[arg-type]
            except Exception as e:
                # Index already exists is not an error
                if "duplicate key" not in str(e).lower() and "index already exists" not in str(e).lower():
                    logger.warning(f"Failed to create index {index}: {e}")

    # ── Public API ──────────────────────────────────────────────────

    async def record(self, user_id: str, source: str, at: Optional[datetime] = None) -> None:
        """
        记录用户活跃度（upsert）。

        Args:
            user_id: 用户 ID
            source: 来源 ('login' | 'message')
            at: 事件发生时间，默认当前 UTC 时间
        """
        at = at or utc_now()
        date_cst = at.astimezone(CST).strftime("%Y-%m-%d")

        try:
            await self.collection.update_one(
                {"user_id": user_id, "date": date_cst},
                {
                    "$setOnInsert": {"first_at": at, "user_id": user_id, "date": date_cst},
                    "$addToSet": {"sources": source},
                    "$set": {"last_at": at},
                },
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"Failed to record activity for user={user_id}, source={source}: {e}")

    async def distinct_users(
        self, start: str, end: str, source: Optional[str] = None
    ) -> list[str]:
        """
        查询区间内的去重用户列表。

        Args:
            start: 开始日期 (YYYY-MM-DD)
            end: 结束日期 (YYYY-MM-DD)
            source: 可选筛选来源 ('login' | 'message')

        Returns:
            用户 ID 列表
        """
        pipeline: list[dict] = [
            {
                "$match": {
                    "date": {
                        "$gte": start,
                        "$lte": end,
                    },
                }
            },
        ]

        if source:
            pipeline.append({"$match": {"sources": source}})

        pipeline.extend(
            [  # type: ignore[list-item]
                {"$group": {"_id": "$user_id"}},
                {"$sort": {"_id": 1}},
                {"$project": {"_id": 1}},
            ]
        )

        try:
            cursor = self.collection.aggregate(pipeline)  # type: ignore[arg-type]
            users: list[str] = []
            async for doc in cursor:
                users.append(doc["_id"])
            return users
        except Exception as e:
            logger.warning(f"Failed to query distinct users: {e}")
            return []

    async def first_message_date(
        self, user_ids: Optional[list[str]] = None
    ) -> dict[str, str]:
        """
        查询每个用户首次出现 "message" 的日期。

        Args:
            user_ids: 可选用户列表，为空则查所有

        Returns:
            {user_id: first_message_date}
        """
        filter_query = {}
        if user_ids:
            filter_query["user_id"] = {"$in": user_ids}

        # Match documents containing 'message' source, then group and find min date
        filter_query_with_source: dict = {"sources": "message"}
        if user_ids:
            filter_query_with_source["user_id"] = {"$in": user_ids}

        pipeline_list: list[dict] = [
            {"$match": filter_query_with_source},
            {"$group": {"_id": "$user_id", "first_date": {"$min": "$date"}}},
            {"$project": {"_id": 1, "first_date": 1}},
        ]

        try:
            cursor = self.collection.aggregate(pipeline_list)  # type: ignore[arg-type]
            result = {}
            async for doc in cursor:
                result[doc["_id"]] = doc["first_date"]
            return result
        except Exception as e:
            logger.warning(f"Failed to query first message date: {e}")
            return {}

    async def get_by_user_and_date(self, user_id: str, date: str) -> Optional[Activity]:
        """根据用户 ID 和日期获取记录"""
        try:
            doc = await self.collection.find_one({"user_id": user_id, "date": date})
            if doc:
                doc["_id"] = str(doc["_id"])
                return Activity(**doc)
            return None
        except Exception as e:
            logger.warning(f"Failed to get activity by user_id={user_id}, date={date}: {e}")
            return None

    async def list_by_date_range(
        self, start: str, end: str, limit: int = 1000, skip: int = 0
    ) -> list[Activity]:
        """按日期范围列出记录"""
        try:
            cursor = self.collection.find(
                {"date": {"$gte": start, "$lte": end}},
                sort=[("date", -1), ("user_id", 1)],
            ).skip(skip).limit(limit)

            result = []
            async for doc in cursor:
                doc["_id"] = str(doc["_id"])
                result.append(Activity(**doc))
            return result
        except Exception as e:
            logger.warning(f"Failed to list activities: {e}")
            return []


async def record_message_activity(user_id: str, at: Optional[datetime] = None) -> None:
    """Record a message best-effort from every message ingestion path."""
    try:
        await ActivityStorage().record(user_id, "message", at=at)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to record message activity for user=%s: %s", user_id, exc)
