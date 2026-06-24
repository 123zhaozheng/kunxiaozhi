"""Tests for rerank_candidates resolving a kind=rerank model card."""

from __future__ import annotations

import pytest


def _candidate(memory_id: str) -> dict:
    return {
        "memory_id": memory_id,
        "title": memory_id,
        "summary": memory_id,
        "text": memory_id,
        "score": 0.5,
    }


@pytest.mark.asyncio
async def test_rerank_uses_rerank_card_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.memory.client.native import search as search_module

    captured: dict[str, object] = {}

    async def fake_get_card_config(model_id, *, kind):
        assert model_id == "rerank-card-1"
        assert kind == "rerank"
        return {
            "api_base": "https://api.example.com/v1",
            "api_key": "sk-rerank",
            "model": "bge-reranker-v2-m3",
        }

    monkeypatch.setattr(
        search_module.settings, "NATIVE_MEMORY_RERANK_MODEL_ID", "rerank-card-1"
    )
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config", staticmethod(fake_get_card_config)
    )

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"results": [{"index": 1, "relevance_score": 0.9}]}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def post(self, path, json):
            captured["path"] = path
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", lambda **kwargs: _FakeClient()
    )

    candidates = [_candidate("m1"), _candidate("m2")]
    result = await search_module.rerank_candidates("query", candidates, max_results=2)

    assert [item["memory_id"] for item in result] == ["m2"]
    assert captured["path"] == "/v1/rerank"
    assert captured["json"]["model"] == "bge-reranker-v2-m3"
    assert captured["json"]["query"] == "query"
    assert captured["json"]["top_n"] == 2


@pytest.mark.asyncio
async def test_rerank_falls_back_to_local_when_no_card(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.memory.client.native import search as search_module

    async def fake_get_card_config(_model_id, *, kind):
        return None

    monkeypatch.setattr(
        search_module.settings, "NATIVE_MEMORY_RERANK_MODEL_ID", ""
    )
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config", staticmethod(fake_get_card_config)
    )

    posted: list[str] = []

    class _ExplodingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def post(self, *_a, **_kw):
            posted.append("called")
            raise AssertionError("rerank HTTP must not be called without a card")

    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", lambda **kwargs: _ExplodingClient()
    )

    candidates = [_candidate("m1"), _candidate("m2")]
    result = await search_module.rerank_candidates("m1", candidates, max_results=2)

    # Local rerank returns both candidates ordered by blended score; no HTTP call.
    assert posted == []
    assert {item["memory_id"] for item in result} == {"m1", "m2"}


@pytest.mark.asyncio
async def test_rerank_falls_back_when_card_kind_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.memory.client.native import search as search_module

    async def fake_get_card_config(_model_id, *, kind):
        # kind mismatch -> get_card_config returns None
        return None

    monkeypatch.setattr(
        search_module.settings, "NATIVE_MEMORY_RERANK_MODEL_ID", "chat-card-1"
    )
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config", staticmethod(fake_get_card_config)
    )

    class _ExplodingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def post(self, *_a, **_kw):
            raise AssertionError("rerank HTTP must not be called on kind mismatch")

    monkeypatch.setattr(
        search_module.httpx, "AsyncClient", lambda **kwargs: _ExplodingClient()
    )

    candidates = [_candidate("m1"), _candidate("m2")]
    result = await search_module.rerank_candidates("m1", candidates, max_results=2)

    assert {item["memory_id"] for item in result} == {"m1", "m2"}
