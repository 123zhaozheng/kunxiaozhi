from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId

from src.infra.notification.storage import NotificationStorage
from src.infra.utils.datetime import utc_now

NOTIFICATION_LIST_LIMIT_MAX = 100


def _notification_doc(
    notification_id: ObjectId,
    *,
    title: str = "title",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    popup: bool | None = None,
) -> dict[str, Any]:
    now = utc_now()
    doc = {
        "_id": notification_id,
        "title_i18n": {"en": title, "zh": title, "ja": title, "ko": title, "ru": title},
        "content_i18n": {"en": "body", "zh": "body", "ja": "body", "ko": "body", "ru": "body"},
        "type": "info",
        "start_time": start_time,
        "end_time": end_time,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "created_by": "admin",
    }
    if popup is not None:
        doc["popup"] = popup
    return doc


class _AggregateCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _NotificationCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.pipeline: list[dict[str, Any]] | None = None

    def aggregate(self, pipeline: list[dict[str, Any]]):
        self.pipeline = pipeline
        return _AggregateCursor([dict(doc) for doc in self.docs])


class _FindCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self.skip_value: int | None = None
        self.limit_value: int | None = None

    def sort(self, *_args):
        return self

    def skip(self, value: int):
        self.skip_value = value
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def __aiter__(self):
        cap = self.limit_value
        self._iter = iter(self._docs[: cap or None])
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _ListNotificationCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.cursor = _FindCursor([dict(doc) for doc in docs])

    async def count_documents(self, _query: dict[str, Any]) -> int:
        return 1_000

    def find(self):
        return self.cursor


class _DismissalCollection:
    async def distinct(self, *_args, **_kwargs):
        raise AssertionError("get_active_notifications should not materialize all dismissals")


class _DismissalWriteCollection:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []

    async def update_one(self, filt: dict[str, Any], update: dict[str, Any], upsert: bool = False):
        self.calls.append((filt, update, upsert))


@pytest.mark.asyncio
async def test_active_notifications_filters_in_mongo_without_distinct() -> None:
    storage = NotificationStorage()
    notification_id = ObjectId()
    storage._collection = _NotificationCollection([_notification_doc(notification_id)])
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_active_notifications("user-1", limit=3)

    assert [item.id for item in items] == [str(notification_id)]
    pipeline = storage._collection.pipeline
    assert pipeline is not None
    assert pipeline[0]["$match"]["is_active"] is True
    now = utc_now()
    start_conditions = pipeline[0]["$match"]["$and"][0]["$or"]
    end_conditions = pipeline[0]["$match"]["$and"][1]["$or"]
    assert {"start_time": {"$exists": False}} in start_conditions
    assert {"start_time": None} in start_conditions
    assert any(
        "start_time" in condition
        and isinstance(condition["start_time"], dict)
        and "$lte" in condition["start_time"]
        and isinstance(condition["start_time"]["$lte"], datetime)
        and abs(condition["start_time"]["$lte"] - now) <= timedelta(seconds=5)
        for condition in start_conditions
    )
    assert {"end_time": {"$exists": False}} in end_conditions
    assert {"end_time": None} in end_conditions
    assert any(
        "end_time" in condition
        and isinstance(condition["end_time"], dict)
        and "$gte" in condition["end_time"]
        and isinstance(condition["end_time"]["$gte"], datetime)
        and abs(condition["end_time"]["$gte"] - now) <= timedelta(seconds=5)
        for condition in end_conditions
    )
    assert pipeline[-1] == {"$limit": 3}
    assert any("$lookup" in stage for stage in pipeline)
    assert any(
        stage.get("$match")
        == {
            "$or": [
                {"dismissals": {"$eq": []}},
                {"dismissals.0.forever": False},
            ]
        }
        for stage in pipeline
    )
    assert {"$match": {"dismissals": {"$eq": []}}} not in pipeline


@pytest.mark.asyncio
async def test_list_notifications_clamps_storage_limit() -> None:
    storage = NotificationStorage()
    storage._collection = _ListNotificationCollection(
        [_notification_doc(ObjectId()) for _ in range(150)]
    )

    items, total = await storage.list_notifications(limit=10_000)

    assert total == 1_000
    assert len(items) == NOTIFICATION_LIST_LIMIT_MAX
    assert storage.collection.cursor.limit_value == NOTIFICATION_LIST_LIMIT_MAX


@pytest.mark.asyncio
async def test_active_notifications_clamps_storage_limit() -> None:
    storage = NotificationStorage()
    storage._collection = _NotificationCollection([_notification_doc(ObjectId())])

    await storage.get_active_notifications("user-1", limit=10_000)

    assert storage.collection.pipeline is not None
    assert storage.collection.pipeline[-1] == {"$limit": NOTIFICATION_LIST_LIMIT_MAX}


@pytest.mark.asyncio
async def test_missing_popup_defaults_to_false() -> None:
    storage = NotificationStorage()
    notification_id = ObjectId()
    storage._collection = _NotificationCollection([_notification_doc(notification_id)])
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_active_notifications("user-1", limit=3)

    assert items[0].popup is False
    assert items[0].should_popup is False


@pytest.mark.asyncio
async def test_popup_without_dismissal_sets_should_popup() -> None:
    storage = NotificationStorage()
    notification_id = ObjectId()
    storage._collection = _NotificationCollection(
        [_notification_doc(notification_id, popup=True)]
    )
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_active_notifications("user-1", limit=3)

    assert items[0].popup is True
    assert items[0].should_popup is True


@pytest.mark.asyncio
async def test_active_keeps_snooze_but_clears_should_popup() -> None:
    storage = NotificationStorage()
    notification_id = ObjectId()
    now = utc_now()
    doc = _notification_doc(notification_id, popup=True)
    doc["dismissals"] = [
        {"forever": False, "snooze_until": now + timedelta(hours=6)}
    ]
    storage._collection = _NotificationCollection([doc])
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_active_notifications("user-1", limit=3)

    assert [item.id for item in items] == [str(notification_id)]
    assert items[0].popup is True
    assert items[0].should_popup is False
    pipeline = storage._collection.pipeline
    assert pipeline is not None
    assert {
        "$match": {
            "$or": [
                {"dismissals": {"$eq": []}},
                {"dismissals.0.forever": False},
            ]
        }
    } in pipeline


@pytest.mark.asyncio
async def test_popup_eligible_query_not_capped_at_five() -> None:
    storage = NotificationStorage()
    storage._collection = _NotificationCollection(
        [_notification_doc(ObjectId(), popup=True)]
    )
    storage._dismissal_collection = _DismissalCollection()

    await storage.get_popup_eligible_notifications("user-1")

    pipeline = storage.collection.pipeline
    assert pipeline is not None
    assert pipeline[0]["$match"]["popup"] is True
    assert pipeline[-1] == {"$limit": NOTIFICATION_LIST_LIMIT_MAX}
    assert pipeline[-1] != {"$limit": 5}


@pytest.mark.asyncio
async def test_dismiss_forever_default_and_snooze() -> None:
    storage = NotificationStorage()
    dismissals = _DismissalWriteCollection()
    storage._dismissal_collection = dismissals

    await storage.dismiss("n1", "user-1")
    forever_filter, forever_update, forever_upsert = dismissals.calls[0]
    assert forever_filter == {"notification_id": "n1", "user_id": "user-1"}
    assert forever_update["$set"]["forever"] is True
    assert forever_update["$set"]["snooze_until"] is None
    assert forever_upsert is True

    until = utc_now() + timedelta(hours=1)
    await storage.dismiss("n1", "user-1", snooze_until=until)
    _, snooze_update, snooze_upsert = dismissals.calls[1]
    assert snooze_update["$set"]["forever"] is False
    assert snooze_update["$set"]["snooze_until"] == until
    assert snooze_upsert is True


@pytest.mark.asyncio
async def test_legacy_dismissal_hidden_from_active() -> None:
    storage = NotificationStorage()
    notification_id = ObjectId()
    doc = _notification_doc(notification_id, popup=True)
    doc["dismissals"] = [{"dismissed_at": utc_now()}]
    storage._collection = _NotificationCollection([doc])
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_active_notifications("user-1", limit=3)

    assert items == []


@pytest.mark.asyncio
async def test_forever_true_hidden_from_active() -> None:
    storage = NotificationStorage()
    notification_id = ObjectId()
    doc = _notification_doc(notification_id, popup=True)
    doc["dismissals"] = [{"forever": True, "snooze_until": None}]
    storage._collection = _NotificationCollection([doc])
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_active_notifications("user-1", limit=3)

    assert items == []


@pytest.mark.asyncio
async def test_expired_snooze_sets_should_popup() -> None:
    storage = NotificationStorage()
    notification_id = ObjectId()
    now = utc_now()
    doc = _notification_doc(notification_id, popup=True)
    doc["dismissals"] = [
        {"forever": False, "snooze_until": now - timedelta(hours=1)}
    ]
    storage._collection = _NotificationCollection([doc])
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_active_notifications("user-1", limit=3)

    assert [item.id for item in items] == [str(notification_id)]
    assert items[0].should_popup is True


@pytest.mark.asyncio
async def test_popup_eligible_excludes_active_snooze() -> None:
    storage = NotificationStorage()
    now = utc_now()
    doc = _notification_doc(ObjectId(), popup=True)
    doc["dismissals"] = [
        {"forever": False, "snooze_until": now + timedelta(hours=6)}
    ]
    storage._collection = _NotificationCollection([doc])
    storage._dismissal_collection = _DismissalCollection()

    items = await storage.get_popup_eligible_notifications("user-1")

    assert items == []
    pipeline = storage.collection.pipeline
    assert pipeline is not None
    assert any("dismissals.0.snooze_until" in str(stage) for stage in pipeline)
