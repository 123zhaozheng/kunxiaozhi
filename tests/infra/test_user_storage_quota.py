from __future__ import annotations

import copy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from src.infra.storage.user_storage import (
    StorageInvalidCursorError,
    StorageOperationBusyError,
    StorageOperationTooLargeError,
    StorageQuotaExceededError,
    UserStorageQuotaService,
    UserStorageQuotaStorage,
    _decode_storage_cursor,
    encode_storage_cursor,
)
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.storage import (
    OperationManifestItem,
    StorageFileListQuery,
    StorageOperationKind,
    StorageSource,
)


class _Cursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def sort(self, *_args):
        return self

    def skip(self, count: int):
        self.documents = self.documents[count:]
        return self

    def limit(self, count: int):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for document in self.documents:
            yield copy.deepcopy(document)


class _Collection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    @staticmethod
    def _get(document: dict, path: str, default=None):
        value = document
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    @classmethod
    def _matches(cls, document: dict, query: dict) -> bool:
        for key, expected in query.items():
            if key == "$or":
                if not any(cls._matches(document, branch) for branch in expected):
                    return False
                continue
            if key == "$and":
                if not all(cls._matches(document, branch) for branch in expected):
                    return False
                continue
            value = cls._get(document, key)
            if isinstance(expected, dict):
                for operator, operand in expected.items():
                    if operator == "$exists" and ((value is not None) != operand):
                        return False
                    if operator == "$in" and value not in operand:
                        return False
                    if operator == "$nin" and value in operand:
                        return False
                    if operator == "$lt" and not (value is not None and value < operand):
                        return False
                    if operator == "$lte" and not (value is not None and value <= operand):
                        return False
                    if operator == "$gt" and not (value is not None and value > operand):
                        return False
                continue
            if value != expected:
                return False
        return True

    async def create_index(self, *_args, **_kwargs):
        return "index"

    async def insert_one(self, document: dict):
        key = document["_id"]
        if key in self.documents:
            raise DuplicateKeyError("duplicate")
        self.documents[key] = copy.deepcopy(document)
        return SimpleNamespace(inserted_id=key)

    async def insert_many(self, documents: list[dict], ordered=True):
        for document in documents:
            await self.insert_one(document)

    async def find_one(self, query: dict):
        return next(
            (copy.deepcopy(document) for document in self.documents.values() if self._matches(document, query)),
            None,
        )

    def find(self, query: dict):
        return _Cursor(
            [copy.deepcopy(document) for document in self.documents.values() if self._matches(document, query)]
        )

    async def count_documents(self, query: dict) -> int:
        return sum(self._matches(document, query) for document in self.documents.values())

    async def update_one(self, query: dict, update: dict):
        for key, document in self.documents.items():
            if not self._matches(document, query):
                continue
            for operator, values in update.items():
                if operator == "$set":
                    for path, value in values.items():
                        self._put(document, path, value)
                elif operator == "$inc":
                    for path, value in values.items():
                        self._put(document, path, self._get(document, path, 0) + value)
                elif operator == "$unset":
                    for path in values:
                        self._delete(document, path)
            self.documents[key] = document
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    @staticmethod
    def _put(document: dict, path: str, value) -> None:
        parts = path.split(".")
        target = document
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

    @staticmethod
    def _delete(document: dict, path: str) -> None:
        parts = path.split(".")
        target = document
        for part in parts[:-1]:
            target = target.get(part, {})
        target.pop(parts[-1], None)


class _Database:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> UserStorageQuotaService:
    monkeypatch.setattr("src.infra.storage.user_storage.settings.USER_STORAGE_DEFAULT_QUOTA_MB", 1)
    monkeypatch.setattr("src.infra.storage.user_storage.settings.USER_STORAGE_ENFORCEMENT_ENABLED", True)
    return UserStorageQuotaService(UserStorageQuotaStorage(database=_Database()))


@pytest.mark.asyncio
async def test_reservation_exact_boundary_and_quota_rejection(service: UserStorageQuotaService) -> None:
    prepared = await service.prepare_create(
        "user-a",
        source=StorageSource.CHAT,
        name="one.txt",
        mime_type="text/plain",
        category="document",
        size=1024 * 1024,
        content_hash="a" * 64,
        storage_key="managed/chat/user-a/one",
        idempotency_key="one",
    )
    await service.complete_create(prepared)

    with pytest.raises(StorageQuotaExceededError) as error:
        await service.prepare_create(
            "user-a",
            source=StorageSource.CHAT,
            name="two.txt",
            mime_type="text/plain",
            category="document",
            size=1,
            content_hash="b" * 64,
            storage_key="managed/chat/user-a/two",
            idempotency_key="two",
        )
    assert error.value.code == "storage_quota_exceeded"
    assert (await service.get_usage("user-a")).used_bytes == 1024 * 1024


@pytest.mark.asyncio
async def test_create_and_delete_are_idempotent_and_owner_scoped(service: UserStorageQuotaService) -> None:
    prepared = await service.prepare_create(
        "user-a",
        source=StorageSource.CHAT,
        name="one.txt",
        mime_type="text/plain",
        category="document",
        size=5,
        content_hash="a" * 64,
        storage_key="managed/chat/user-a/one",
        idempotency_key="one",
    )
    first, _ = await service.complete_create(prepared)
    second, _ = await service.complete_create(prepared)
    assert first.file_id == second.file_id

    assert (await service.storage.get_owned_file("user-b", first.file_id)) is None
    result = await service.delete_file("user-a", first.file_id)
    repeated = await service.delete_file("user-a", first.file_id)
    assert result["logical_status"] == "deleted"
    assert repeated["logical_status"] == "already_deleted"
    assert (await service.get_usage("user-a")).used_bytes == 0


@pytest.mark.asyncio
async def test_expired_create_retry_reuses_persisted_generation(service: UserStorageQuotaService) -> None:
    kwargs = {
        "source": StorageSource.CHAT,
        "name": "retry.txt",
        "mime_type": "text/plain",
        "category": "document",
        "size": 5,
        "content_hash": "c" * 64,
        "storage_key": "managed/chat/user-a/retry",
        "idempotency_key": "retry-operation",
    }
    first = await service.prepare_create("user-a", **kwargs)
    operation = await service.storage.get_operation(first.operation_id)
    assert operation is not None
    await service.storage.update_operation(
        {"_id": first.operation_id},
        {"$set": {"lease_expires_at": utc_now() - timedelta(seconds=1)}},
    )

    retried = await service.prepare_create("user-a", **kwargs)
    assert retried.operation_id == first.operation_id
    assert retried.file_id == first.file_id
    assert retried.blob_id == first.blob_id
    assert retried.storage_key == first.storage_key


def test_storage_cursor_round_trip_and_query_binding() -> None:
    query = StorageFileListQuery(sort="size", descending=True)
    cursor = encode_storage_cursor({"_id": "file-1", "size": 42}, query)
    assert _decode_storage_cursor(cursor, query) == (42, "file-1")
    with pytest.raises(StorageInvalidCursorError):
        _decode_storage_cursor(cursor, query.model_copy(update={"descending": False}))


@pytest.mark.asyncio
async def test_operation_manifest_is_normalized_and_bounded(service: UserStorageQuotaService) -> None:
    item = {
        "file_id": "f",
        "blob_id": "b",
        "immutable_storage_key": "managed/chat/u/f",
        "write_generation": "g",
        "source": StorageSource.CHAT,
        "name": "file.txt",
        "content_hash": "a" * 64,
        "mime_type": "text/plain",
        "category": "document",
        "size": 1,
    }
    with pytest.raises(StorageOperationTooLargeError):
        await service.begin_operation(
            "user-a",
            idempotency_key="too-many",
            kind=StorageOperationKind.GROUP_CREATE,
            source=StorageSource.CHAT,
            items=[OperationManifestItem(**item) for _ in range(501)],
            size_bytes=501,
        )


@pytest.mark.asyncio
async def test_protected_shrinking_replace_releases_only_after_finalize(
    service: UserStorageQuotaService,
) -> None:
    first = await service.prepare_create(
        "user-a",
        source=StorageSource.PROFILE_AVATAR,
        source_ref="profile.avatar",
        name="old.png",
        mime_type="image/png",
        category="image",
        size=10,
        content_hash="a" * 64,
        storage_key="managed/profile_avatar/user-a/old",
        idempotency_key="avatar-old",
    )
    await service.complete_create(first)
    replacement = await service.prepare_create(
        "user-a",
        source=StorageSource.PROFILE_AVATAR,
        source_ref="profile.avatar",
        name="new.png",
        mime_type="image/png",
        category="image",
        size=4,
        content_hash="b" * 64,
        storage_key="managed/profile_avatar/user-a/new",
        idempotency_key="avatar-new",
    )
    with pytest.raises(StorageOperationBusyError):
        await service.prepare_create(
            "user-a",
            source=StorageSource.PROFILE_AVATAR,
            source_ref="profile.avatar",
            name="other.png",
            mime_type="image/png",
            category="image",
            size=4,
            content_hash="d" * 64,
            storage_key="managed/profile_avatar/user-a/other",
            idempotency_key="avatar-other",
        )
    await service.complete_create(replacement)
    # The old charge remains in place while the smaller generation is pending.
    assert (await service.get_usage("user-a")).used_bytes == 10
    await service.finalize_replace(replacement)
    assert (await service.get_usage("user-a")).used_bytes == 4
