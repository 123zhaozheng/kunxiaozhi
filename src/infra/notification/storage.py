"""通知存储层"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from bson import ObjectId

from src.infra.logging import get_logger
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import ensure_utc, utc_now
from src.kernel.config import settings
from src.kernel.schemas.notification import (
    Notification,
    NotificationCreate,
    NotificationUpdate,
)

logger = get_logger(__name__)

NOTIFICATION_LIST_LIMIT_MAX = 100


def _bounded_limit(limit: int) -> int:
    return min(max(int(limit), 1), NOTIFICATION_LIST_LIMIT_MAX)


def _active_window_match(now: datetime) -> dict[str, Any]:
    return {
        "is_active": True,
        "$and": [
            {
                "$or": [
                    {"start_time": {"$exists": False}},
                    {"start_time": None},
                    {"start_time": {"$lte": now}},
                ]
            },
            {
                "$or": [
                    {"end_time": {"$exists": False}},
                    {"end_time": None},
                    {"end_time": {"$gte": now}},
                ]
            },
        ],
    }


def _dismissal_lookup(user_id: str) -> dict[str, Any]:
    return {
        "$lookup": {
            "from": "notification_dismissals",
            "let": {"notification_id": {"$toString": "$_id"}},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$notification_id", "$$notification_id"]},
                                {"$eq": ["$user_id", user_id]},
                            ]
                        }
                    }
                },
                {"$limit": 1},
            ],
            "as": "dismissals",
        }
    }


def _not_forever_dismissed_match() -> dict[str, Any]:
    """Keep undismissed rows and snooze-only rows. Legacy docs (no forever) count as forever."""
    return {
        "$match": {
            "$or": [
                {"dismissals": {"$eq": []}},
                {"dismissals.0.forever": False},
            ]
        }
    }


def _snooze_expired_or_absent_match(now: datetime) -> dict[str, Any]:
    return {
        "$match": {
            "$or": [
                {"dismissals": {"$eq": []}},
                {"dismissals.0.snooze_until": {"$exists": False}},
                {"dismissals.0.snooze_until": None},
                {"dismissals.0.snooze_until": {"$lte": now}},
            ]
        }
    }


def _is_snooze_active(dismissal: dict[str, Any], now: datetime) -> bool:
    snooze_until = dismissal.get("snooze_until")
    if not isinstance(snooze_until, datetime):
        return False
    return ensure_utc(snooze_until) > now


def _is_forever_dismissed(dismissal: dict[str, Any]) -> bool:
    """Legacy rows (no forever field) count as forever."""
    return dismissal.get("forever", True) is not False


def _keep_active_doc(doc: dict[str, Any]) -> bool:
    dismissals = doc.get("dismissals") or []
    if not dismissals:
        return True
    return not _is_forever_dismissed(dismissals[0])


def _compute_should_popup(doc: dict[str, Any], now: datetime) -> bool:
    if not bool(doc.get("popup")):
        return False
    dismissals = doc.get("dismissals") or []
    if not dismissals:
        return True
    dismissal = dismissals[0]
    if _is_forever_dismissed(dismissal):
        return False
    return not _is_snooze_active(dismissal, now)


def _to_notification(doc: dict[str, Any], now: datetime) -> Notification:
    doc["id"] = str(doc.pop("_id"))
    doc["popup"] = bool(doc.get("popup"))
    doc["should_popup"] = _compute_should_popup(doc, now)
    doc.pop("dismissals", None)
    return Notification.model_validate(doc)


class NotificationStorage:
    """通知存储"""

    def __init__(self):
        self._collection = None
        self._dismissal_collection = None

    @property
    def collection(self):
        if self._collection is None:
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            self._collection = db["notifications"]
        return self._collection

    @property
    def dismissal_collection(self):
        if self._dismissal_collection is None:
            client = get_mongo_client()
            db = client[settings.MONGODB_DB]
            self._dismissal_collection = db["notification_dismissals"]
        return self._dismissal_collection

    async def create_indexes(self) -> None:
        await self.collection.create_index([("created_at", -1)])
        await self.collection.create_index([("is_active", 1), ("created_at", -1)])
        await self.dismissal_collection.create_index(
            [("notification_id", 1), ("user_id", 1)], unique=True
        )
        logger.info("Notification indexes created")

    async def create(self, data: NotificationCreate, user_id: str) -> Notification:
        now = utc_now()
        doc = {
            "title_i18n": data.title_i18n.model_dump(),
            "content_i18n": data.content_i18n.model_dump(),
            "type": data.type.value if isinstance(data.type, Enum) else data.type,
            "start_time": data.start_time,
            "end_time": data.end_time,
            "is_active": data.is_active,
            "popup": data.popup,
            "created_at": now,
            "updated_at": now,
            "created_by": user_id,
        }
        result = await self.collection.insert_one(doc)
        doc["id"] = str(result.inserted_id)
        return Notification.model_validate(doc)

    async def get_by_id(self, notification_id: str) -> Optional[Notification]:
        try:
            doc = await self.collection.find_one({"_id": ObjectId(notification_id)})
            if doc:
                doc["id"] = str(doc.pop("_id"))
                return Notification.model_validate(doc)
            return None
        except Exception as e:
            logger.error(f"Error getting notification {notification_id}: {e}")
            return None

    async def list_notifications(
        self, skip: int = 0, limit: int = 50
    ) -> tuple[list[Notification], int]:
        limit = _bounded_limit(limit)
        total = await self.collection.count_documents({})
        cursor = self.collection.find().sort("created_at", -1).skip(skip).limit(limit)
        items = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            items.append(Notification.model_validate(doc))
        return items, total

    async def update(
        self, notification_id: str, data: NotificationUpdate
    ) -> Optional[Notification]:
        try:
            update_fields: dict = {"updated_at": utc_now()}
            provided = data.model_fields_set
            if "title_i18n" in provided and data.title_i18n is not None:
                update_fields["title_i18n"] = data.title_i18n.model_dump()
            if "content_i18n" in provided and data.content_i18n is not None:
                update_fields["content_i18n"] = data.content_i18n.model_dump()
            if "start_time" in provided:
                update_fields["start_time"] = data.start_time
            if "end_time" in provided:
                update_fields["end_time"] = data.end_time
            if "is_active" in provided:
                update_fields["is_active"] = data.is_active
            if "popup" in provided:
                update_fields["popup"] = data.popup

            result = await self.collection.find_one_and_update(
                {"_id": ObjectId(notification_id)},
                {"$set": update_fields},
                return_document=True,
            )
            if result:
                result["id"] = str(result.pop("_id"))
                return Notification.model_validate(result)
            return None
        except Exception as e:
            logger.error(f"Error updating notification {notification_id}: {e}")
            return None

    async def delete(self, notification_id: str) -> bool:
        try:
            result = await self.collection.delete_one({"_id": ObjectId(notification_id)})
            if result.deleted_count > 0:
                await self.dismissal_collection.delete_many({"notification_id": notification_id})
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting notification {notification_id}: {e}")
            return False

    async def get_active_notifications(self, user_id: str, limit: int = 5) -> list[Notification]:
        """Get active notifications that the user hasn't forever-dismissed, sorted by created_at desc."""
        now = utc_now()
        limit = _bounded_limit(limit)

        pipeline = [
            {"$match": _active_window_match(now)},
            {"$sort": {"created_at": -1}},
            _dismissal_lookup(user_id),
            _not_forever_dismissed_match(),
            {"$limit": limit},
        ]

        cursor = self.collection.aggregate(pipeline)
        results = []
        async for doc in cursor:
            if not _keep_active_doc(doc):
                continue
            results.append(_to_notification(doc, now))
        return results

    async def get_popup_eligible_notifications(
        self, user_id: str, limit: int = NOTIFICATION_LIST_LIMIT_MAX
    ) -> list[Notification]:
        """Active popup notifications eligible to auto-open, not capped at the bell limit."""
        now = utc_now()
        limit = _bounded_limit(limit)
        match = _active_window_match(now)
        match["popup"] = True

        pipeline = [
            {"$match": match},
            {"$sort": {"created_at": -1}},
            _dismissal_lookup(user_id),
            _not_forever_dismissed_match(),
            _snooze_expired_or_absent_match(now),
            {"$limit": limit},
        ]

        cursor = self.collection.aggregate(pipeline)
        results = []
        async for doc in cursor:
            if not _compute_should_popup(doc, now):
                continue
            results.append(_to_notification(doc, now))
        return results

    async def dismiss(
        self,
        notification_id: str,
        user_id: str,
        snooze_until: datetime | None = None,
    ) -> bool:
        try:
            now = utc_now()
            if snooze_until is not None:
                update_fields = {
                    "dismissed_at": now,
                    "forever": False,
                    "snooze_until": ensure_utc(snooze_until),
                }
            else:
                update_fields = {
                    "dismissed_at": now,
                    "forever": True,
                    "snooze_until": None,
                }
            await self.dismissal_collection.update_one(
                {"notification_id": notification_id, "user_id": user_id},
                {"$set": update_fields},
                upsert=True,
            )
            return True
        except Exception as e:
            logger.error(f"Error dismissing notification: {e}")
            return False
