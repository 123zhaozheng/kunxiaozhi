from __future__ import annotations

from typing import Any

import pytest
from bson import ObjectId

from src.infra.feedback.storage import FeedbackStorage
from src.kernel.schemas.feedback import FeedbackCreate


class _FakeInsertResult:
    def __init__(self, inserted_id: ObjectId) -> None:
        self.inserted_id = inserted_id


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def insert_one(self, doc: dict[str, Any]) -> _FakeInsertResult:
        self.inserted.append(doc)
        return _FakeInsertResult(ObjectId())

    async def find_one(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_create_persists_reason_for_down_feedback() -> None:
    """E2: storage.create 必须显式把 reason 写进 feedback_dict。"""
    storage = FeedbackStorage()
    storage._collection = _FakeCollection()

    data = FeedbackCreate(
        session_id="session-1",
        run_id="run-1",
        rating="down",
        reason="incorrect",
    )
    await storage.create(data, user_id="user-1", username="alice")

    assert storage._collection.inserted, "feedback doc should be inserted"
    doc = storage._collection.inserted[0]
    assert doc["reason"] == "incorrect"
    assert doc["rating"] == "down"


@pytest.mark.asyncio
async def test_create_persists_none_reason_for_up_feedback() -> None:
    """E2: up 反馈的 reason 为 None，仍落库为 None。"""
    storage = FeedbackStorage()
    storage._collection = _FakeCollection()

    data = FeedbackCreate(
        session_id="session-1",
        run_id="run-2",
        rating="up",
    )
    await storage.create(data, user_id="user-1", username="alice")

    doc = storage._collection.inserted[0]
    assert doc["reason"] is None
    assert doc["rating"] == "up"
