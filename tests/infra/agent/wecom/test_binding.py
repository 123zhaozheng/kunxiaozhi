"""Tests for WeCom notify binding storage and persona WeCom config notify targets."""

from __future__ import annotations

from typing import Any

import pytest

from src.infra.agent import config_storage
from src.infra.agent.wecom.binding import WeComNotifyBindingStorage
from src.infra.utils.datetime import utc_now


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeBindingsCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs: list[dict[str, Any]] = list(docs or [])
        self.indexes: list[tuple[list, dict]] = []
        self.update_one_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        self.find_one_calls: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    @staticmethod
    def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, value in query.items():
            if isinstance(value, dict) and "$in" in value:
                if doc.get(key) not in value["$in"]:
                    return False
            elif doc.get(key) != value:
                return False
        return True

    async def create_index(self, keys: list, unique: bool = False):
        self.indexes.append((keys, {"unique": unique}))

    async def update_one(self, query, update, upsert: bool = False):
        self.update_one_calls.append((query, update, upsert))
        for doc in self.docs:
            if self._matches(doc, query):
                return None
        if upsert:
            new_doc = dict(query)
            new_doc.update(update.get("$setOnInsert", {}))
            self.docs.append(new_doc)
        return None

    async def find_one(self, query, projection=None):
        self.find_one_calls.append((query, projection))
        for doc in self.docs:
            if self._matches(doc, query):
                return dict(doc)
        return None

    def find(self, query, projection=None):
        return _FakeCursor([dict(doc) for doc in self.docs if self._matches(doc, query)])


class _FakePersonaConfigCollection:
    """Fake for persona_wecom_config: find_one + update_one (merges $set)."""

    def __init__(self, doc: dict[str, Any] | None = None) -> None:
        self.doc: dict[str, Any] | None = doc
        self.update_one_calls: list[tuple[dict[str, Any], dict[str, Any], bool]] = []

    async def find_one(self, query=None, projection=None):
        return self.doc

    async def update_one(self, query, update, upsert: bool = False):
        self.update_one_calls.append((query, update, upsert))
        if self.doc is None:
            self.doc = dict(query)
        self.doc.update(update.get("$set", {}))


def _binding_doc(aibotid: str, username: str) -> dict[str, Any]:
    return {"aibotid": aibotid, "username": username, "bound_at": utc_now()}


@pytest.mark.asyncio
async def test_create_indexes_creates_unique_binding_index() -> None:
    storage = WeComNotifyBindingStorage()
    coll = _FakeBindingsCollection()
    storage._collection = coll

    await storage.create_indexes()

    assert coll.indexes == [([("aibotid", 1), ("username", 1)], {"unique": True})]


@pytest.mark.asyncio
async def test_upsert_is_idempotent() -> None:
    storage = WeComNotifyBindingStorage()
    coll = _FakeBindingsCollection()
    storage._collection = coll

    assert await storage.upsert("bot-1", "u1") is True
    assert await storage.upsert("bot-1", "u1") is True

    assert len(coll.docs) == 1
    assert coll.docs[0]["aibotid"] == "bot-1"
    assert coll.docs[0]["username"] == "u1"
    assert coll.docs[0]["bound_at"] is not None
    assert coll.update_one_calls[0][2] is True  # upsert=True


@pytest.mark.asyncio
async def test_is_bound() -> None:
    storage = WeComNotifyBindingStorage()
    coll = _FakeBindingsCollection([_binding_doc("bot-1", "u1")])
    storage._collection = coll

    assert await storage.is_bound("bot-1", "u1") is True
    assert await storage.is_bound("bot-1", "u2") is False
    assert await storage.is_bound("bot-2", "u1") is False


@pytest.mark.asyncio
async def test_list_bound_returns_only_bound_subset() -> None:
    storage = WeComNotifyBindingStorage()
    coll = _FakeBindingsCollection(
        [_binding_doc("bot-1", "u1"), _binding_doc("bot-1", "u2")]
    )
    storage._collection = coll

    assert await storage.list_bound("bot-1", ["u1", "u3", "u4"]) == {"u1"}
    assert await storage.list_bound("bot-1", ["u3", "u4"]) == set()
    assert await storage.list_bound("bot-2", ["u1", "u2"]) == set()
    assert await storage.list_bound("bot-1", []) == set()


@pytest.mark.asyncio
async def test_get_persona_wecom_config_missing_feedback_notify_targets_defaults_empty() -> None:
    storage = config_storage.AgentConfigStorage()
    doc = {
        "preset_id": "preset-1",
        "aibotid": "aibotid-1",
        "secret": "s3cret",
        "stream_reply": False,
    }
    storage._collections = {
        config_storage._COLL_PERSONA_WECOM_CONFIG: _FakePersonaConfigCollection(dict(doc))
    }

    result = await storage.get_persona_wecom_config("preset-1")

    assert result is not None
    assert result.feedback_notify_targets == []


@pytest.mark.asyncio
async def test_set_persona_wecom_config_writes_feedback_notify_targets() -> None:
    storage = config_storage.AgentConfigStorage()
    coll = _FakePersonaConfigCollection(doc={"preset_id": "preset-1"})
    storage._collections = {config_storage._COLL_PERSONA_WECOM_CONFIG: coll}

    result = await storage.set_persona_wecom_config(
        "preset-1", "aibotid-1", feedback_notify_targets=["u1", "u2"]
    )

    assert coll.update_one_calls[0][1]["$set"]["feedback_notify_targets"] == ["u1", "u2"]
    assert result is not None
    assert result.feedback_notify_targets == ["u1", "u2"]
