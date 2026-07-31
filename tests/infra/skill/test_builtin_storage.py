from __future__ import annotations

from typing import Any

import pytest

from src.infra.skill.builtin import (
    BUILTIN_FILES_PER_SKILL_LIMIT,
    BuiltinSkillStorage,
    invalidate_builtin_skills_cache,
)
from src.infra.skill.constants import BUILTIN_SKILLS_VERSION_KEY
from src.infra.skill.types import BuiltinSkillCreate, BuiltinSkillUpdate

# ==========================================
# Fake query/collection helpers
# ==========================================


def _matches_condition(value: Any, cond: Any) -> bool:
    if isinstance(cond, dict):
        for op, operand in cond.items():
            if op == "$ne":
                if value == operand:
                    return False
            elif op == "$in":
                operand_list = operand or []
                if isinstance(value, list):
                    # MongoDB semantics: array field intersects operand
                    if not any(v in operand_list for v in value):
                        return False
                elif value not in operand_list:
                    return False
            elif op == "$nin":
                operand_list = operand or []
                if isinstance(value, list):
                    if any(v in operand_list for v in value):
                        return False
                elif value in operand_list:
                    return False
            elif op == "$size":
                if not isinstance(value, list) or len(value) != operand:
                    return False
            elif op == "$exists":
                has = value is not None
                if has != bool(operand):
                    return False
            else:  # unknown operator — fall back to equality
                if value != cond:
                    return False
        return True
    return value == cond


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, cond in query.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
        elif key == "$and":
            if not all(_matches(doc, sub) for sub in cond):
                return False
        else:
            if not _matches_condition(doc.get(key), cond):
                return False
    return True


class _AsyncCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)
        self._index = 0
        self.limit_value: int | None = None
        self.sort_field: str | None = None
        self.sort_dir: int | None = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.limit_value is not None:
            self._docs = self._docs[: self.limit_value]
            self.limit_value = None
        if self._index >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._index]
        self._index += 1
        return doc

    def sort(self, field: str, direction: int) -> "_AsyncCursor":
        self.sort_field = field
        self.sort_dir = direction
        if field == "skill_name":
            self._docs.sort(key=lambda d: d.get(field, ""), reverse=(direction < 0))
        return self

    def limit(self, value: int) -> "_AsyncCursor":
        self.limit_value = value
        return self


class _RecordingMetaCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self.finds: list[dict[str, Any]] = []
        self.inserts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def find(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        self.finds.append(query)
        return _AsyncCursor([d for d in self._docs if _matches(d, query)])

    async def find_one(self, query: dict[str, Any]):
        for doc in self._docs:
            if _matches(doc, query):
                return doc
        return None

    async def insert_one(self, doc: dict[str, Any]):
        self.inserts.append(doc)
        self._docs.append(doc)
        return None

    async def delete_one(self, query: dict[str, Any]):
        self.deletes.append(query)
        before = len(self._docs)
        self._docs = [d for d in self._docs if not _matches(d, query)]
        removed = before - len(self._docs)
        return type("Res", (), {"deleted_count": removed})()

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]):
        self.updates.append((query, update))
        for doc in self._docs:
            if _matches(doc, query) and "$set" in update:
                doc.update(update["$set"])
        return None


class _RecordingFilesCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self._docs = list(docs or [])
        self.finds: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.bulk_operations: list[Any] = []

    def find(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        self.finds.append(query)
        return _AsyncCursor([d for d in self._docs if _matches(d, query)])

    async def delete_many(self, query: dict[str, Any]):
        self.deletes.append(query)
        self._docs = [d for d in self._docs if not _matches(d, query)]
        return None

    async def bulk_write(self, operations: list[Any], ordered: bool = True):
        self.bulk_operations.extend(operations)
        return None


def _make_storage(
    meta_docs: list[dict[str, Any]] | None = None,
    file_docs: list[dict[str, Any]] | None = None,
) -> tuple[BuiltinSkillStorage, _RecordingMetaCollection, _RecordingFilesCollection]:
    storage = BuiltinSkillStorage()
    meta = _RecordingMetaCollection(meta_docs or [])
    files = _RecordingFilesCollection(file_docs or [])
    storage._meta_collection = meta
    storage._files_collection = files
    return storage, meta, files


# ==========================================
# 元数据 CRUD
# ==========================================


@pytest.mark.asyncio
async def test_create_builtin_skill_inserts_metadata() -> None:
    storage, meta, _ = _make_storage()

    result = await storage.create_builtin_skill(
        BuiltinSkillCreate(
            skill_name="planner",
            description="Plan work",
            allowed_roles=["analyst"],
            source="zip",
        ),
        created_by="admin-1",
    )

    assert result.skill_name == "planner"
    assert result.allowed_roles == ["analyst"]
    assert result.source == "zip"
    assert result.is_active is True
    assert len(meta.inserts) == 1
    inserted = meta.inserts[0]
    assert inserted["skill_name"] == "planner"
    assert inserted["created_by"] == "admin-1"
    assert inserted["created_at"] == inserted["updated_at"]


@pytest.mark.asyncio
async def test_create_builtin_skill_rejects_duplicate() -> None:
    storage, _, _ = _make_storage(
        [{"skill_name": "planner", "allowed_roles": [], "source": "zip"}]
    )

    with pytest.raises(ValueError, match="already exists"):
        await storage.create_builtin_skill(
            BuiltinSkillCreate(skill_name="planner", source="zip"),
            created_by="admin-1",
        )


@pytest.mark.asyncio
async def test_update_builtin_skill_changes_allowed_roles_and_active() -> None:
    storage, meta, _ = _make_storage(
        [
            {
                "skill_name": "planner",
                "description": "",
                "allowed_roles": ["x"],
                "source": "zip",
                "is_active": True,
            }
        ]
    )

    updated = await storage.update_builtin_skill(
        "planner",
        BuiltinSkillUpdate(
            allowed_roles=["a", "b"], description="new", is_active=False
        ),
    )

    assert updated is not None
    assert updated.allowed_roles == ["a", "b"]
    assert updated.is_active is False
    set_payload = meta.updates[0][1]["$set"]
    assert set_payload["allowed_roles"] == ["a", "b"]
    assert set_payload["is_active"] is False


@pytest.mark.asyncio
async def test_update_builtin_skill_returns_none_when_missing() -> None:
    storage, _, _ = _make_storage()

    result = await storage.update_builtin_skill(
        "missing", BuiltinSkillUpdate(description="x")
    )

    assert result is None


@pytest.mark.asyncio
async def test_delete_builtin_skill_removes_metadata_and_files() -> None:
    storage, meta, files = _make_storage(
        [{"skill_name": "planner", "allowed_roles": [], "source": "zip"}],
        [{"skill_name": "planner", "file_path": "SKILL.md", "content": "x"}],
    )

    deleted = await storage.delete_builtin_skill("planner")

    assert deleted is True
    assert meta.deletes == [{"skill_name": "planner"}]
    # files delete_many uses skill_name equality
    assert any(d.get("skill_name") == "planner" for d in files.deletes)


# ==========================================
# 角色过滤（注入用）
# ==========================================


@pytest.mark.asyncio
async def test_list_builtin_skill_names_filters_by_role_intersection() -> None:
    docs = [
        {"skill_name": "all-role", "is_active": True, "allowed_roles": []},
        {"skill_name": "analyst-only", "is_active": True, "allowed_roles": ["analyst"]},
        {"skill_name": "eng-only", "is_active": True, "allowed_roles": ["engineer"]},
        {"skill_name": "inactive", "is_active": False, "allowed_roles": []},
    ]
    storage, meta, _ = _make_storage(docs)

    names = await storage.list_builtin_skill_names_for_roles(
        ["analyst"], is_admin=False
    )

    assert names == ["all-role", "analyst-only"]
    # Verify the query shape: active filter + role $or clause
    query = meta.finds[0]
    assert query["is_active"] == {"$ne": False}
    assert "$or" in query
    or_clauses = query["$or"]
    assert {"allowed_roles": {"$in": ["analyst"]}} in or_clauses
    assert {"allowed_roles": {"$size": 0}} in or_clauses
    assert {"allowed_roles": {"$exists": False}} in or_clauses


@pytest.mark.asyncio
async def test_list_builtin_skill_names_admin_skips_role_clause() -> None:
    docs = [
        {"skill_name": "all-role", "is_active": True, "allowed_roles": []},
        {"skill_name": "eng-only", "is_active": True, "allowed_roles": ["engineer"]},
        {"skill_name": "inactive", "is_active": False, "allowed_roles": []},
    ]
    storage, meta, _ = _make_storage(docs)

    names = await storage.list_builtin_skill_names_for_roles([], is_admin=True)

    assert names == ["all-role", "eng-only"]
    query = meta.finds[0]
    # admin path: only active filter, no $or
    assert query == {"is_active": {"$ne": False}}


@pytest.mark.asyncio
async def test_list_builtin_skill_names_no_role_match_returns_empty() -> None:
    docs = [
        {"skill_name": "eng-only", "is_active": True, "allowed_roles": ["engineer"]},
    ]
    storage, _, _ = _make_storage(docs)

    names = await storage.list_builtin_skill_names_for_roles(
        ["analyst"], is_admin=False
    )

    assert names == []


# ==========================================
# 文件批量加载
# ==========================================


@pytest.mark.asyncio
async def test_batch_get_builtin_skill_files_groups_by_skill_name() -> None:
    file_docs = [
        {"skill_name": "planner", "file_path": "SKILL.md", "content": "p-md"},
        {"skill_name": "planner", "file_path": "notes.md", "content": "p-notes"},
        {"skill_name": "writer", "file_path": "SKILL.md", "content": "w-md"},
    ]
    storage, _, _ = _make_storage([], file_docs)

    result = await storage.batch_get_builtin_skill_files(["planner", "writer"])

    assert result == {
        "planner": {"SKILL.md": "p-md", "notes.md": "p-notes"},
        "writer": {"SKILL.md": "w-md"},
    }


@pytest.mark.asyncio
async def test_batch_get_builtin_skill_files_empty_input_returns_empty() -> None:
    storage, _, _ = _make_storage(
        [],
        [{"skill_name": "planner", "file_path": "SKILL.md", "content": "x"}],
    )

    result = await storage.batch_get_builtin_skill_files([])

    assert result == {}


@pytest.mark.asyncio
async def test_sync_builtin_files_rejects_too_many_files() -> None:
    storage, _, _ = _make_storage()

    with pytest.raises(ValueError, match="too many files"):
        await storage.sync_builtin_files(
            "planner",
            {f"file-{i}.md": "x" for i in range(BUILTIN_FILES_PER_SKILL_LIMIT + 1)},
        )


@pytest.mark.asyncio
async def test_sync_builtin_files_deletes_orphans_and_upserts() -> None:
    storage, _, files = _make_storage(
        [],
        [
            {"skill_name": "planner", "file_path": "old.md", "content": "old"},
            {"skill_name": "planner", "file_path": "SKILL.md", "content": "keep"},
        ],
    )

    await storage.sync_builtin_files("planner", {"SKILL.md": "keep", "notes.md": "n"})

    assert files.deletes == [
        {"skill_name": "planner", "file_path": {"$nin": ["SKILL.md", "notes.md"]}}
    ]
    assert len(files.bulk_operations) == 2


# ==========================================
# 缓存失效
# ==========================================


class _IncrRedis:
    def __init__(self) -> None:
        self.incr_calls: list[str] = []

    async def incr(self, key: str) -> int:
        self.incr_calls.append(key)
        return 1


@pytest.mark.asyncio
async def test_invalidate_builtin_skills_cache_bumps_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.skill import builtin as builtin_module
    from src.infra.storage import redis as redis_storage

    redis = _IncrRedis()
    monkeypatch.setattr(redis_storage, "get_redis_client", lambda: redis)

    await invalidate_builtin_skills_cache()

    assert redis.incr_calls == [BUILTIN_SKILLS_VERSION_KEY]
    assert builtin_module.invalidate_builtin_skills_cache is invalidate_builtin_skills_cache


# ==========================================
# zip 来源（import_parsed_skills）
# ==========================================


@pytest.mark.asyncio
async def test_import_parsed_skills_creates_metadata_and_files_and_bumps_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, meta, files = _make_storage()
    binary_uploads: list[tuple[str, str, bytes]] = []

    async def _fake_binary_upload(skill_name, file_path, data, mime_type=None):
        binary_uploads.append((skill_name, file_path, data))
        from src.infra.skill.binary import SkillBinaryRef

        return SkillBinaryRef(
            storage_key=f"skills/_builtin/{skill_name}/{file_path}",
            mime_type=mime_type or "application/octet-stream",
            size=len(data),
        )

    monkeypatch.setattr(storage, "set_builtin_binary_file", _fake_binary_upload)

    incr_calls: list[str] = []

    async def _fake_invalidate() -> None:
        incr_calls.append("bumped")

    monkeypatch.setattr(
        "src.infra.skill.builtin.invalidate_builtin_skills_cache",
        _fake_invalidate,
    )

    parsed = [
        (
            "planner",
            {"SKILL.md": "---\nname: planner\ndescription: Plan work\n---\n", "notes.md": "n"},
            {"logo.png": b"\x89PNG bytes"},
        ),
    ]

    created = await storage.import_parsed_skills(parsed, ["analyst"], "admin-1")

    assert created == ["planner"]
    # metadata created with zip source + allowed_roles
    assert len(meta.inserts) == 1
    inserted = meta.inserts[0]
    assert inserted["source"] == "zip"
    assert inserted["source_ref"] is None
    assert inserted["allowed_roles"] == ["analyst"]
    assert inserted["description"] == "Plan work"
    # text files synced (2 → SKILL.md + notes.md)
    assert len(files.bulk_operations) == 2
    # binary uploaded via S3 helper
    assert binary_uploads == [("planner", "logo.png", b"\x89PNG bytes")]
    # version bumped once after the batch
    assert incr_calls == ["bumped"]


@pytest.mark.asyncio
async def test_import_parsed_skills_rejects_duplicate_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, _, _ = _make_storage(
        [{"skill_name": "planner", "allowed_roles": [], "source": "zip"}]
    )

    async def _noop_invalidate() -> None:
        return None

    monkeypatch.setattr(
        "src.infra.skill.builtin.invalidate_builtin_skills_cache",
        _noop_invalidate,
    )

    parsed = [("planner", {"SKILL.md": "x"}, {})]
    with pytest.raises(ValueError, match="already exists"):
        await storage.import_parsed_skills(parsed, [], "admin-1")


# ==========================================
# 商城来源（create_from_marketplace）
# ==========================================


class _FakeMarketplaceStorage:
    def __init__(
        self,
        meta: dict[str, Any] | None,
        file_batches: list[dict[str, str]],
    ) -> None:
        self._meta = meta
        self._file_batches = file_batches

    async def get_marketplace_skill(self, name: str):
        if not self._meta:
            return None
        from src.infra.skill.types import MarketplaceSkill

        return MarketplaceSkill(
            skill_name=self._meta["skill_name"],
            description=self._meta.get("description", ""),
            tags=self._meta.get("tags", []),
        )

    async def iter_marketplace_file_batches(self, name: str, *, batch_size: int = 25):
        for batch in self._file_batches:
            yield batch


@pytest.mark.asyncio
async def test_create_from_marketplace_copies_files_to_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, meta, files = _make_storage()
    marketplace = _FakeMarketplaceStorage(
        {"skill_name": "planner", "description": "from market", "tags": ["p"]},
        [{"SKILL.md": "market-md", "notes.md": "market-notes"}],
    )

    async def _noop_invalidate() -> None:
        return None

    monkeypatch.setattr(
        "src.infra.skill.builtin.invalidate_builtin_skills_cache",
        _noop_invalidate,
    )

    result = await storage.create_from_marketplace(
        "planner", ["analyst"], "admin-1", marketplace_storage=marketplace
    )

    assert result.skill_name == "planner"
    assert result.source == "marketplace"
    assert result.source_ref == "planner"
    assert result.allowed_roles == ["analyst"]
    # metadata inserted once
    assert len(meta.inserts) == 1
    assert meta.inserts[0]["description"] == "from market"
    # files copied via upsert batch (2 files)
    assert len(files.bulk_operations) == 2


@pytest.mark.asyncio
async def test_create_from_marketplace_missing_source_raises() -> None:
    storage, _, _ = _make_storage()
    marketplace = _FakeMarketplaceStorage(None, [])

    with pytest.raises(ValueError, match="not found"):
        await storage.create_from_marketplace(
            "missing", [], "admin-1", marketplace_storage=marketplace
        )
