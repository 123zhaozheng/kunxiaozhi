from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes import session as session_route


class _FakeManager:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []

    async def get_session(self, session_id: str):
        return SimpleNamespace(user_id="user-1", session_id=session_id, metadata={})

    async def update_session(self, session_id: str, update):
        self.updated.append((session_id, getattr(update, "name", None)))
        return SimpleNamespace(session_id=session_id)


@pytest.mark.asyncio
async def test_generate_session_title_passes_session_title_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def fake_get_model(**kwargs):
        calls.append(kwargs)
        return "title-model-instance"

    async def fake_ainvoke_with_retry(model, prompt, max_retries=None):
        return SimpleNamespace(content="📊 股市趋势")

    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_model", fake_get_model
    )
    monkeypatch.setattr(session_route, "_ainvoke_with_retry", fake_ainvoke_with_retry)
    monkeypatch.setattr(session_route, "SessionManager", _FakeManager)
    monkeypatch.setattr(session_route, "verify_session_ownership", lambda session, user: None)
    monkeypatch.setattr(session_route.settings, "SESSION_TITLE_MODEL_ID", "title-card-id")
    monkeypatch.setattr(session_route.settings, "LLM_MAX_RETRIES", 3)

    user = SimpleNamespace(user_id="user-1")
    result = await session_route.generate_session_title(
        "sess-1", message="今天股市怎么样？", lang="zh", user=user
    )

    assert calls == [
        {"model_id": "title-card-id", "max_tokens": 100, "max_retries": 3}
    ]
    assert result["title"] == "📊 股市趋势"


@pytest.mark.asyncio
async def test_generate_session_title_passes_none_when_id_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def fake_get_model(**kwargs):
        calls.append(kwargs)
        return "title-model-instance"

    async def fake_ainvoke_with_retry(model, prompt, max_retries=None):
        return SimpleNamespace(content="📊 股市趋势")

    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_model", fake_get_model
    )
    monkeypatch.setattr(session_route, "_ainvoke_with_retry", fake_ainvoke_with_retry)
    monkeypatch.setattr(session_route, "SessionManager", _FakeManager)
    monkeypatch.setattr(session_route, "verify_session_ownership", lambda session, user: None)
    monkeypatch.setattr(session_route.settings, "SESSION_TITLE_MODEL_ID", "", raising=False)
    monkeypatch.setattr(session_route.settings, "LLM_MAX_RETRIES", 3)

    user = SimpleNamespace(user_id="user-1")
    await session_route.generate_session_title(
        "sess-1", message="今天股市怎么样？", lang="zh", user=user
    )

    assert calls == [
        {"model_id": None, "max_tokens": 100, "max_retries": 3}
    ]
