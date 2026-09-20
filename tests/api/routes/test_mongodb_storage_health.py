from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import OperationFailure

from src.api import deps as api_deps
from src.api.routes.health import router
from src.infra.monitoring import mongo_storage
from src.kernel.schemas.user import TokenPayload


def _admin_user() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


def _regular_user() -> TokenPayload:
    return TokenPayload(
        sub="user-1",
        username="user",
        roles=["user"],
        permissions=[],
    )


class _FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.documents


class _FakeCollection:
    def __init__(
        self,
        documents: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.documents = documents or []
        self.error = error
        self.aggregate_calls: list[list[dict[str, Any]]] = []
        self.count_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _FakeCursor:
        self.aggregate_calls.append(pipeline)
        if self.error is not None:
            raise self.error
        return _FakeCursor(self.documents)

    async def count_documents(self, query: dict[str, Any], **kwargs: Any) -> int:
        self.count_calls.append((query, kwargs))
        return 3


class _FakeDatabase:
    def __init__(
        self,
        collections: dict[str, _FakeCollection],
        *,
        command_error: Exception | None = None,
        coll_stats_error_names: set[str] | None = None,
    ) -> None:
        self.collections = collections
        self.command_error = command_error
        self.coll_stats_error_names = coll_stats_error_names or set()
        self.commands: list[tuple[Any, ...]] = []

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections[name]

    async def command(self, *args: Any) -> dict[str, Any]:
        self.commands.append(args)
        if self.command_error is not None:
            raise self.command_error
        if args[0] == "collStats" and args[1] in self.coll_stats_error_names:
            raise OperationFailure("collStats unavailable")
        if args[0] == "dbStats":
            return {
                "dataSize": 100,
                "storageSize": 200,
                "indexSize": 30,
                "objects": 4,
                "collections": 4,
            }
        return {
            "size": 1,
            "storageSize": 2,
            "totalIndexSize": 3,
            "count": 4,
        }


class _FakeClient:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database

    def __getitem__(self, name: str) -> _FakeDatabase:
        return self.database


def _app(user: TokenPayload) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[api_deps.get_current_user_required] = lambda: user
    return app


async def _get(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/api/health/mongodb")


def _database_with_collections(
    *,
    failing_collection: str | None = None,
    collection_documents: dict[str, list[dict[str, Any]]] | None = None,
) -> _FakeDatabase:
    collection_documents = collection_documents or {}
    names = [
        mongo_storage.CHECKPOINT_COLLECTION_NAME,
        mongo_storage.CHECKPOINT_WRITES_COLLECTION_NAME,
        "sessions",
        "traces",
    ]
    collections = {
        name: _FakeCollection(
            collection_documents.get(name),
            error=OperationFailure("$collStats unavailable")
            if name == failing_collection
            else None,
        )
        for name in names
    }
    return _FakeDatabase(
        collections,
        coll_stats_error_names={failing_collection} if failing_collection else set(),
    )


@pytest.mark.asyncio
async def test_mongodb_storage_health_reports_database_and_checkpoint_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database_with_collections()
    monkeypatch.setattr(mongo_storage, "get_mongo_client", lambda: _FakeClient(database))

    response = await _get(_app(_admin_user()))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"available", "database", "collections", "cleanup", "checkpoint_backend"}
    assert body["available"] is True
    assert body["database"] == {
        "name": "agent_state",
        "available": True,
        "data_size": 100,
        "storage_size": 200,
        "index_size": 30,
        "objects": 4,
        "collections": 4,
        "error": None,
    }
    collection_names = {item["name"] for item in body["collections"]}
    assert {"checkpoints", "checkpoint_writes"}.issubset(collection_names)
    assert body["cleanup"]["available"] is True
    assert body["cleanup"]["approximate"] is True
    assert body["checkpoint_backend"]["backend"]


@pytest.mark.asyncio
async def test_mongodb_storage_health_requires_settings_manage_permission() -> None:
    response = await _get(_app(_regular_user()))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_mongodb_storage_health_degrades_one_collection_without_breaking_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database_with_collections(failing_collection="checkpoints")
    monkeypatch.setattr(mongo_storage, "get_mongo_client", lambda: _FakeClient(database))

    response = await _get(_app(_admin_user()))

    assert response.status_code == 200
    body = response.json()
    by_name = {item["name"]: item for item in body["collections"]}
    assert body["available"] is True
    assert by_name["checkpoints"]["available"] is False
    assert by_name["checkpoints"]["error"]
    assert by_name["checkpoint_writes"]["available"] is True
    assert body["database"]["available"] is True


@pytest.mark.asyncio
async def test_mongodb_storage_health_sums_sharded_collstats_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database_with_collections(
        collection_documents={
            "checkpoints": [
                {
                    "storageStats": {
                        "size": 10,
                        "storageSize": 20,
                        "totalIndexSize": 3,
                        "count": 2,
                    }
                },
                {
                    "storageStats": {
                        "size": 7,
                        "storageSize": 8,
                        "totalIndexSize": 4,
                        "count": 5,
                    }
                },
            ]
        }
    )
    monkeypatch.setattr(mongo_storage, "get_mongo_client", lambda: _FakeClient(database))

    response = await _get(_app(_admin_user()))

    assert response.status_code == 200
    checkpoints = next(
        item for item in response.json()["collections"] if item["name"] == "checkpoints"
    )
    assert checkpoints["size"] == 17
    assert checkpoints["storage_size"] == 28
    assert checkpoints["total_index_size"] == 7
    assert checkpoints["count"] == 7
    assert database.collections["checkpoints"].aggregate_calls == [
        [{"$collStats": {"storageStats": {}}}]
    ]


@pytest.mark.asyncio
async def test_mongodb_storage_health_connection_failure_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_connection_error() -> Any:
        raise ConnectionError("mongo unavailable")

    monkeypatch.setattr(mongo_storage, "get_mongo_client", _raise_connection_error)

    response = await _get(_app(_admin_user()))

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["database"]["available"] is False
    assert body["database"]["error"] == "mongo unavailable"
