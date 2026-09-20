"""Hourly pre-aggregate and fallback coverage for the tokens-by-model route."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps as api_deps
from src.api.routes.analytics import get_analytics_manager, router
from src.infra.analytics import storage as storage_module
from src.infra.analytics.storage import AnalyticsStorage
from src.kernel.schemas.user import TokenPayload


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        if length is None:
            return list(self.documents)
        return list(self.documents[:length])

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for document in self.documents:
            yield document


class _HourlyCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.pipelines: list[list[dict[str, Any]]] = []

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _Cursor:
        self.pipelines.append(pipeline)
        match = pipeline[0]["$match"]
        if "source" in match:
            live = [document for document in self.documents if document.get("source") == "live"]
            live.sort(key=lambda document: document["bucket"])
            return _Cursor(
                [{"bucket": live[0]["bucket"]}] if live else []
            )

        bounds = match["bucket"]
        in_range = [
            document
            for document in self.documents
            if bounds["$gte"] <= document["bucket"] < bounds["$lt"]
        ]
        if any("$group" in stage for stage in pipeline):
            totals: dict[str, int] = {}
            for document in in_range:
                model = document.get("model")
                if model is not None:
                    totals[str(model)] = totals.get(str(model), 0) + int(
                        document.get("tokens", 0)
                    )
            return _Cursor(
                [
                    {"label": model, "value": value}
                    for model, value in sorted(
                        totals.items(), key=lambda item: item[1], reverse=True
                    )
                ]
            )
        return _Cursor([{"_id": document.get("_id", "available")} for document in in_range[:1]])


class _ErrorCollection:
    def aggregate(self, _pipeline: list[dict[str, Any]]) -> _Cursor:
        raise RuntimeError("usage_hourly unavailable")


class _ExplodingCollection:
    def aggregate(self, _pipeline: list[dict[str, Any]]) -> _Cursor:
        raise AssertionError("legacy traces collection must not be accessed")


class _RawCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.pipelines: list[list[dict[str, Any]]] = []

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _Cursor:
        self.pipelines.append(pipeline)
        return _Cursor(self.documents)


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


def _app(manager: _StorageManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/analytics")
    app.dependency_overrides[api_deps.get_current_user_required] = _admin_user
    app.dependency_overrides[get_analytics_manager] = lambda: manager
    return app


async def _get_response(manager: _StorageManager) -> Any:
    transport = ASGITransport(app=_app(manager))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(
            "/api/analytics/tokens/by-model",
            params={"start": "2026-09-01", "end": "2026-09-30"},
        )


def _hourly_documents() -> list[dict[str, Any]]:
    return [
        {
            "_id": "live-model",
            "bucket": datetime(2026, 9, 20, tzinfo=timezone.utc),
            "model": "gpt-4o",
            "source": "live",
            "tokens": 120,
        },
        {
            "_id": "live-model-2",
            "bucket": datetime(2026, 9, 21, tzinfo=timezone.utc),
            "model": "claude-3-7",
            "source": "live",
            "tokens": 40,
        },
        {
            "_id": "snapshot",
            "bucket": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "model": None,
            "source": "snapshot",
            "tokens": 999,
        },
    ]


@pytest.mark.asyncio
async def test_hourly_fast_path_aggregates_without_touching_traces(monkeypatch) -> None:
    hourly = _HourlyCollection(_hourly_documents())
    storage = AnalyticsStorage()
    storage._traces = _ExplodingCollection()
    monkeypatch.setattr(storage_module.settings, "ANALYTICS_USAGE_HOURLY_ENABLED", True, raising=False)
    monkeypatch.setattr(
        "src.infra.analytics.storage.get_usage_hourly_collection",
        lambda: hourly,
    )

    response = await _get_response(_StorageManager(storage))

    assert response.status_code == 200
    assert response.json()["items"] == [
        {"label": "gpt-4o", "value": 120.0, "id": None},
        {"label": "claude-3-7", "value": 40.0, "id": None},
    ]
    assert len(hourly.pipelines) == 4
    model_pipeline = next(
        pipeline for pipeline in hourly.pipelines if any("$group" in stage for stage in pipeline)
    )
    assert model_pipeline[0]["$match"]["bucket"]["$gte"].tzinfo is not None


@pytest.mark.asyncio
async def test_disabled_hourly_setting_uses_legacy_raw_path(monkeypatch) -> None:
    raw = _RawCollection([{"label": "legacy-model", "value": 77}])
    storage = AnalyticsStorage()
    storage._traces = raw
    monkeypatch.setattr(storage_module.settings, "ANALYTICS_USAGE_HOURLY_ENABLED", False, raising=False)
    monkeypatch.setattr(
        "src.infra.analytics.storage.get_usage_hourly_collection",
        lambda: (_ for _ in ()).throw(AssertionError("hourly must be disabled")),
    )

    response = await _get_response(_StorageManager(storage))

    assert response.status_code == 200
    assert response.json()["items"] == [{"label": "legacy-model", "value": 77.0, "id": None}]
    assert len(raw.pipelines) == 1


@pytest.mark.asyncio
async def test_hourly_error_logs_warning_and_returns_legacy_result(monkeypatch, caplog) -> None:
    raw = _RawCollection([{"label": "legacy-model", "value": 55}])
    storage = AnalyticsStorage()
    storage._traces = raw
    monkeypatch.setattr(storage_module.settings, "ANALYTICS_USAGE_HOURLY_ENABLED", True, raising=False)
    monkeypatch.setattr(
        "src.infra.analytics.storage.get_usage_hourly_collection",
        lambda: _ErrorCollection(),
    )

    with caplog.at_level("WARNING"):
        response = await _get_response(_StorageManager(storage))

    assert response.status_code == 200
    assert response.json()["items"] == [{"label": "legacy-model", "value": 55.0, "id": None}]
    assert any("falling back to traces" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_hourly_response_marks_range_before_live_model_data(monkeypatch) -> None:
    hourly = _HourlyCollection(_hourly_documents())
    storage = AnalyticsStorage()
    storage._traces = _ExplodingCollection()
    monkeypatch.setattr(storage_module.settings, "ANALYTICS_USAGE_HOURLY_ENABLED", True, raising=False)
    monkeypatch.setattr(
        "src.infra.analytics.storage.get_usage_hourly_collection",
        lambda: hourly,
    )

    response = await _get_response(_StorageManager(storage))

    assert response.status_code == 200
    assert response.json()["partial"] is True
    assert response.json()["model_data_since"] == "2026-09-20"


@pytest.mark.asyncio
async def test_snapshot_rows_without_model_are_not_emitted_or_counted(monkeypatch) -> None:
    hourly = _HourlyCollection(
        [
            {
                "bucket": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "model": None,
                "source": "snapshot",
                "tokens": 999,
            }
        ]
    )
    storage = AnalyticsStorage()
    storage._traces = _ExplodingCollection()
    monkeypatch.setattr(storage_module.settings, "ANALYTICS_USAGE_HOURLY_ENABLED", True, raising=False)
    monkeypatch.setattr(
        "src.infra.analytics.storage.get_usage_hourly_collection",
        lambda: hourly,
    )

    response = await _get_response(_StorageManager(storage))

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["partial"] is True
    assert response.json()["model_data_since"] is None
