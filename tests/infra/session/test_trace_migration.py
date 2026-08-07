from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infra.session.trace_migration import (
    MigrationConfirmationRequiredError,
    TraceDuplicateMigrator,
    _merge_documents,
)


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, length=None):
        return list(self.docs)


class _Collection:
    def __init__(self, docs=None):
        self.docs = {doc["_id"]: dict(doc) for doc in (docs or [])}
        self.writes = []

    def find(self, query=None):
        query = query or {}
        return _Cursor([doc for doc in self.docs.values() if all(doc.get(k) == v for k, v in query.items())])

    async def find_one(self, query):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    async def insert_many(self, docs, ordered=True):
        self.writes.append(("insert_many", list(docs)))
        for doc in docs:
            self.docs[doc["_id"]] = dict(doc)

    async def insert_one(self, doc):
        self.writes.append(("insert_one", dict(doc)))
        self.docs[doc.get("_id", len(self.docs))] = dict(doc)
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_one(self, query, update, upsert=False):
        self.writes.append(("update_one", query, update))
        key = query.get("_id")
        doc = self.docs.get(key, {"_id": key})
        for field, value in update.get("$set", {}).items():
            doc[field] = value
        for field, value in update.get("$setOnInsert", {}).items():
            doc.setdefault(field, value)
        self.docs[key] = doc
        return SimpleNamespace(upserted_id=key, modified_count=1)

    async def replace_one(self, query, replacement, upsert=False):
        self.writes.append(("replace_one", query, replacement))
        key = query["_id"]
        if key in self.docs or upsert:
            self.docs[key] = dict(replacement)
        return SimpleNamespace(modified_count=1)

    async def delete_one(self, query):
        self.writes.append(("delete_one", query))
        for key, doc in list(self.docs.items()):
            if all(doc.get(field) == value for field, value in query.items() if field != "_id") and (
                query.get("_id") is None or key == query["_id"]
            ):
                del self.docs[key]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def _duplicates():
    return [
        {"_id": "old", "session_id": "s1", "trace_id": "t1", "updated_at": 1, "events": [{"event_id": "a", "seq": 2}]},
        {"_id": "new", "session_id": "s1", "trace_id": "t1", "updated_at": 2, "metadata": {"model": "x"}, "events": [{"event_id": "b", "seq": 1}]},
        {"_id": "other-session", "session_id": "s2", "trace_id": "t1", "events": []},
    ]


@pytest.mark.asyncio
async def test_plan_is_read_only_and_strictly_session_scoped():
    source = _Collection(_duplicates())
    plan = await TraceDuplicateMigrator(source).plan()
    assert plan.source_document_count == 2
    assert plan.duplicate_groups[0].session_id == "s1"
    assert plan.duplicate_groups[0].merged_event_count == 2
    assert source.writes == []


@pytest.mark.asyncio
async def test_apply_requires_confirmation_and_merges_before_exact_deletes():
    source = _Collection(_duplicates())
    backups = _Collection()
    audit = _Collection()
    operations = _Collection()
    migrator = TraceDuplicateMigrator(
        source,
        backup_collection=backups,
        audit_collection=audit,
        operation_collection=operations,
    )
    with pytest.raises(MigrationConfirmationRequiredError):
        await migrator.apply()
    result = await migrator.apply(confirm=True, operation_id="op-1")
    assert result.applied is True
    assert result.deleted_document_count == 1
    assert set(source.docs) == {"new", "other-session"}
    assert source.docs["new"]["event_count"] == 2
    assert len(backups.docs) == 2
    assert result.index_preflight_clear is True
    assert audit.docs


@pytest.mark.asyncio
async def test_rollback_restores_all_backed_up_sources():
    source = _Collection(_duplicates())
    backups = _Collection()
    migrator = TraceDuplicateMigrator(source, backup_collection=backups)
    await migrator.apply(confirm=True, operation_id="op-2")
    await migrator.rollback("op-2")
    assert set(source.docs) == {"old", "new", "other-session"}


@pytest.mark.asyncio
async def test_active_groups_are_skipped_by_default():
    source = _Collection(
        [
            {"_id": "a", "session_id": "s", "trace_id": "t", "status": "running", "events": []},
            {"_id": "b", "session_id": "s", "trace_id": "t", "status": "completed", "events": []},
        ]
    )
    plan = await TraceDuplicateMigrator(source).plan()
    assert plan.skipped_active_groups == 1
    assert plan.duplicate_groups[0].skip_reason == "active_writer"


def test_merge_uses_history_order_and_legacy_id_spelling() -> None:
    merged, _ = _merge_documents(
        [
            {
                "_id": "a",
                "session_id": "s",
                "trace_id": "t",
                "events": [
                    {"event_id": "new", "seq": 1},
                    {"id": "old", "timestamp": "2024-01-01T00:00:00+00:00"},
                ],
            },
            {"_id": "b", "session_id": "s", "trace_id": "t", "events": []},
        ]
    )
    assert [event["event_id"] for event in merged["events"]] == ["old", "new"]


@pytest.mark.asyncio
async def test_session_scoped_plan_does_not_claim_global_index_readiness():
    source = _Collection(
        _duplicates()
        + [
            {"_id": "other-a", "session_id": "s2", "trace_id": "t2", "events": []},
            {"_id": "other-b", "session_id": "s2", "trace_id": "t2", "events": []},
        ]
    )
    plan = await TraceDuplicateMigrator(source).plan(session_id="s1")
    assert plan.index_preflight_clear is False


@pytest.mark.asyncio
async def test_operation_id_is_validated_before_mutation():
    source = _Collection(_duplicates())
    with pytest.raises(ValueError, match="operation_id"):
        await TraceDuplicateMigrator(source).plan(operation_id="bad id")
