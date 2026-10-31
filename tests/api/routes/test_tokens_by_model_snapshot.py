"""Tokens-by-model reads the snapshot model breakdown and shows model names."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.infra.analytics.storage import AnalyticsStorage
from src.kernel.schemas.user import TokenPayload


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for document in self.documents:
            yield document


class _StorageManager:
    def __init__(self, storage: AnalyticsStorage) -> None:
        self.storage = storage

    async def get_tokens_by_model(self, start, end, filters=None):
        return await self.storage.get_tokens_by_model(start, end, filters=filters)


def _admin_user() -> TokenPayload:
    return TokenPayload(
        sub="admin-1",
        username="admin",
        roles=["admin"],
        permissions=["settings:manage"],
    )


async def _get(storage: AnalyticsStorage) -> Any:
    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: _StorageManager(storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(
            "/api/analytics/tokens/by-model",
            params={"start": "2026-09-01", "end": "2026-09-30"},
        )


def _storage(result: dict[str, Any] | Exception, configs: list[dict[str, Any]]) -> AnalyticsStorage:
    storage = AnalyticsStorage()
    if isinstance(result, Exception):
        storage._read_usage = AsyncMock(side_effect=result)
    else:
        storage._read_usage = AsyncMock(return_value=result)
    model_configs = MagicMock()
    model_configs.find.return_value = _Cursor(configs)
    storage._model_configs = model_configs
    traces = MagicMock()
    traces.aggregate.side_effect = AssertionError("must not scan traces")
    storage._traces = traces
    return storage


@pytest.mark.asyncio
async def test_model_ids_are_shown_by_display_name_and_keep_raw_id() -> None:
    config_id = "9f86d081884c7d659a2feaa0c55ad015"
    storage = _storage(
        {
            "by_model": [
                {"model": config_id, "tokens": 120},
                {"model": "gpt-4o", "tokens": 40},
                {"model": "deleted-model", "tokens": 5},
            ],
            "extras_complete": True,
        },
        [
            {"id": config_id, "value": "qwen-max", "label": "通义千问 Max"},
            {"id": "other", "value": "gpt-4o", "label": "GPT-4o"},
        ],
    )

    response = await _get(storage)

    assert response.status_code == 200
    body = response.json()
    assert [(i["label"], i["id"], i["value"]) for i in body["items"]] == [
        ("通义千问 Max", config_id, 120.0),
        ("GPT-4o", "gpt-4o", 40.0),
        ("deleted-model", "deleted-model", 5.0),
    ]
    assert body["partial"] is False


@pytest.mark.asyncio
async def test_range_with_unfilled_breakdowns_is_marked_partial() -> None:
    storage = _storage({"by_model": [{"model": "m1", "tokens": 7}], "extras_complete": False}, [])

    body = (await _get(storage)).json()

    assert body["partial"] is True
    assert body["items"][0]["id"] == "m1"


@pytest.mark.asyncio
async def test_snapshot_failure_falls_back_to_live_aggregation() -> None:
    storage = _storage(RuntimeError("snapshot down"), [])
    traces = MagicMock()
    traces.aggregate.return_value = _Cursor([{"label": "m1", "value": 9}])
    storage._traces = traces

    body = (await _get(storage)).json()

    assert [(i["label"], i["value"]) for i in body["items"]] == [("m1", 9.0)]
    assert body["partial"] is False
    traces.aggregate.assert_called_once()
