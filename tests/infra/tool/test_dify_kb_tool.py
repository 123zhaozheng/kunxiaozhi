"""Tests for the Dify knowledge base retrieval tool."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infra.tool import dify_kb_tool
from src.infra.tool.dify_kb_tool import (
    _batch_retrieve,
    _is_dify_kb_configured,
    _llm_decide_retrieval,
    _persona_dataset_ids,
    _rerank_segments,
    _truncate_query,
    dify_kb_retrieve,
    get_dify_kb_retrieve_tool,
)
from src.kernel.config import settings


def _runtime(dataset_ids: list[str] | None = None) -> SimpleNamespace:
    agent_options: dict[str, object] = {}
    if dataset_ids is not None:
        agent_options["dify_kb_dataset_ids"] = dataset_ids
    return SimpleNamespace(config={"configurable": {"agent_options": agent_options}})


def _configure_dify(
    *,
    enabled: bool = True,
    base_url: str = "https://api.dify.ai/v1",
    api_key: str = "dataset-test",
    llm_model_id: str = "llm-1",
    rerank_model_id: str = "rerank-1",
) -> None:
    settings.DIFY_KB_ENABLED = enabled
    settings.DIFY_KB_BASE_URL = base_url
    settings.DIFY_KB_API_KEY = api_key
    settings.DIFY_KB_LLM_MODEL_ID = llm_model_id
    settings.DIFY_KB_RERANK_MODEL_ID = rerank_model_id


# ---------------------------------------------------------------------------
# gating + persona binding
# ---------------------------------------------------------------------------


def test_is_configured_requires_all_fields() -> None:
    _configure_dify()
    assert _is_dify_kb_configured() is True

    settings.DIFY_KB_RERANK_MODEL_ID = ""
    assert _is_dify_kb_configured() is False

    settings.DIFY_KB_RERANK_MODEL_ID = "rerank-1"
    settings.DIFY_KB_API_KEY = ""
    assert _is_dify_kb_configured() is False


def test_persona_dataset_ids_reads_runtime() -> None:
    assert _persona_dataset_ids(_runtime(["a", "b"])) == ["a", "b"]
    assert _persona_dataset_ids(_runtime(None)) == []
    assert _persona_dataset_ids(_runtime([])) == []
    assert _persona_dataset_ids(SimpleNamespace()) == []


def test_truncate_query_enforces_dify_limit() -> None:
    assert _truncate_query("x" * 300) == "x" * 250
    assert _truncate_query("short") == "short"


# ---------------------------------------------------------------------------
# tool entry point: no KB bound -> empty no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_returns_noop_when_persona_has_no_kb() -> None:
    _configure_dify()
    result = await dify_kb_retrieve.coroutine(query="hello", runtime=_runtime([]))
    payload = json.loads(result)
    assert payload["success"] is False
    assert payload["records"] == []
    assert "未配置" in payload["reason"]


@pytest.mark.asyncio
async def test_retrieve_returns_noop_when_runtime_missing_binding() -> None:
    _configure_dify()
    result = await dify_kb_retrieve.coroutine(query="hello", runtime=_runtime(None))
    payload = json.loads(result)
    assert payload["success"] is False


# ---------------------------------------------------------------------------
# step 1: LLM rewrite + gate, with fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_decide_retrieval_falls_back_on_card_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_dify()
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config",
        AsyncMock(return_value=None),
    )
    decision = await _llm_decide_retrieval(
        question="what is dify?",
        datasets=[{"dataset_id": "ds-1", "description": ""}],
    )
    assert decision["need_retrieval"] is True
    assert decision["retrieval_queries"] == [{"dataset_id": "ds-1", "query": "what is dify?"}]


@pytest.mark.asyncio
async def test_llm_decide_retrieval_respects_need_retrieval_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_dify()
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config",
        AsyncMock(return_value={"model": "m", "api_base": "https://x", "api_key": "k"}),
    )

    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({"need_retrieval": False, "retrieval_queries": []})}}]}

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(dify_kb_tool.httpx, "AsyncClient", _Client)
    decision = await _llm_decide_retrieval(
        question="hi",
        datasets=[{"dataset_id": "ds-1", "description": ""}],
    )
    assert decision == {"need_retrieval": False, "retrieval_queries": []}
    assert captured["url"] == "/v1/chat/completions"
    assert captured["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_llm_decide_retrieval_filters_unknown_dataset_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_dify()
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config",
        AsyncMock(return_value={"model": "m", "api_base": "https://x", "api_key": "k"}),
    )

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "need_retrieval": True,
                                    "retrieval_queries": [
                                        {"dataset_id": "ds-1", "query": "good query"},
                                        {"dataset_id": "ds-unknown", "query": "bad"},
                                        {"dataset_id": "ds-1", "query": ""},
                                    ],
                                }
                            )
                        }
                    }
                ]
            }

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return _Resp()

    monkeypatch.setattr(dify_kb_tool.httpx, "AsyncClient", _Client)
    decision = await _llm_decide_retrieval(
        question="q",
        datasets=[{"dataset_id": "ds-1", "description": ""}],
    )
    assert decision["need_retrieval"] is True
    assert decision["retrieval_queries"] == [{"dataset_id": "ds-1", "query": "good query"}]


# ---------------------------------------------------------------------------
# step 2: batch retrieve + dedupe + single-KB failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_retrieve_dedupes_and_isolates_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_dify()

    call_log: list[str] = []

    class _Resp:
        def __init__(self, body, raise_status: bool = True) -> None:
            self._body = body
            self._raise = raise_status

        def raise_for_status(self) -> None:
            if not self._raise:
                raise Exception("boom")

        def json(self) -> dict:
            return self._body

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            call_log.append(url)
            if url.endswith("/ds-2/retrieve"):
                return _Resp({}, raise_status=False)
            return _Resp(
                {
                    "records": [
                        {
                            "segment": {
                                "id": "seg-A",
                                "document_id": "d1",
                                "content": "chunk A",
                                "position": 0,
                                "document": {"name": "docA.txt"},
                            },
                            "score": 0.9,
                        }
                    ]
                }
            )

    monkeypatch.setattr(dify_kb_tool.httpx, "AsyncClient", _Client)
    merged = await _batch_retrieve(
        retrieval_queries=[
            {"dataset_id": "ds-1", "query": "q1"},
            {"dataset_id": "ds-2", "query": "q2"},  # fails
        ],
        top_k=10,
        score_threshold=0.4,
    )
    assert len(merged) == 1
    assert merged[0]["segment_id"] == "seg-A"
    assert merged[0]["dataset_id"] == "ds-1"
    assert merged[0]["document_name"] == "docA.txt"
    # both datasets were attempted despite one failing
    assert any(u.endswith("/ds-2/retrieve") for u in call_log)


@pytest.mark.asyncio
async def test_batch_retrieve_dedupes_same_segment_across_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_dify()

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "records": [
                    {"segment": {"id": "seg-X", "content": "c", "document": {"name": "n"}}, "score": 0.5}
                ]
            }

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return _Resp()

    monkeypatch.setattr(dify_kb_tool.httpx, "AsyncClient", _Client)
    merged = await _batch_retrieve(
        retrieval_queries=[
            {"dataset_id": "ds-1", "query": "q1"},
            {"dataset_id": "ds-1", "query": "q2"},
        ],
        top_k=10,
        score_threshold=0.4,
    )
    assert len(merged) == 1  # deduped by segment id


# ---------------------------------------------------------------------------
# step 3: rerank short-circuit + fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_short_circuits_when_pool_within_top_n() -> None:
    segments = [{"segment_id": f"s{i}", "content": f"c{i}"} for i in range(3)]
    ranked = await _rerank_segments(query="q", segments=segments, top_n=5)
    assert ranked == segments  # no rerank call needed


@pytest.mark.asyncio
async def test_rerank_falls_back_on_card_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_dify()
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config",
        AsyncMock(return_value=None),
    )
    segments = [{"segment_id": f"s{i}", "content": f"c{i}"} for i in range(8)]
    ranked = await _rerank_segments(query="q", segments=segments, top_n=3)
    assert ranked == segments[:3]


@pytest.mark.asyncio
async def test_rerank_reorders_by_relevance_score(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_dify()
    monkeypatch.setattr(
        "src.infra.llm.client.LLMClient.get_card_config",
        AsyncMock(return_value={"model": "rm", "api_base": "https://x", "api_key": "k"}),
    )

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"results": [{"index": 2, "relevance_score": 0.99}, {"index": 0, "relevance_score": 0.8}]}

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return _Resp()

    monkeypatch.setattr(dify_kb_tool.httpx, "AsyncClient", _Client)
    segments = [{"segment_id": f"s{i}", "content": f"c{i}"} for i in range(8)]
    ranked = await _rerank_segments(query="q", segments=segments, top_n=2)
    assert [r["segment_id"] for r in ranked] == ["s2", "s0"]
    assert ranked[0]["score"] == 0.99


# ---------------------------------------------------------------------------
# tool factory
# ---------------------------------------------------------------------------


def test_get_tool_returns_named_tool() -> None:
    tool = get_dify_kb_retrieve_tool()
    assert tool.name == "dify_kb_retrieve"
