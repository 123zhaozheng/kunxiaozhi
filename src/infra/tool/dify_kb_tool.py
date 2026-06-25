"""Dify knowledge base retrieval tool for 昆小智 agents.

Retrieves relevant segments from one or more Dify knowledge bases selected on the
active persona. Mirrors the flow of https://github.com/123zhaozheng/search_knowledge:

    query
      -> LLM query rewrite + retrieval gate (host LLM, via model card)
      -> per-dataset parallel retrieval against Dify ``/datasets/{id}/retrieve``
      -> merge + dedupe by segment id
      -> external rerank (host reranker, via model card) -> top rerank_top_k

Dify-side reranking is deliberately disabled (``reranking_enable=false``) so the
external reranker is authoritative — it can merge-rank across multiple datasets,
which Dify's in-instance reranker cannot. See prd.md for the full design.
"""

from __future__ import annotations

import asyncio
import json
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

# LLM rewrite prompt (ported from search_knowledge llm_service._create_system_prompt).
# Forces JSON output: {"need_retrieval": bool, "retrieval_queries": [{"dataset_id", "query"}]}
_LLM_SYSTEM_PROMPT = """你是一个智能检索助手。你的任务是分析用户问题,判断是否需要从知识库中检索信息来回答。
可用的知识库:
{dataset_desc}
请按照以下规则进行判断:
1. 如果问题需要特定的事实、数据、文档内容或专业知识才能回答,则需要检索
2. 如果问题是通用常识、闲聊、问候等,则不需要检索
3. 如果需要检索,请为每个相关的知识库生成最优的检索查询语句
4. 检索查询应该简洁、准确,能够匹配到相关文档
你必须以JSON格式返回结果,格式如下:
{{
  "need_retrieval": true/false,
  "retrieval_queries": [
    {{"dataset_id": "知识库ID", "query": "优化后的检索查询"}}
  ]
}}
注意:
- 如果need_retrieval为false,retrieval_queries应为空数组
- 可以为同一个知识库生成多个不同角度的查询
- 查询语句应该提取问题的核心关键词和语义"""


async def _json_dumps_result(data: dict[str, Any]) -> str:
    return await run_blocking_io(json.dumps, data, ensure_ascii=False)


def _is_dify_kb_configured() -> bool:
    """True when the Dify KB tool has all required settings filled in."""
    return bool(
        settings.DIFY_KB_ENABLED
        and settings.DIFY_KB_BASE_URL
        and settings.DIFY_KB_API_KEY
        and settings.DIFY_KB_LLM_MODEL_ID
        and settings.DIFY_KB_RERANK_MODEL_ID
    )


def _resolve_dify_base_url() -> str:
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
    """Clamp a query to Dify's 250-char limit (search_knowledge does not do this)."""
    return query[:DIFY_QUERY_MAX_CHARS]


# ---------------------------------------------------------------------------
# Step 1: LLM query rewrite + retrieval gate
# ---------------------------------------------------------------------------


async def _llm_decide_retrieval(
    *,
    question: str,
    datasets: list[dict[str, str]],
) -> dict[str, Any]:
    """Ask the host LLM whether retrieval is needed and rewrite the per-dataset queries.

    Returns ``{"need_retrieval": bool, "retrieval_queries": [...]}``. Degrades to
    "retrieve with the original question broadcast to every dataset" on any LLM
    error — the LLM is an optimization, not a hard dependency.
    """
    fallback: dict[str, Any] = {
        "need_retrieval": True,
        "retrieval_queries": [
            {"dataset_id": ds["dataset_id"], "query": _truncate_query(question)}
            for ds in datasets
        ],
    }

    try:
        from src.infra.llm.client import LLMClient

        card = await LLMClient.get_card_config(settings.DIFY_KB_LLM_MODEL_ID, kind="chat")
        if not card or not card.get("model") or not card.get("api_base") or not card.get("api_key"):
            logger.warning("[dify_kb_retrieve] LLM card unresolved, falling back to original query")
            return fallback

        dataset_desc = "\n".join(f"- {ds['dataset_id']}: {ds.get('description') or ''}" for ds in datasets)
        system_prompt = _LLM_SYSTEM_PROMPT.format(dataset_desc=dataset_desc)

        async with httpx.AsyncClient(
            base_url=str(card["api_base"]).rstrip("/"),
            headers={
                "Authorization": f"Bearer {card['api_key']}",
                "Content-Type": "application/json",
            },
            timeout=_DIFY_TIMEOUT,
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": card["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            payload = response.json()

        content = (
            payload.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(payload, dict)
            else ""
        )
        decision = json.loads(content) if isinstance(content, str) and content.strip() else None
        if not isinstance(decision, dict):
            return fallback

        need = bool(decision.get("need_retrieval", True))
        if not need:
            return {"need_retrieval": False, "retrieval_queries": []}

        queries = decision.get("retrieval_queries")
        if not isinstance(queries, list):
            return fallback

        valid_ids = {ds["dataset_id"] for ds in datasets}
        cleaned: list[dict[str, str]] = []
        for item in queries:
            if not isinstance(item, dict):
                continue
            ds_id = item.get("dataset_id")
            q = item.get("query")
            if not isinstance(ds_id, str) or ds_id not in valid_ids:
                continue
            if not isinstance(q, str) or not q.strip():
                continue
            cleaned.append({"dataset_id": ds_id, "query": _truncate_query(q)})

        if not cleaned:
            return fallback
        return {"need_retrieval": True, "retrieval_queries": cleaned}
    except Exception as exc:
        logger.warning("[dify_kb_retrieve] LLM rewrite failed (%s), falling back to original query", exc)
        return fallback


# ---------------------------------------------------------------------------
# Step 2: per-dataset parallel retrieval against Dify
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
        document = segment.get("document") if isinstance(segment.get("document"), dict) else {}
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


async def _batch_retrieve(
    *,
    retrieval_queries: list[dict[str, str]],
    top_k: int,
    score_threshold: float,
) -> list[dict[str, Any]]:
    """Fan out retrieval across (dataset_id, query) pairs, merge, dedupe by segment id."""
    base_url = _resolve_dify_base_url()
    headers = {"Authorization": f"Bearer {settings.DIFY_KB_API_KEY}"}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=_DIFY_TIMEOUT) as client:
        tasks = [
            _retrieve_from_dataset(
                client=client,
                dataset_id=item["dataset_id"],
                query=item["query"],
                top_k=top_k,
                score_threshold=score_threshold,
            )
            for item in retrieval_queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning("[dify_kb_retrieve] one dataset retrieval failed: %s", result)
            continue
        for segment in result:
            seg_id = segment.get("segment_id")
            if seg_id is None or seg_id in seen:
                continue
            seen.add(seg_id)
            merged.append(segment)
    return merged


# ---------------------------------------------------------------------------
# Step 3: external rerank
# ---------------------------------------------------------------------------


async def _rerank_segments(*, query: str, segments: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Re-rank the merged pool with the host reranker. Degrades to pool[:top_n] on error."""
    if len(segments) <= top_n:
        return segments[:top_n]

    try:
        from src.infra.llm.client import LLMClient

        card = await LLMClient.get_card_config(settings.DIFY_KB_RERANK_MODEL_ID, kind="rerank")
        if not card or not card.get("model") or not card.get("api_base") or not card.get("api_key"):
            logger.warning("[dify_kb_retrieve] rerank card unresolved, returning pool[:top_n]")
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
        logger.warning("[dify_kb_retrieve] rerank failed (%s), returning pool[:top_n]", exc)
        return segments[:top_n]


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------


@tool
async def dify_kb_retrieve(
    query: Annotated[str, "The user question or search query to look up in the Dify knowledge bases bound to the current persona."],
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
    """Retrieve relevant knowledge segments from the Dify knowledge bases bound to the current persona.

    The tool first rewrites the query with an LLM and decides whether retrieval is
    needed, then searches each bound Dify knowledge base in parallel, merges and
    deduplicates the results, and re-ranks them with a dedicated reranker before
    returning the top segments. Each returned segment includes its source knowledge
    base id, document name, content, and relevance score.

    If the current persona has no Dify knowledge bases bound, the tool returns a
    no-op result explaining that no knowledge base scope is configured — it does
    not raise, so the conversation is not interrupted.
    """
    try:
        dataset_ids = _persona_dataset_ids(runtime)
        if not dataset_ids:
            return await _json_dumps_result(
                {
                    "success": False,
                    "reason": "当前 persona 未配置 Dify 知识库检索范围",
                    "records": [],
                }
            )

        # The LLM prompt benefits from a per-dataset description; we only have ids
        # here (descriptions are not threaded through the persona snapshot), so we
        # pass the ids as-is. The LLM still routes/rewrites per dataset by id.
        datasets = [{"dataset_id": ds_id, "description": ""} for ds_id in dataset_ids]

        decision = await _llm_decide_retrieval(question=query, datasets=datasets)
        if not decision.get("need_retrieval"):
            return await _json_dumps_result(
                {
                    "success": True,
                    "need_retrieval": False,
                    "retrieval_queries": [],
                    "records": [],
                }
            )

        retrieval_queries = decision.get("retrieval_queries") or []
        per_dataset_top_k = int(settings.DIFY_KB_TOP_K) or 10
        threshold = score_threshold if score_threshold is not None else float(settings.DIFY_KB_SCORE_THRESHOLD)

        merged = await _batch_retrieve(
            retrieval_queries=retrieval_queries,
            top_k=per_dataset_top_k,
            score_threshold=threshold,
        )

        rerank_top_k = int(top_k) if top_k is not None else int(settings.DIFY_KB_RERANK_TOP_K)
        ranked = await _rerank_segments(query=query, segments=merged, top_n=rerank_top_k)

        return await _json_dumps_result(
            {
                "success": True,
                "need_retrieval": True,
                "retrieval_queries": retrieval_queries,
                "records": ranked,
            }
        )
    except Exception as exc:
        logger.warning("[dify_kb_retrieve] failed: %s", exc)
        return await _json_dumps_result({"error": f"Dify KB retrieval failed: {exc}"})


def get_dify_kb_retrieve_tool() -> BaseTool:
    return dify_kb_retrieve
