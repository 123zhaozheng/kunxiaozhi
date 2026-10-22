"""Knowledge-graph build: per-chunk LLM extraction, upserts, vectors, reclaim and enqueueing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from src.infra.knowledge_base import embedding, vector_store
from src.infra.knowledge_base.events import push_task_updated
from src.infra.knowledge_base.graph_extract import (
    Extraction,
    build_prompt,
    message_text,
    normalize_extraction,
    parse_extraction_json,
)
from src.infra.knowledge_base.graph_storage import (
    VECTOR_FAILED,
    VECTOR_INDEXED,
    GraphStorage,
)
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.logging import get_logger
from src.kernel.config import settings
from src.kernel.schemas.knowledge_base import (
    KB_STATUS_ACTIVE,
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    TASK_TYPE_GRAPH,
)

logger = get_logger(__name__)

NO_MODEL_MESSAGE = "管理员尚未配置图谱抽取模型，请在管理端「知识库」设置中选择对话模型"
NOT_INDEXED_MESSAGE = "知识库尚未建立索引，无法构建图谱"
MODEL_CHANGED_MESSAGE = "向量模型已变更，请先重建索引后再构建图谱"
VECTOR_FAILED_MESSAGE = "图谱向量写入失败，请稍后点击继续构建重试"
EXTRACT_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 10.0)
PAGE_SIZE = 20
EMBED_EVERY_CHUNKS = 20
VECTOR_BATCH = 200

_sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep


class GraphConfigError(Exception):
    """The build cannot start (no chat model, no index); the task fails with this message."""


class ExtractionFailedError(Exception):
    pass


def graph_collection_of(kb: dict[str, Any]) -> str | None:
    emb = kb.get("embedding") or {}
    if not emb.get("model_name") or not emb.get("dim"):
        return None
    return vector_store.graph_collection_name_for(int(emb["dim"]), str(emb["model_name"]))


async def get_extraction_llm() -> Any:
    """Chat model chosen by the admin; never falls back to non-chat model cards."""
    from src.infra.agent.model_storage import get_model_storage
    from src.infra.llm.client import LLMClient

    model_id = str(
        getattr(settings, "KB_GRAPH_LLM_MODEL_ID", "") or getattr(settings, "DEFAULT_MODEL_ID", "")
    ).strip()
    if not model_id:
        raise GraphConfigError(NO_MODEL_MESSAGE)
    try:
        stored = await get_model_storage().get(model_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[KB] graph model lookup failed: %s", exc)
        stored = None
    if stored is None or (getattr(stored, "kind", None) or "chat") != "chat":
        raise GraphConfigError(NO_MODEL_MESSAGE)
    try:
        return await LLMClient.get_model(model_id=model_id, temperature=0, max_tokens=2048)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[KB] graph model %s unavailable: %s", model_id, exc)
        raise GraphConfigError(NO_MODEL_MESSAGE) from exc


async def extract_chunk(llm: Any, kb: dict[str, Any], chunk: dict[str, Any]) -> Extraction:
    """One strict-JSON extraction with 3 attempts (2s / 10s backoff)."""
    prompt = build_prompt(str(chunk.get("text") or ""), kb.get("graph_schema"))
    last: Exception | None = None
    for attempt in range(EXTRACT_ATTEMPTS):
        if attempt:
            await _sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
        try:
            reply = await llm.ainvoke(prompt)
            data = parse_extraction_json(message_text(getattr(reply, "content", reply)))
            return normalize_extraction(str(kb["_id"]), data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried, then recorded on the chunk
            last = exc
            logger.warning("[KB] graph extraction attempt %s failed: %s", attempt + 1, exc)
    raise ExtractionFailedError(f"{type(last).__name__}: {last}"[:300])


# ── vectors ──────────────────────────────────────────────────


async def delete_graph_vectors(
    kb: dict[str, Any], entity_ids: list[str], triple_ids: list[str]
) -> None:
    collection = graph_collection_of(kb)
    if not collection or not (entity_ids or triple_ids):
        return
    store = vector_store.get_vector_store()
    try:
        await store.delete_graph_points(collection, "entity", entity_ids)
        await store.delete_graph_points(collection, "triple", triple_ids)
    except Exception as exc:  # noqa: BLE001 - orphan vectors only waste space; reads re-check Mongo
        logger.warning("[KB] deleting graph vectors for %s failed: %s", kb.get("_id"), exc)


async def reclaim_documents(kb: dict[str, Any], doc_ids: list[str]) -> None:
    """Remove the documents' graph contribution (entities/triples left empty are deleted)."""
    if not doc_ids:
        return
    entities, triples = await GraphStorage(get_kb_storage()).reclaim(
        str(kb["_id"]), doc_ids=doc_ids
    )
    await delete_graph_vectors(kb, entities, triples)


async def purge_graph(kb: dict[str, Any]) -> None:
    """Delete all graph rows and vectors of a KB and reset its chunks' graph state."""
    kb_id = str(kb["_id"])
    collection = graph_collection_of(kb)
    if collection:
        try:
            await vector_store.get_vector_store().delete_graph_by_kb(collection, kb_id)
        except Exception:
            logger.exception("[KB] deleting graph vectors of %s failed", kb_id)
            raise
    await GraphStorage(get_kb_storage()).purge_kb(kb_id)


class _VectorContext:
    def __init__(self, kb: dict[str, Any], card: embedding.ModelCard, collection: str) -> None:
        self.kb = kb
        self.card = card
        self.collection = collection


async def _vector_context(kb: dict[str, Any]) -> _VectorContext:
    emb = kb.get("embedding") or {}
    if not emb.get("model_name") or not emb.get("dim"):
        raise GraphConfigError(NOT_INDEXED_MESSAGE)
    card = await embedding.resolve_embedding_card(emb.get("model_id") or None)
    if card is None or card.model_name != emb["model_name"]:
        raise GraphConfigError(MODEL_CHANGED_MESSAGE)
    collection = await vector_store.get_vector_store().ensure_graph_collection(
        int(emb["dim"]), card.model_name
    )
    return _VectorContext(kb, card, collection)


async def flush_vectors(ctx: _VectorContext) -> None:
    """Embed every entity / relation still waiting for a vector and write it to Qdrant."""
    gs = GraphStorage(get_kb_storage())
    store = vector_store.get_vector_store()
    kb_id = str(ctx.kb["_id"])
    for kind in ("entity", "triple"):
        while True:
            rows = await gs.list_vector_backlog(kb_id, kind, VECTOR_BATCH)
            if not rows:
                break
            texts = [str(r["name"] if kind == "entity" else r["text"]) for r in rows]
            ids = [str(r["_id"]) for r in rows]
            try:
                vectors = await embedding.embed_texts(texts, ctx.card)
                await store.upsert_graph_points(
                    ctx.collection,
                    [
                        vector_store.GraphPoint(kind, ref, kb_id, text, vec)
                        for ref, text, vec in zip(ids, texts, vectors, strict=True)
                    ],
                )
            except Exception:
                await gs.set_vector_status(kind, ids, VECTOR_FAILED)
                raise
            await gs.set_vector_status(kind, ids, VECTOR_INDEXED)


# ── task runner ──────────────────────────────────────────────


async def run_graph_task(
    task: dict[str, Any], owner: str, cancel: asyncio.Event, lost: asyncio.Event
) -> None:
    """Process the KB's pending chunks; the caller already claimed the task and runs the heartbeat."""
    storage = get_kb_storage()
    gs = GraphStorage(storage)
    task_id, kb_id = task["_id"], task["kb_id"]

    async def finish(status: str, error: str | None = None) -> None:
        if lost.is_set():
            logger.warning(
                "[KB] graph task %s lost its lease; leaving it to the reconciler", task_id
            )
            return
        finished = await storage.finish_task(task_id, owner, status, error)
        if finished is not None:
            await push_task_updated(finished)

    kb = await storage.get_base(kb_id)
    if (
        not getattr(settings, "KB_ALLOW_GRAPH", True)
        or kb is None
        or kb.get("status") != KB_STATUS_ACTIVE
        or not kb.get("graph_enabled")
    ):
        await finish(TASK_SUCCEEDED)
        return
    try:
        llm = await get_extraction_llm()
        ctx = await _vector_context(kb)
    except GraphConfigError as exc:
        await finish(TASK_FAILED, str(exc))
        return
    except Exception:
        logger.exception("[KB] graph task %s setup failed", task_id)
        await finish(TASK_FAILED, "图谱构建初始化失败")
        return

    total = await gs.count_status(kb_id, "pending")
    done = failed = 0
    await storage.update_progress(
        task_id, owner, done=0, failed=0, current_doc_id=None, total=total
    )
    semaphore = asyncio.Semaphore(max(1, int(getattr(settings, "KB_GRAPH_CONCURRENCY", 2))))

    async def cancelled() -> bool:
        if cancel.is_set():
            return True
        current = await storage.get_task(task_id)
        if current is None or current.get("cancel_requested"):
            cancel.set()
        return cancel.is_set()

    async def one(chunk: dict[str, Any]) -> str:
        async with semaphore:
            if cancel.is_set():
                return "skipped"  # untouched chunks stay pending
            try:
                extraction = await extract_chunk(llm, kb, chunk)
                if cancel.is_set():
                    return "skipped"
                # Always reclaim first: a retried chunk may already have contributed counts.
                ents, tris = await gs.reclaim(kb_id, chunk_ids=[chunk["_id"]])
                await delete_graph_vectors(kb, ents, tris)
                await gs.bump_attempt(chunk["_id"])
                await gs.upsert_extraction(kb_id, chunk["_id"], chunk["doc_id"], extraction)
                if not await gs.mark_chunk_done(
                    chunk["_id"],
                    [e.id for e in extraction.entities],
                    [t.id for t in extraction.triples],
                ):
                    # The chunk was reset (graph disabled / reindexed) while we worked.
                    ents, tris = await gs.reclaim(kb_id, chunk_ids=[chunk["_id"]])
                    await delete_graph_vectors(kb, ents, tris)
                    return "skipped"
                return "done"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if not isinstance(exc, ExtractionFailedError):
                    logger.exception("[KB] graph chunk %s crashed", chunk.get("_id"))
                await gs.mark_chunk_failed(chunk["_id"], str(exc))
                return "failed"

    since_flush = 0
    while not await cancelled():
        fresh = await storage.get_base(kb_id)
        if fresh is None or not fresh.get("graph_enabled"):
            cancel.set()
            break
        page = await gs.list_pending(kb_id, PAGE_SIZE)
        if not page:
            break
        outcomes = await asyncio.gather(*(one(c) for c in page))
        done += outcomes.count("done")
        failed += outcomes.count("failed")
        if outcomes.count("skipped") == len(page):
            break
        since_flush += len(page)
        if not await storage.update_progress(
            task_id, owner, done=done + failed, failed=failed, current_doc_id=None
        ):
            lost.set()
            cancel.set()
            break
        await push_task_updated(
            {
                "_id": task_id,
                "kb_id": kb_id,
                "type": TASK_TYPE_GRAPH,
                "status": TASK_RUNNING,
                "progress": {"total": total, "done": done + failed, "failed": failed},
            }
        )
        if since_flush >= EMBED_EVERY_CHUNKS:
            since_flush = 0
            try:
                await flush_vectors(ctx)
            except Exception as exc:  # noqa: BLE001 - retried by the next flush
                logger.warning("[KB] graph vector flush failed: %s", exc)

    if lost.is_set():
        logger.warning("[KB] graph task %s lost its lease; leaving it to the reconciler", task_id)
        return
    try:
        await flush_vectors(ctx)
    except Exception:
        logger.exception("[KB] final graph vector flush for %s failed", kb_id)
        await finish(TASK_FAILED, VECTOR_FAILED_MESSAGE)
        return
    await finish(TASK_CANCELLED if cancel.is_set() else TASK_SUCCEEDED)


# ── enqueueing ───────────────────────────────────────────────


async def enqueue_graph_task(kb_id: str, user_id: str) -> dict[str, Any]:
    from src.infra.knowledge_base import queue as kb_queue

    task = await get_kb_storage().enqueue_task(kb_id, [], user_id, TASK_TYPE_GRAPH)
    await kb_queue.publish_task(task["_id"])
    await push_task_updated(task)
    return task


async def queue_new_chunks(kb_id: str, user_id: str) -> int:
    """Incremental hook: mark un-graphed chunks pending and enqueue a build if work exists."""
    storage = get_kb_storage()
    kb = await storage.get_base(kb_id)
    if kb is None or kb.get("status") != KB_STATUS_ACTIVE or not kb.get("graph_enabled"):
        return 0
    if not getattr(settings, "KB_ALLOW_GRAPH", True):
        return 0
    gs = GraphStorage(storage)
    await gs.mark_pending(kb_id, int(settings.KB_GRAPH_MAX_CHUNKS_PER_KB))
    pending = await gs.count_status(kb_id, "pending")
    if pending:
        await enqueue_graph_task(kb_id, user_id)
    return pending
