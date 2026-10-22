"""Knowledge-graph business logic behind ``/api/kb/{kb_id}/graph`` (design §3, §6)."""

from __future__ import annotations

import asyncio
from typing import Any

from src.infra.knowledge_base import graph_build
from src.infra.knowledge_base import queue as kb_queue
from src.infra.knowledge_base.access import require_kb_access
from src.infra.knowledge_base.errors import KBForbiddenError, KBNotFoundError, KBValidationError
from src.infra.knowledge_base.graph_extract import SCHEMA_MAX
from src.infra.knowledge_base.graph_storage import (
    GRAPH_DONE,
    GRAPH_FAILED,
    GRAPH_PENDING,
    GraphStorage,
)
from src.infra.knowledge_base.service import ensure_enabled
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.logging import get_logger
from src.kernel.config import settings
from src.kernel.schemas.knowledge_base import (
    TASK_FAILED,
    TASK_TYPE_GRAPH,
    KBGraphEdge,
    KBGraphEntity,
    KBGraphEntityDetail,
    KBGraphFailedChunk,
    KBGraphFailedResponse,
    KBGraphMention,
    KBGraphNode,
    KBGraphRelation,
    KBGraphResponse,
    KBGraphStatus,
    KBTask,
    KBTaskEnqueueResponse,
)
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

MAX_NODES_RANGE = (10, 500)
MAX_DEPTH_RANGE = (1, 3)
SEED_LIMIT = 20
EDGE_LIMIT = 5000
ENTITY_RELATIONS_MAX = 50
ENTITY_MENTIONS_MAX = 20
SNIPPET_CHARS = 200
FAILED_LIMIT_MAX = 50
QUERY_MAX = 200


def _allow_graph() -> bool:
    return bool(getattr(settings, "KB_ALLOW_GRAPH", True))


def _require_allowed() -> None:
    if not _allow_graph():
        raise KBForbiddenError("管理员已关闭知识图谱功能")


async def _status(kb: dict[str, Any]) -> KBGraphStatus:
    kb_id = str(kb["_id"])
    storage = get_kb_storage()
    gs = GraphStorage(storage)
    enabled = bool(kb.get("graph_enabled"))
    total = await gs.count_chunks(kb_id)
    pending = await gs.count_status(kb_id, GRAPH_PENDING)
    done = await gs.count_status(kb_id, GRAPH_DONE)
    failed = await gs.count_status(kb_id, GRAPH_FAILED)
    excluded = await gs.count_status(kb_id, None) if enabled else 0
    active = await storage.list_tasks(kb_id, active_only=True, limit=1, task_type=TASK_TYPE_GRAPH)
    last_error: str | None = None
    if not active:
        latest = await storage.list_tasks(kb_id, limit=1, task_type=TASK_TYPE_GRAPH)
        if latest and latest[0].get("status") == TASK_FAILED:
            last_error = latest[0].get("error")
    return KBGraphStatus(
        enabled=enabled,
        schema_text=str(kb.get("graph_schema") or ""),
        total_chunks=total,
        pending=pending,
        done=done,
        failed=failed,
        excluded=excluded,
        entity_count=await gs.count_entities(kb_id),
        relation_count=await gs.count_triples(kb_id),
        active_task=KBTask.model_validate(active[0]) if active else None,
        last_error=last_error,
        allow_graph=_allow_graph(),
    )


async def get_status(user: TokenPayload, kb_id: str) -> KBGraphStatus:
    ensure_enabled()
    access = await require_kb_access(user.sub, kb_id)
    return await _status(access.kb)


async def enable(user: TokenPayload, kb_id: str, schema: str = "") -> KBGraphStatus:
    ensure_enabled()
    _require_allowed()
    access = await require_kb_access(user.sub, kb_id, manage=True)
    schema = (schema or "").strip()
    if len(schema) > SCHEMA_MAX:
        raise KBValidationError(f"抽取约束不能超过 {SCHEMA_MAX} 个字符")
    storage = get_kb_storage()
    updated = await storage.update_base(kb_id, {"graph_enabled": True, "graph_schema": schema})
    if updated is None:
        raise KBNotFoundError()
    gs = GraphStorage(storage)
    await gs.mark_pending(kb_id, int(settings.KB_GRAPH_MAX_CHUNKS_PER_KB))
    if await gs.count_status(kb_id, GRAPH_PENDING):
        await graph_build.enqueue_graph_task(kb_id, access.kb.get("owner_id") or user.sub)
    return await _status(updated)


async def build(user: TokenPayload, kb_id: str) -> KBTaskEnqueueResponse:
    """Continue the build: failed chunks and newly arrived chunks go back to ``pending``."""
    ensure_enabled()
    _require_allowed()
    access = await require_kb_access(user.sub, kb_id, manage=True)
    if not access.kb.get("graph_enabled"):
        raise KBValidationError("请先开启知识图谱")
    gs = GraphStorage(get_kb_storage())
    await gs.retry_failed(kb_id)
    await gs.mark_pending(kb_id, int(settings.KB_GRAPH_MAX_CHUNKS_PER_KB))
    pending = await gs.count_status(kb_id, GRAPH_PENDING)
    if not pending:
        return KBTaskEnqueueResponse(task_id=None, count=0)
    task = await graph_build.enqueue_graph_task(kb_id, user.sub)
    return KBTaskEnqueueResponse(task_id=task["_id"], count=pending)


async def disable(user: TokenPayload, kb_id: str) -> KBGraphStatus:
    ensure_enabled()
    access = await require_kb_access(user.sub, kb_id, manage=True)
    storage = get_kb_storage()
    await storage.update_base(kb_id, {"graph_enabled": False})
    for task in await storage.list_tasks(kb_id, active_only=True, task_type=TASK_TYPE_GRAPH):
        await kb_queue.cancel_task(kb_id, task["_id"])
    # A running extractor may still be writing; wait briefly for its cooperative stop.
    for _ in range(20):
        if not await _graph_running(kb_id):
            break
        await asyncio.sleep(0.25)
    await graph_build.purge_graph(access.kb)
    fresh = await storage.get_base(kb_id) or access.kb
    return await _status(fresh)


async def _graph_running(kb_id: str) -> bool:
    tasks = await get_kb_storage().list_tasks(kb_id, active_only=True, task_type=TASK_TYPE_GRAPH)
    return any(t.get("status") == "running" for t in tasks)


# ── browsing ─────────────────────────────────────────────────


def _node(row: dict[str, Any]) -> KBGraphNode:
    return KBGraphNode(
        id=str(row["_id"]),
        name=str(row.get("name") or ""),
        label=str(row.get("label") or ""),
        mention_count=int(row.get("mention_count") or 0),
    )


async def get_subgraph(
    user: TokenPayload,
    kb_id: str,
    *,
    q: str | None = None,
    max_nodes: int = 100,
    max_depth: int = 2,
) -> KBGraphResponse:
    ensure_enabled()
    await require_kb_access(user.sub, kb_id)
    if not MAX_NODES_RANGE[0] <= max_nodes <= MAX_NODES_RANGE[1]:
        raise KBValidationError("max_nodes 需在 10-500 之间")
    if not MAX_DEPTH_RANGE[0] <= max_depth <= MAX_DEPTH_RANGE[1]:
        raise KBValidationError("max_depth 需在 1-3 之间")
    query = (q or "").strip()
    if len(query) > QUERY_MAX:
        raise KBValidationError(f"搜索内容不能超过 {QUERY_MAX} 个字符")

    gs = GraphStorage(get_kb_storage())
    seed_limit = min(max_nodes, SEED_LIMIT)
    seeds = (
        await gs.search_entities(kb_id, query, seed_limit + 1)
        if query
        else await gs.top_entities(kb_id, seed_limit + 1)
    )
    truncated = len(seeds) > seed_limit
    seeds = seeds[:seed_limit]
    nodes: dict[str, dict[str, Any]] = {str(r["_id"]): r for r in seeds}
    frontier = set(nodes)
    for _ in range(max_depth):
        if not frontier:
            break
        touching = await gs.triples_touching(kb_id, sorted(frontier), EDGE_LIMIT)
        candidates = {
            end
            for tri in touching
            for end in (tri["source_id"], tri["target_id"])
            if end not in nodes
        }
        rows = await gs.entities_by_ids(kb_id, sorted(candidates))
        rows.sort(key=lambda r: (-int(r.get("mention_count") or 0), str(r.get("name") or "")))
        added: set[str] = set()
        for row in rows:
            if len(nodes) >= max_nodes:
                truncated = True
                break
            nodes[str(row["_id"])] = row
            added.add(str(row["_id"]))
        frontier = added
    edges = await gs.triples_among(kb_id, list(nodes), EDGE_LIMIT)
    return KBGraphResponse(
        nodes=[_node(r) for r in nodes.values()],
        edges=[
            KBGraphEdge(
                id=str(t["_id"]),
                source=str(t["source_id"]),
                target=str(t["target_id"]),
                label=str(t.get("label") or ""),
                text=str(t.get("text") or ""),
            )
            for t in edges
        ],
        truncated=truncated,
    )


async def get_entity(user: TokenPayload, kb_id: str, entity_id: str) -> KBGraphEntityDetail:
    ensure_enabled()
    await require_kb_access(user.sub, kb_id)
    storage = get_kb_storage()
    gs = GraphStorage(storage)
    row = await gs.get_entity(kb_id, entity_id)
    if row is None:
        raise KBNotFoundError("实体不存在")

    touching_filter = {
        "kb_id": kb_id,
        "$or": [{"source_id": entity_id}, {"target_id": entity_id}],
    }
    relations_total = int(await gs.triples.count_documents(touching_filter))
    triples = await gs.triples_touching(kb_id, [entity_id], ENTITY_RELATIONS_MAX)
    ends = {t["source_id"] for t in triples} | {t["target_id"] for t in triples}
    names = {
        str(r["_id"]): str(r.get("name") or "")
        for r in await gs.entities_by_ids(kb_id, sorted(ends))
    }

    chunk_ids = sorted(row.get("chunk_ids") or [])
    chunks = {c["_id"]: c for c in await gs.chunks_by_ids(kb_id, chunk_ids[:ENTITY_MENTIONS_MAX])}
    briefs = await storage.document_briefs(list({c["doc_id"] for c in chunks.values()}))
    mentions = [
        KBGraphMention(
            doc_id=str(chunks[cid]["doc_id"]),
            filename=str((briefs.get(chunks[cid]["doc_id"]) or {}).get("filename") or ""),
            chunk_id=cid,
            start_line=chunks[cid].get("start_line"),
            end_line=chunks[cid].get("end_line"),
            snippet=str(chunks[cid].get("text") or "")[:SNIPPET_CHARS],
        )
        for cid in chunk_ids[:ENTITY_MENTIONS_MAX]
        if cid in chunks
    ]
    return KBGraphEntityDetail(
        entity=KBGraphEntity(
            id=str(row["_id"]),
            name=str(row.get("name") or ""),
            label=str(row.get("label") or ""),
            attributes={str(k): str(v) for k, v in (row.get("attributes") or {}).items()},
            mention_count=int(row.get("mention_count") or 0),
        ),
        relations=[
            KBGraphRelation(
                id=str(t["_id"]),
                source_id=str(t["source_id"]),
                target_id=str(t["target_id"]),
                source_name=names.get(t["source_id"], ""),
                target_name=names.get(t["target_id"], ""),
                label=str(t.get("label") or ""),
                text=str(t.get("text") or ""),
            )
            for t in triples
        ],
        mentions=mentions,
        relations_total=relations_total,
        mentions_total=len(chunk_ids),
    )


async def list_failed(user: TokenPayload, kb_id: str, limit: int = 10) -> KBGraphFailedResponse:
    ensure_enabled()
    await require_kb_access(user.sub, kb_id, write=True)
    if not 1 <= limit <= FAILED_LIMIT_MAX:
        raise KBValidationError("limit 需在 1-50 之间")
    storage = get_kb_storage()
    gs = GraphStorage(storage)
    rows = await gs.list_failed(kb_id, limit)
    briefs = await storage.document_briefs(list({r["doc_id"] for r in rows}))
    return KBGraphFailedResponse(
        items=[
            KBGraphFailedChunk(
                chunk_id=str(r["_id"]),
                doc_id=str(r["doc_id"]),
                filename=str((briefs.get(r["doc_id"]) or {}).get("filename") or ""),
                error=str(r.get("graph_error") or ""),
                snippet=str(r.get("text") or "")[:SNIPPET_CHARS],
            )
            for r in rows
        ],
        total=await gs.count_status(kb_id, GRAPH_FAILED),
    )
