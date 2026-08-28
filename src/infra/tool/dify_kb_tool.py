"""Dify knowledge base retrieval tools for 昆小智 agents.

Provides two simple, transparent tools that give agents full control over the
retrieval process without internal LLM rewriting:

1. list_dify_knowledge_bases() - Lists available knowledge bases bound to the
   current persona (or system default if no persona is bound). Returns format:
   [{id, name, description}]

2. query_dify_knowledge_base(query: str, dataset_id: str) - Directly retrieves
   relevant segments from a specified knowledge base using the agent's original
   query (no rewriting). Returns segments with score, document info, etc.

Agent workflow:
  1. Call list_dify_knowledge_bases() to see available KBs
  2. Select appropriate dataset_id and call query_dify_knowledge_base(query, dataset_id)

Core features preserved:
  - Dify API calls for single-dataset retrieval
  - Segment deduplication handled by Dify backend within each dataset
  - External reranking (via configured reranker model)
  - Score threshold filtering

Note: All internal LLM query rewrite logic has been removed. Agents now directly
control what queries to send and which datasets to search.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

import httpx
from langchain_core.tools import BaseTool, InjectedToolArg

from src.infra.async_utils import run_blocking_io
from src.infra.logging import get_logger
from src.kernel.config import settings

try:
    from langchain.tools import ToolRuntime  # type: ignore[assignment]
except ImportError:  # pragma: no cover
    import sys

    _mod = type(sys)("langchain.tools")
    _mod.ToolRuntime = Any  # type: ignore[attr-defined]
    sys.modules.setdefault("langchain.tools", _mod)
    from langchain.tools import ToolRuntime  # type: ignore[assignment]

from langchain.tools import tool  # noqa: E402

logger = get_logger(__name__)

DIFY_QUERY_MAX_CHARS = 250  # Dify /retrieve caps query at 250 chars
_DIFY_TIMEOUT = httpx.Timeout(20.0)
_DIFY_LIST_PAGE_LIMIT = 20
_DIFY_LIST_MAX_PAGES = 200  # hard safety cap

# Module-level TTL cache for dataset {id: {name, description}}, keyed by Dify base
# url so multiple instances are isolated. Avoids pulling the full dataset list on
# every retrieval; refreshed on first miss after the TTL window elapses.
_DATASET_DESC_CACHE: dict[str, dict[str, Any]] = {}  # base_url -> {"fetched_at", "by_id"}
_DATASET_DESC_TTL_SECONDS = 300  # 5 min


async def _json_dumps_result(data: dict[str, Any]) -> str:
    """Dump result to JSON string asynchronously."""
    return await run_blocking_io(json.dumps, data, ensure_ascii=False)


def _is_dify_kb_configured() -> bool:
    """True when the Dify KB tool has all required settings filled in."""
    return bool(
        settings.DIFY_KB_ENABLED
        and settings.DIFY_KB_BASE_URL
        and settings.DIFY_KB_API_KEY
        and settings.DIFY_KB_RERANK_MODEL_ID
    )


def _resolve_dify_base_url() -> str:
    """Get the Dify base URL from settings."""
    return str(settings.DIFY_KB_BASE_URL or "").rstrip("/")


def _persona_dataset_ids(runtime: Any) -> list[str]:
    """Read the active persona's bound Dify knowledge-base ids from runtime.

    The ids ride on the request's ``agent_options`` dict (the same channel that
    carries resolved model config to agent nodes), which is threaded through
    ``config["configurable"]["agent_options"]``. Mirrors the
    ``configurable.get("agent_options")`` pattern in agent nodes.
    """
    if runtime is None or not hasattr(runtime, "config") or not runtime.config:
        return []
    config = runtime.config
    if not isinstance(config, dict):
        return []
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return []
    agent_options = configurable.get("agent_options")
    if not isinstance(agent_options, dict):
        return []
    raw = agent_options.get("dify_kb_dataset_ids")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


def _truncate_query(query: str) -> str:
    """Clamp a query to Dify's 250-char limit."""
    return query[:DIFY_QUERY_MAX_CHARS]


# ---------------------------------------------------------------------------
# Dataset Description Lookup
# ---------------------------------------------------------------------------

async def _fetch_dataset_descriptions(dataset_ids: list[str]) -> dict[str, dict[str, str]]:
    """Fetch ``{id: {"name", "description"}}`` for the given dataset ids.

    Pulls Dify ``GET /datasets`` with pagination, cached for ``_DATASET_DESC_TTL_SECONDS``
    per base url. Collection stops early once all requested ids are gathered.
    Degrades to ``{}`` on any error (network/auth/parse) so retrieval is never blocked.
    """
    if not dataset_ids:
        return {}

    base_url = _resolve_dify_base_url()
    now = time.monotonic()
    cached = _DATASET_DESC_CACHE.get(base_url)
    if cached and (now - cached["fetched_at"]) < _DATASET_DESC_TTL_SECONDS:
        return cached["by_id"]

    wanted: set[str] = set(dataset_ids)
    by_id: dict[str, dict[str, str]] = {}
    headers = {"Authorization": f"Bearer {settings.DIFY_KB_API_KEY}"}

    try:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=_DIFY_TIMEOUT) as client:
            for page in range(1, _DIFY_LIST_MAX_PAGES + 1):
                response = await client.get(
                    "/datasets",
                    params={"page": page, "limit": _DIFY_LIST_PAGE_LIMIT},
                )
                response.raise_for_status()
                body = response.json()

                items = body.get("data") if isinstance(body, dict) else None
                if not isinstance(items, list):
                    break

                for item in items:
                    if not isinstance(item, dict):
                        continue
                    ds_id = item.get("id")
                    if not isinstance(ds_id, str) or ds_id not in wanted:
                        continue
                    by_id[ds_id] = {
                        "name": str(item.get("name") or ""),
                        "description": str(item.get("description") or ""),
                    }

                # Early termination once every requested id is collected.
                if wanted.issubset(by_id.keys()):
                    break

                has_more = body.get("has_more") if isinstance(body, dict) else None
                if has_more is False:
                    break
    except Exception as exc:
        logger.warning("[dify_kb] failed to fetch dataset descriptions: %s", exc)
        return {}

    _DATASET_DESC_CACHE[base_url] = {"fetched_at": time.monotonic(), "by_id": by_id}
    return by_id


# ---------------------------------------------------------------------------
# Step 1: Retrieve segments from a single dataset
# ---------------------------------------------------------------------------

async def _retrieve_from_dataset(
    *,
    client: httpx.AsyncClient,
    dataset_id: str,
    query: str,
    top_k: int,
    score_threshold: float,
) -> list[dict[str, Any]]:
    """Retrieve segments from a single Dify dataset. Returns [] on failure."""
    payload = {
        "query": query,
        "retrieval_model": {
            "search_method": settings.DIFY_KB_SEARCH_METHOD or "hybrid_search",
            "reranking_enable": False,  # external reranker is authoritative
            "top_k": top_k,
            "score_threshold_enabled": True,
            "score_threshold": score_threshold,
            "weights": settings.DIFY_KB_SEMANTIC_WEIGHT,
        },
    }
    response = await client.post(f"/datasets/{dataset_id}/retrieve", json=payload)
    response.raise_for_status()
    body = response.json()

    records = body.get("records") if isinstance(body, dict) else None
    if not isinstance(records, list):
        return []

    segments: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        segment = record.get("segment")
        if not isinstance(segment, dict):
            continue
        document = segment.get("document")
        if not isinstance(document, dict):
            document = {}
        segments.append(
            {
                "dataset_id": dataset_id,
                "segment_id": segment.get("id"),
                "document_id": segment.get("document_id"),
                "document_name": document.get("name"),
                "content": segment.get("content"),
                "position": segment.get("position"),
                "score": record.get("score"),
            }
        )
    return segments


# ---------------------------------------------------------------------------
# Step 2: External rerank
# ---------------------------------------------------------------------------

async def _rerank_segments(*, query: str, segments: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Re-rank the merged pool with the host reranker. Degrades to pool[:top_n] on error."""
    if len(segments) <= top_n:
        return segments[:top_n]

    try:
        from src.infra.llm.client import LLMClient

        card = await LLMClient.get_card_config(settings.DIFY_KB_RERANK_MODEL_ID, kind="rerank")
        if not card or not card.get("model") or not card.get("api_base") or not card.get("api_key"):
            logger.warning("[dify_kb] rerank card unresolved, returning pool[:top_n]")
            return segments[:top_n]

        documents = [str(seg.get("content") or "") for seg in segments]
        async with httpx.AsyncClient(
            base_url=str(card["api_base"]).rstrip("/"),
            headers={
                "Authorization": f"Bearer {card['api_key']}",
                "Content-Type": "application/json",
            },
            timeout=_DIFY_TIMEOUT,
        ) as client:
            response = await client.post(
                "/v1/rerank",
                json={
                    "model": card["model"],
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
            )
            response.raise_for_status()
            payload = response.json()

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return segments[:top_n]

        ranked: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int) or not (0 <= index < len(segments)):
                continue
            segment = segments[index]
            segment = {**segment, "score": item.get("relevance_score", segment.get("score"))}
            ranked.append(segment)
        return ranked[:top_n] if ranked else segments[:top_n]
    except Exception as exc:
        logger.warning("[dify_kb] rerank failed (%s), returning pool[:top_n]", exc)
        return segments[:top_n]


# ---------------------------------------------------------------------------
# NEW TOOL 1: List Knowledge Bases
# ---------------------------------------------------------------------------

@tool
async def list_dify_knowledge_bases(
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """List available Dify knowledge bases for the current persona.

    Returns a list of knowledge bases bound to the active persona. If no persona
    is bound, returns the system default knowledge bases configured in
    DIFY_KB_DEFAULT_DATASET_IDS.

    Usage:
        1. First call this tool to see available knowledge bases
        2. Then use query_dify_knowledge_base() with the desired dataset_id

    Returns:
        JSON string with format: {
            "success": true,
            "knowledge_bases": [
                {"id": "dataset-id", "name": "KB Name", "description": "Description"},
                ...
            ]
        }

    Example:
        > list_dify_knowledge_bases()
        {
          "success": true,
          "knowledge_bases": [
            {"id": "abc123", "name": "Product Docs", "description": "Product documentation"},
            {"id": "xyz789", "name": "FAQ", "description": "Frequently asked questions"}
          ]
        }
    """
    try:
        # Get dataset IDs from persona binding or use default
        dataset_ids = _persona_dataset_ids(runtime)
        if not dataset_ids:
            dataset_ids = settings.DIFY_KB_DEFAULT_DATASET_IDS or []

        if not dataset_ids:
            return await _json_dumps_result(
                {
                    "success": True,
                    "knowledge_bases": [],
                    "reason": "当前 persona 未配置知识库，且系统默认知识库为空",
                }
            )

        # Fetch descriptions from Dify API
        desc_map = await _fetch_dataset_descriptions(dataset_ids)
        knowledge_bases = []

        for ds_id in dataset_ids:
            info = desc_map.get(ds_id, {})
            name = info.get("name", ds_id)
            description = info.get("description", "")
            knowledge_bases.append({
                "id": ds_id,
                "name": name,
                "description": description,
            })

        return await _json_dumps_result({
            "success": True,
            "knowledge_bases": knowledge_bases,
        })
    except Exception as exc:
        logger.warning("[list_dify_knowledge_bases] failed: %s", exc)
        return await _json_dumps_result({"error": f"Failed to list knowledge bases: {exc}"})


# ---------------------------------------------------------------------------
# NEW TOOL 2: Query Knowledge Base
# ---------------------------------------------------------------------------

@tool
async def query_dify_knowledge_base(
    query: Annotated[str, "The user's original query (no modification applied)."],
    dataset_id: Annotated[str, "The ID of the knowledge base to query."],
    top_k: Annotated[
        int | None,
        "Optional override for the max number of segments to return after rerank. Defaults to the system setting DIFY_KB_RERANK_TOP_K.",
    ] = None,
    score_threshold: Annotated[
        float | None,
        "Optional override for the minimum relevance score (0.0-1.0). Defaults to the system setting DIFY_KB_SCORE_THRESHOLD.",
    ] = None,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,  # type: ignore[assignment]
) -> str:
    """Query a specific Dify knowledge base with the original query.

    Retrieves relevant segments from a specified knowledge base using the exact
    query provided by the agent (no LLM rewriting or modification). Applies
    deduplication and reranking before returning results.

    Args:
        query: The original user query string (max 250 chars, will be truncated if longer)
        dataset_id: The knowledge base ID to query
        top_k: Override max returned segments (uses DIFY_KB_RERANK_TOP_K if not set)
        score_threshold: Override minimum score threshold (uses DIFY_KB_SCORE_THRESHOLD if not set)

    Returns:
        JSON string with format: {
            "success": true,
            "query": "original query used",
            "dataset_id": "dataset-id",
            "records": [
                {
                    "dataset_id": "dataset-id",
                    "segment_id": "segment-id",
                    "document_id": "doc-id",
                    "document_name": "Document Name",
                    "content": "Segment content...",
                    "position": 1,
                    "score": 0.95
                },
                ...
            ]
        }

    Usage:
        1. First call list_dify_knowledge_bases() to get available dataset_ids
        2. Call this tool with the desired dataset_id and your query

    Example:
        > query_dify_knowledge_base(
        ...     query="What is the refund policy?",
        ...     dataset_id="abc123"
        ... )
        {
          "success": true,
          "query": "What is the refund policy?",
          "dataset_id": "abc123",
          "records": [...]
        }
    """
    try:
        # Validate dataset exists
        dataset_ids = _persona_dataset_ids(runtime)
        if not dataset_ids:
            dataset_ids = settings.DIFY_KB_DEFAULT_DATASET_IDS or []

        if dataset_ids and dataset_id not in dataset_ids:
            # Check if it's a default dataset ID
            if not settings.DIFY_KB_DEFAULT_DATASET_IDS or dataset_id not in settings.DIFY_KB_DEFAULT_DATASET_IDS:
                return await _json_dumps_result(
                    {
                        "success": False,
                        "error": f"Invalid dataset_id '{dataset_id}'. Available IDs: {', '.join(dataset_ids)}",
                        "records": [],
                    }
                )

        per_dataset_top_k = int(settings.DIFY_KB_TOP_K) or 10
        threshold = score_threshold if score_threshold is not None else float(settings.DIFY_KB_SCORE_THRESHOLD)

        # Truncate query to Dify's limit
        truncated_query = _truncate_query(query)

        # Single dataset retrieval
        base_url = _resolve_dify_base_url()
        headers = {"Authorization": f"Bearer {settings.DIFY_KB_API_KEY}"}

        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=_DIFY_TIMEOUT) as client:
            segments = await _retrieve_from_dataset(
                client=client,
                dataset_id=dataset_id,
                query=truncated_query,
                top_k=per_dataset_top_k,
                score_threshold=threshold,
            )

        # Apply external reranking
        rerank_top_k = int(top_k) if top_k is not None else int(settings.DIFY_KB_RERANK_TOP_K)
        ranked = await _rerank_segments(query=query, segments=segments, top_n=rerank_top_k)

        return await _json_dumps_result(
            {
                "success": True,
                "query": query,
                "dataset_id": dataset_id,
                "records": ranked,
            }
        )
    except Exception as exc:
        logger.warning("[query_dify_knowledge_base] failed: %s", exc)
        return await _json_dumps_result({"error": f"Dify KB query failed: {exc}"})


def get_list_dify_knowledge_bases_tool() -> BaseTool:
    """Get the list_dify_knowledge_bases tool."""
    return list_dify_knowledge_bases


def get_query_dify_knowledge_base_tool() -> BaseTool:
    """Get the query_dify_knowledge_base tool."""
    return query_dify_knowledge_base
