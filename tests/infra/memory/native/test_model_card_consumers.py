"""Tests that memory/audio consumers resolve config from model cards by ID."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# _get_memory_model uses NATIVE_MEMORY_MODEL_ID (chat card)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_memory_model_passes_model_id_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.memory.client.native import backend as backend_module

    calls: list[dict] = []

    async def fake_get_model(**kwargs):
        calls.append(kwargs)
        return "memory-model-instance"

    monkeypatch.setattr(
        backend_module.settings, "NATIVE_MEMORY_MODEL_ID", "memory-card-1"
    )
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_model", staticmethod(fake_get_model)
    )

    result = await backend_module.NativeMemoryBackend._get_memory_model()

    assert result == "memory-model-instance"
    assert calls == [{"model_id": "memory-card-1", "temperature": 0.1, "max_tokens": 2000}]


@pytest.mark.asyncio
async def test_get_memory_model_passes_none_when_id_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.memory.client.native import backend as backend_module

    calls: list[dict] = []

    async def fake_get_model(**kwargs):
        calls.append(kwargs)
        return "default-model-instance"

    monkeypatch.setattr(backend_module.settings, "NATIVE_MEMORY_MODEL_ID", "")
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_model", staticmethod(fake_get_model)
    )

    await backend_module.NativeMemoryBackend._get_memory_model()

    assert calls == [{"model_id": None, "temperature": 0.1, "max_tokens": 2000}]


# ---------------------------------------------------------------------------
# _setup_embedding_fn resolves a kind=embedding card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_embedding_fn_builds_client_from_embedding_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.memory.client.native import backend as backend_module

    async def fake_get_card_config(model_id, *, kind):
        assert model_id == "emb-card-1"
        assert kind == "embedding"
        return {
            "api_base": "https://api.example.com/v1",
            "api_key": "sk-emb",
            "model": "text-embedding-3-small",
        }

    monkeypatch.setattr(
        backend_module.settings, "NATIVE_MEMORY_EMBEDDING_MODEL_ID", "emb-card-1"
    )
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config", staticmethod(fake_get_card_config)
    )

    captured: dict[str, object] = {}

    class _FakeHttpxClient:
        def __init__(self, **kwargs) -> None:
            captured["kwargs"] = kwargs
            self.posted: list[dict] = []

        async def post(self, path, json):
            captured["path"] = path
            captured["json"] = json
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": [{"embedding": [0.1, 0.2]}]},
            )

        async def aclose(self) -> None:
            captured["closed"] = True

    import sys

    fake_httpx = SimpleNamespace(AsyncClient=_FakeHttpxClient, Timeout=lambda *_a, **_kw: object())
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    backend = backend_module.NativeMemoryBackend()
    await backend._setup_embedding_fn()

    assert backend._embedding_fn is not None
    assert backend._httpx_client is not None
    assert captured["kwargs"]["base_url"] == "https://api.example.com/v1"

    embedding = await backend._embedding_fn("hello")
    assert embedding == [0.1, 0.2]
    assert captured["path"] == "/v1/embeddings"
    assert captured["json"] == {"input": "hello", "model": "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_setup_embedding_fn_text_only_when_no_card(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.infra.memory.client.native import backend as backend_module

    async def fake_get_card_config(_model_id, *, kind):
        return None

    monkeypatch.setattr(
        backend_module.settings, "NATIVE_MEMORY_EMBEDDING_MODEL_ID", ""
    )
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config", staticmethod(fake_get_card_config)
    )

    backend = backend_module.NativeMemoryBackend()
    await backend._setup_embedding_fn()

    assert backend._embedding_fn is None
    assert backend._httpx_client is None
