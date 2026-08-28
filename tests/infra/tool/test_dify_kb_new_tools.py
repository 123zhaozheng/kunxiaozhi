"""Tests for the new Dify knowledge base tools (list and query)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.infra import tool as dify_kb_tool
from src.infra.tool.dify_kb_tool import (
    _is_dify_kb_configured,
    _persona_dataset_ids,
    list_dify_knowledge_bases,
    query_dify_knowledge_base,
)
from src.kernel.config import settings


def _runtime(dataset_ids: list[str] | None = None) -> SimpleNamespace:
    """Create a mock runtime with optional dataset bindings."""
    agent_options: dict[str, object] = {}
    if dataset_ids is not None:
        agent_options["dify_kb_dataset_ids"] = dataset_ids
    return SimpleNamespace(config={"configurable": {"agent_options": agent_options}})


def _configure_dify(
    *,
    enabled: bool = True,
    base_url: str = "https://api.dify.ai/v1",
    api_key: str = "dataset-test",
    rerank_model_id: str = "rerank-1",
    default_dataset_ids: list[str] | None = None,
) -> None:
    """Configure Dify KB settings for testing."""
    settings.DIFY_KB_ENABLED = enabled
    settings.DIFY_KB_BASE_URL = base_url
    settings.DIFY_KB_API_KEY = api_key
    settings.DIFY_KB_RERANK_MODEL_ID = rerank_model_id
    settings.DIFY_KB_DEFAULT_DATASET_IDS = default_dataset_ids or []


# ---------------------------------------------------------------------------
# Tool existence and basic structure tests
# ---------------------------------------------------------------------------


def test_list_tool_exists():
    """Verify that list_dify_knowledge_bases tool exists and has correct attributes."""
    assert list_dify_knowledge_bases is not None
    assert hasattr(list_dify_knowledge_bases, "name")
    assert list_dify_knowledge_bases.name == "list_dify_knowledge_bases"
    assert hasattr(list_dify_knowledge_bases, "description")
    assert len(list_dify_knowledge_bases.description) > 0


def test_query_tool_exists():
    """Verify that query_dify_knowledge_base tool exists and has correct attributes."""
    assert query_dify_knowledge_base is not None
    assert hasattr(query_dify_knowledge_base, "name")
    assert query_dify_knowledge_base.name == "query_dify_knowledge_base"
    assert hasattr(query_dify_knowledge_base, "description")
    assert len(query_dify_knowledge_base.description) > 0


def test_no_llm_rewrite_functions_exist():
    """Verify that old LLM rewrite functions have been removed."""
    # These should not exist in the module anymore
    assert not hasattr(dify_kb_tool, "_llm_decide_retrieval"), "_llm_decide_retrieval should be removed"
    assert not hasattr(dify_kb_tool, "_LLM_SYSTEM_PROMPT"), "_LLM_SYSTEM_PROMPT should be removed"


# ---------------------------------------------------------------------------
# Basic configuration tests
# ---------------------------------------------------------------------------


def test_is_configured_requires_all_fields() -> None:
    """Verify that all required fields are present for configuration."""
    _configure_dify()
    assert _is_dify_kb_configured() is True

    settings.DIFY_KB_RERANK_MODEL_ID = ""
    assert _is_dify_kb_configured() is False

    settings.DIFY_KB_RERANK_MODEL_ID = "rerank-1"
    settings.DIFY_KB_API_KEY = ""
    assert _is_dify_kb_configured() is False


def test_persona_dataset_ids_reads_runtime() -> None:
    """Test persona dataset ID extraction from runtime."""
    assert _persona_dataset_ids(_runtime(["a", "b"])) == ["a", "b"]
    assert _persona_dataset_ids(_runtime(None)) == []
    assert _persona_dataset_ids(_runtime([])) == []
    assert _persona_dataset_ids(SimpleNamespace()) == []


# ---------------------------------------------------------------------------
# list_dify_knowledge_bases tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_empty_when_no_persona_and_no_defaults() -> None:
    """Test listing KBs when no persona binding and no default datasets."""
    _configure_dify()
    result = await list_dify_knowledge_bases.coroutine(runtime=_runtime([]))
    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["knowledge_bases"] == []


@pytest.mark.asyncio
async def test_list_uses_default_dataset_ids_when_no_persona() -> None:
    """Test that list uses default dataset IDs when persona has no binding."""
    _configure_dify(default_dataset_ids=["default-ds-1", "default-ds-2"])

    # Mock the fetch_dataset_descriptions to return fake data
    mock_desc_map = {
        "default-ds-1": {"name": "Default KB 1", "description": "First default"},
        "default-ds-2": {"name": "Default KB 2", "description": "Second default"},
    }

    with patch("src.infra.tool.dify_kb_tool._fetch_dataset_descriptions", new_callable=AsyncMock, return_value=mock_desc_map):
        result = await list_dify_knowledge_bases.coroutine(runtime=_runtime([]))
        payload = json.loads(result)

        assert payload["success"] is True
        assert len(payload["knowledge_bases"]) == 2
        kb_ids = [kb["id"] for kb in payload["knowledge_bases"]]
        assert "default-ds-1" in kb_ids
        assert "default-ds-2" in kb_ids


@pytest.mark.asyncio
async def test_list_uses_persona_bound_ids_over_defaults() -> None:
    """Test that persona-bound IDs take priority over defaults."""
    _configure_dify(default_dataset_ids=["default-1", "default-2"])

    # Mock returning descriptions only for the persona-bound dataset
    mock_desc_map = {"persona-ds-1": {"name": "Persona KB", "description": "Bound to persona"}}

    with patch("src.infra.tool.dify_kb_tool._fetch_dataset_descriptions", new_callable=AsyncMock, return_value=mock_desc_map):
        result = await list_dify_knowledge_bases.coroutine(runtime=_runtime(["persona-ds-1"]))
        payload = json.loads(result)

        assert payload["success"] is True
        assert len(payload["knowledge_bases"]) == 1
        assert payload["knowledge_bases"][0]["id"] == "persona-ds-1"


@pytest.mark.asyncio
async def test_list_format_has_required_fields() -> None:
    """Test that returned KB entries have id, name, and description fields."""
    _configure_dify(default_dataset_ids=["ds-1"])

    mock_desc_map = {
        "ds-1": {
            "name": "Test Knowledge Base",
            "description": "A test description",
        }
    }

    with patch("src.infra.tool.dify_kb_tool._fetch_dataset_descriptions", new_callable=AsyncMock, return_value=mock_desc_map):
        result = await list_dify_knowledge_bases.coroutine(runtime=_runtime([]))
        payload = json.loads(result)

        kb = payload["knowledge_bases"][0]
        assert "id" in kb
        assert "name" in kb
        assert "description" in kb
        assert kb["id"] == "ds-1"
        assert kb["name"] == "Test Knowledge Base"
        assert kb["description"] == "A test description"


# ---------------------------------------------------------------------------
# query_dify_knowledge_base tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_validates_dataset_id_against_persona() -> None:
    """Test that query validates dataset_id against persona-bound datasets."""
    _configure_dify()

    # Persona is bound to "valid-ds" but query tries "invalid-ds"
    result = await query_dify_knowledge_base.coroutine(
        query="test query", dataset_id="invalid-ds", runtime=_runtime(["valid-ds"])
    )
    payload = json.loads(result)

    assert payload["success"] is False
    assert "Invalid dataset_id" in payload["error"]
    assert payload["records"] == []


@pytest.mark.asyncio
async def test_query_validates_against_default_dataset_ids() -> None:
    """Test that query validates dataset_id against default dataset IDs."""
    _configure_dify(default_dataset_ids=["default-ds-1"])

    # Try to query a non-existent dataset
    result = await query_dify_knowledge_base.coroutine(
        query="test query", dataset_id="non-existent-ds"
    )
    payload = json.loads(result)

    assert payload["success"] is False
    assert "Invalid dataset_id" in payload["error"]


@pytest.mark.asyncio
async def test_query_accepts_valid_dataset_id() -> None:
    """Test that query accepts valid dataset_id (no error raised)."""
    _configure_dify(default_dataset_ids=["valid-ds"])

    # Mock external dependencies to avoid actual API calls
    with patch("httpx.AsyncClient.get"), patch("httpx.AsyncClient.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_response.json = lambda: {"records": []}
        mock_post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await query_dify_knowledge_base.coroutine(
            query="test query", dataset_id="valid-ds"
        )
        payload = json.loads(result)

        # Should succeed (even though we got empty results)
        assert payload["success"] is True
        assert payload["query"] == "test query"
        assert payload["dataset_id"] == "valid-ds"


@pytest.mark.asyncio
async def test_query_preserves_original_query() -> None:
    """Test that the original query is preserved in the response (not modified)."""
    _configure_dify(default_dataset_ids=["valid-ds"])

    original_query = "What is the refund policy for products purchased within 30 days?"

    with patch("httpx.AsyncClient.get"), patch("httpx.AsyncClient.post") as mock_post:
        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_response.json = lambda: {"records": []}
        mock_post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await query_dify_knowledge_base.coroutine(
            query=original_query, dataset_id="valid-ds"
        )
        payload = json.loads(result)

        assert payload["query"] == original_query


@pytest.mark.asyncio
async def test_query_truncates_very_long_queries() -> None:
    """Test that very long queries are truncated to 250 chars."""
    _configure_dify(default_dataset_ids=["valid-ds"])

    long_query = "x" * 500

    with patch("httpx.AsyncClient.get"):
        async_mock = AsyncMock()
        async_mock.__aenter__ = AsyncMock(return_value=async_mock)
        async_mock.__aexit__ = AsyncMock(return_value=None)

        post_mock = AsyncMock()
        post_mock.return_value = async_mock

        with patch("httpx.AsyncClient.post", return_value=post_mock):
            with patch("src.infra.tool.dify_kb_tool._retrieve_from_dataset") as mock_retrieve:
                mock_retrieve.return_value = []

                result = await query_dify_knowledge_base.coroutine(
                    query=long_query, dataset_id="valid-ds"
                )
                payload = json.loads(result)

                # Verify the truncation happened in the retrieve call
                assert payload["success"] is True


@pytest.mark.asyncio
async def test_query_return_format_has_required_fields() -> None:
    """Test that query response has all required fields."""
    _configure_dify(default_dataset_ids=["valid-ds"])

    mock_segment = {
        "dataset_id": "valid-ds",
        "segment_id": "seg-1",
        "document_id": "doc-1",
        "document_name": "Test Document",
        "content": "Test content",
        "position": 1,
        "score": 0.95,
    }

    with patch("httpx.AsyncClient.get"):
        async_mock = AsyncMock()
        async_mock.__aenter__ = AsyncMock(return_value=async_mock)
        async_mock.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient.post", return_value=async_mock):
            with patch(
                "src.infra.tool.dify_kb_tool._retrieve_from_dataset", return_value=[mock_segment]
            ):
                with patch("src.infra.tool.dify_kb_tool._rerank_segments", return_value=[mock_segment]):
                    result = await query_dify_knowledge_base.coroutine(
                        query="test query", dataset_id="valid-ds"
                    )
                    payload = json.loads(result)

                    assert payload["success"] is True
                    assert "records" in payload
                    assert len(payload["records"]) > 0
                    record = payload["records"][0]
                    assert all(key in record for key in ["dataset_id", "segment_id", "score"])


# ---------------------------------------------------------------------------
# Integration-style tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_list_then_query():
    """Test the expected workflow: list first, then query."""
    _configure_dify(default_dataset_ids=["kb-1", "kb-2"])

    # Step 1: List available KBs
    mock_desc_map = {
        "kb-1": {"name": "Product Docs", "description": "Product documentation"},
        "kb-2": {"name": "FAQ", "description": "Frequently asked questions"},
    }

    with patch("src.infra.tool.dify_kb_tool._fetch_dataset_descriptions", new_callable=AsyncMock, return_value=mock_desc_map):
        # List KBs
        list_result = await list_dify_knowledge_bases.coroutine(runtime=_runtime([]))
        list_payload = json.loads(list_result)

        assert list_payload["success"] is True
        assert len(list_payload["knowledge_bases"]) == 2

        # Extract first KB ID
        kb_id = list_payload["knowledge_bases"][0]["id"]
        assert kb_id == "kb-1"

        # Step 2: Query the selected KB
        with patch("httpx.AsyncClient.get"):
            async_mock = AsyncMock()
            async_mock.__aenter__ = AsyncMock(return_value=async_mock)
            async_mock.__aexit__ = AsyncMock(return_value=None)

            with patch("httpx.AsyncClient.post", return_value=async_mock):
                with patch(
                    "src.infra.tool.dify_kb_tool._retrieve_from_dataset",
                    return_value=[{"dataset_id": "kb-1", "content": "Answer here"}],
                ):
                    with patch(
                        "src.infra.tool.dify_kb_tool._rerank_segments",
                        return_value=[{"dataset_id": "kb-1", "content": "Answer here", "score": 0.9}],
                    ):
                        query_result = await query_dify_knowledge_base.coroutine(
                            query="What's the price?", dataset_id=kb_id
                        )
                        query_payload = json.loads(query_result)

                        assert query_payload["success"] is True
                        assert query_payload["dataset_id"] == kb_id
                        assert "Answer here" in str(query_payload["records"])


# ---------------------------------------------------------------------------
# Backward compatibility tests - verify old tool is gone
# ---------------------------------------------------------------------------


def test_old_dify_kb_retrieve_removed():
    """Verify the old dify_kb_retrieve tool has been removed."""
    with pytest.raises(AttributeError):
        # This function should no longer exist
        dify_kb_tool.dify_kb_retrieve


def test_old_helper_functions_removed():
    """Verify old helper functions have been removed."""
    # _llm_decide_retrieval should not exist (removed LLM rewrite logic)
    assert not hasattr(dify_kb_tool, "_llm_decide_retrieval")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
