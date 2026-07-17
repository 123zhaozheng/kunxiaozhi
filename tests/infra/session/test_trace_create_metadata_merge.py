"""P1-2: create_trace DuplicateKey merges missing persona metadata."""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from src.infra.session.trace_storage import TraceStorage


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def insert_one(self, doc: dict[str, Any]):
        self.insert_calls.append(doc)
        tid = doc["trace_id"]
        if tid in self.docs:
            raise DuplicateKeyError("dup")
        self.docs[tid] = {
            **doc,
            "metadata": dict(doc.get("metadata") or {}),
        }
        return type("R", (), {"inserted_id": "oid-1"})()

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]):
        self.update_calls.append((query, update))
        tid = query.get("trace_id")
        doc = self.docs.get(tid)
        if not doc:
            return type("R", (), {"modified_count": 0})()

        or_clauses = query.get("$or") or []
        meta = doc.get("metadata") or {}
        matches = False if or_clauses else True
        for clause in or_clauses:
            for field, cond in clause.items():
                if not field.startswith("metadata."):
                    continue
                key = field.split(".", 1)[1]
                if isinstance(cond, dict) and "$exists" in cond:
                    if cond["$exists"] is False and key not in meta:
                        matches = True
                elif cond is None:
                    if key not in meta or meta.get(key) is None:
                        matches = True
                elif cond == "":
                    if meta.get(key) == "":
                        matches = True
        if not matches:
            return type("R", (), {"modified_count": 0})()

        set_fields = update.get("$set") or {}
        for field, value in set_fields.items():
            if field.startswith("metadata."):
                key = field.split(".", 1)[1]
                current = meta.get(key)
                if key not in meta or current is None or current == "":
                    meta[key] = value
        doc["metadata"] = meta
        return type("R", (), {"modified_count": 1})()


@pytest.mark.asyncio
async def test_create_trace_merges_persona_on_duplicate_key():
    storage = TraceStorage()
    coll = _FakeCollection()
    storage._collection = coll

    # First create: early path without persona
    ok = await storage.create_trace(
        trace_id="t1",
        session_id="s1",
        agent_id="fast",
        run_id="r1",
        user_id="u1",
        metadata={"agent_name": "Fast", "user_id": "u1"},
    )
    assert ok is True
    assert coll.docs["t1"]["metadata"].get("persona_preset_id") is None

    # Second create: worker path with persona (DuplicateKey)
    ok2 = await storage.create_trace(
        trace_id="t1",
        session_id="s1",
        agent_id="fast",
        run_id="r1",
        user_id="u1",
        metadata={
            "agent_name": "Fast",
            "user_id": "u1",
            "persona_preset_id": "preset-abc",
        },
    )
    assert ok2 is True
    assert coll.docs["t1"]["metadata"]["persona_preset_id"] == "preset-abc"
    assert any(coll.update_calls)


@pytest.mark.asyncio
async def test_create_trace_does_not_overwrite_existing_persona():
    storage = TraceStorage()
    coll = _FakeCollection()
    storage._collection = coll

    await storage.create_trace(
        trace_id="t2",
        session_id="s1",
        metadata={"persona_preset_id": "preset-original"},
    )
    await storage.create_trace(
        trace_id="t2",
        session_id="s1",
        metadata={"persona_preset_id": "preset-other", "username": "alice"},
    )

    assert coll.docs["t2"]["metadata"]["persona_preset_id"] == "preset-original"
    assert coll.docs["t2"]["metadata"]["username"] == "alice"
