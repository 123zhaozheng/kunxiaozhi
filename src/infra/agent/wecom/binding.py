"""企业微信通知绑定存储层

记录"目标用户 → 机器人"的绑定关系（用户先给 bot 发消息建立会话后，
才能由 bot 主动推送消息）。绑定关系存于 MongoDB collection wecom_notify_bindings。
"""

from __future__ import annotations

from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

logger = get_logger(__name__)

_BINDINGS_COLLECTION = "wecom_notify_bindings"


class WeComNotifyBindingStorage:
    """企业微信通知绑定存储（aibotid + username → 绑定时间）"""

    def __init__(self):
        self._collection = None

    @property
    def collection(self):
        if self._collection is None:
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            self._collection = db[_BINDINGS_COLLECTION]
        return self._collection

    async def create_indexes(self) -> None:
        """创建唯一索引 (aibotid, username)，防止重复绑定"""
        await self.collection.create_index(
            [("aibotid", 1), ("username", 1)], unique=True
        )
        logger.info("WeCom notify binding indexes created")

    async def upsert(self, aibotid: str, username: str) -> bool:
        """记录绑定关系（幂等：重复绑定不改变绑定时间）"""
        try:
            await self.collection.update_one(
                {"aibotid": aibotid, "username": username},
                {
                    "$setOnInsert": {
                        "aibotid": aibotid,
                        "username": username,
                        "bound_at": utc_now(),
                    }
                },
                upsert=True,
            )
            return True
        except Exception as e:
            logger.error(f"Error upserting WeCom notify binding {aibotid}/{username}: {e}")
            return False

    async def is_bound(self, aibotid: str, username: str) -> bool:
        """查询指定用户是否已绑定该机器人"""
        try:
            doc = await self.collection.find_one(
                {"aibotid": aibotid, "username": username}, {"_id": 1}
            )
            return doc is not None
        except Exception as e:
            logger.error(f"Error checking WeCom notify binding {aibotid}/{username}: {e}")
            return False

    async def list_bound(self, aibotid: str, usernames: list[str]) -> set[str]:
        """返回 usernames 中已绑定该机器人的子集"""
        if not usernames:
            return set()
        try:
            cursor = self.collection.find(
                {"aibotid": aibotid, "username": {"$in": usernames}},
                {"username": 1},
            )
            bound: set[str] = set()
            async for doc in cursor:
                bound.add(doc["username"])
            return bound
        except Exception as e:
            logger.error(f"Error listing WeCom notify bindings for {aibotid}: {e}")
            return set()
