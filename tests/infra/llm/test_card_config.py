"""Tests for LLMClient.get_card_config (non-chat card resolution)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infra.llm.client import LLMClient


@pytest.mark.asyncio
async def test_get_card_config_returns_none_for_empty_id() -> None:
    assert await LLMClient.get_card_config(None, kind="embedding") is None
    assert await LLMClient.get_card_config("", kind="transcribe") is None


@pytest.mark.asyncio
async def test_get_card_config_resolves_matching_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(model_id):
        assert model_id == "emb-card-1"
        return SimpleNamespace(
            id=model_id,
            value="text-embedding-3-small",
            api_base="https://api.example.com/v1",
            api_key="sk-emb",
            kind="embedding",
        )

    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: SimpleNamespace(get=fake_get),
    )

    config = await LLMClient.get_card_config("emb-card-1", kind="embedding")

    assert config == {
        "api_base": "https://api.example.com/v1",
        "api_key": "sk-emb",
        "model": "text-embedding-3-small",
    }


@pytest.mark.asyncio
async def test_get_card_config_returns_none_on_kind_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(_model_id):
        return SimpleNamespace(
            id="card-1",
            value="whisper-1",
            api_base="https://api.example.com/v1",
            api_key="sk",
            kind="transcribe",
        )

    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: SimpleNamespace(get=fake_get),
    )

    config = await LLMClient.get_card_config("card-1", kind="embedding")

    assert config is None


@pytest.mark.asyncio
async def test_get_card_config_legacy_card_matches_chat_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(_model_id):
        # Legacy card without `kind` -> defaults to chat.
        return SimpleNamespace(
            id="card-legacy",
            value="gpt-4o-mini",
            api_base="https://api.openai.com/v1",
            api_key="sk",
            kind=None,
        )

    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: SimpleNamespace(get=fake_get),
    )

    assert (
        await LLMClient.get_card_config("card-legacy", kind="chat")
    ) is not None
    assert (
        await LLMClient.get_card_config("card-legacy", kind="transcribe") is None
    )


@pytest.mark.asyncio
async def test_get_card_config_returns_none_when_card_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(_model_id):
        return None

    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: SimpleNamespace(get=fake_get),
    )

    assert await LLMClient.get_card_config("missing", kind="rerank") is None


@pytest.mark.asyncio
async def test_get_card_config_swallow_storage_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(_model_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "src.infra.agent.model_storage.get_model_storage",
        lambda: SimpleNamespace(get=fake_get),
    )

    # Must not raise — returns None so callers fall back gracefully.
    assert await LLMClient.get_card_config("card-1", kind="rerank") is None
