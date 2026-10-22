"""Graph-enhanced retrieval: entity/relation seeds -> 2-hop subgraph -> personalized PageRank."""

from __future__ import annotations

from typing import Any

from src.infra.knowledge_base import embedding, fusion, vector_store
from src.infra.knowledge_base.graph_build import graph_collection_of
from src.infra.knowledge_base.graph_storage import GRAPH_DONE, GraphStorage
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.knowledge_base.vector_store import VectorHit
from src.infra.logging import get_logger
from src.kernel.config import settings

logger = get_logger(__name__)

SEED_TOP_K = 10
ENTITY_SEED_WEIGHT = 1.0
RELATION_SEED_WEIGHT = 0.8
CHUNK_SEED_WEIGHT = 0.3
MAX_HOPS = 2
MAX_NODES = 2000
EDGE_FETCH_LIMIT = 20000
PPR_ALPHA = 0.85
PPR_MAX_ITER = 50
PPR_TOLERANCE = 1e-6
GRAPH_TOP_CHUNKS = 20
CHUNK_LINKS_PER_ENTITY = 50


def personalized_pagerank(
    adjacency: dict[str, list[str]],
    personalization: dict[str, float],
    *,
    alpha: float = PPR_ALPHA,
    max_iter: int = PPR_MAX_ITER,
    tolerance: float = PPR_TOLERANCE,
) -> dict[str, float]:
    """Power-iteration PageRank on an undirected graph, restarting at ``personalization``.

    Mass of dangling nodes is redistributed along the personalization vector.
    """
    nodes = list(adjacency)
    total = sum(w for n, w in personalization.items() if n in adjacency and w > 0)
    if not nodes or total <= 0:
        return {}
    restart = {
        n: personalization.get(n, 0.0) / total for n in nodes if personalization.get(n, 0.0) > 0
    }
    rank = dict(restart)
    for n in nodes:
        rank.setdefault(n, 0.0)
    for _ in range(max_iter):
        nxt = {n: (1.0 - alpha) * restart.get(n, 0.0) for n in nodes}
        dangling = 0.0
        for node, score in rank.items():
            neighbours = adjacency[node]
            if not neighbours:
                dangling += score
                continue
            share = alpha * score / len(neighbours)
            for other in neighbours:
                nxt[other] += share
        if dangling:
            for n, w in restart.items():
                nxt[n] += alpha * dangling * w
        delta = sum(abs(nxt[n] - rank[n]) for n in nodes)
        rank = nxt
        if delta < tolerance:
            break
    return rank


def seed_weights(
    entity_hits: list[tuple[str, float]],
    relation_hits: list[tuple[str, str, float]],
    chunk_entities: list[tuple[list[str], float]],
) -> dict[str, float]:
    """Entity score*1.0, relation ends score*0.8, regular-hit entities chunk_score*0.3; normalised."""
    weights: dict[str, float] = {}
    for ent, score in entity_hits:
        weights[ent] = weights.get(ent, 0.0) + max(score, 0.0) * ENTITY_SEED_WEIGHT
    for src, tgt, score in relation_hits:
        for ent in (src, tgt):
            weights[ent] = weights.get(ent, 0.0) + max(score, 0.0) * RELATION_SEED_WEIGHT
    for ents, score in chunk_entities:
        for ent in ents:
            weights[ent] = weights.get(ent, 0.0) + max(score, 0.0) * CHUNK_SEED_WEIGHT
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total > 0 else {}


async def _expand(
    gs: GraphStorage, kb_id: str, seeds: set[str]
) -> tuple[set[str], list[dict[str, Any]]]:
    """Up to two hops of entity -> relation -> entity, capped at ``MAX_NODES`` entities."""
    nodes = set(seeds)
    edges: dict[str, dict[str, Any]] = {}
    frontier = set(seeds)
    for _ in range(MAX_HOPS):
        if not frontier or len(nodes) >= MAX_NODES:
            break
        found = await gs.triples_touching(kb_id, sorted(frontier), EDGE_FETCH_LIMIT)
        nxt: set[str] = set()
        for tri in found:
            for end in (tri["source_id"], tri["target_id"]):
                if end not in nodes and len(nodes) < MAX_NODES:
                    nodes.add(end)
                    nxt.add(end)
            edges[tri["_id"]] = tri
        frontier = nxt
    kept = [t for t in edges.values() if t["source_id"] in nodes and t["target_id"] in nodes]
    return nodes, kept


async def graph_chunk_hits(
    kb: dict[str, Any],
    query_vec: list[float],
    regular_hits: list[VectorHit],
) -> list[VectorHit]:
    """Top chunks of one KB ranked by personalized PageRank (empty when nothing seeds)."""
    kb_id = str(kb["_id"])
    collection = graph_collection_of(kb)
    if not collection:
        return []
    store = vector_store.get_vector_store()
    gs = GraphStorage(get_kb_storage())

    ent_found = await store.search_graph(collection, kb_id, "entity", query_vec, SEED_TOP_K)
    rel_found = await store.search_graph(collection, kb_id, "triple", query_vec, SEED_TOP_K)
    triples = {t["_id"]: t for t in await gs.triples_by_ids(kb_id, [h.ref_id for h in rel_found])}
    relation_hits = [
        (triples[h.ref_id]["source_id"], triples[h.ref_id]["target_id"], h.score)
        for h in rel_found
        if h.ref_id in triples
    ]
    mine = [h for h in regular_hits if h.kb_id == kb_id]
    chunk_rows = {c["_id"]: c for c in await gs.chunks_by_ids(kb_id, [h.chunk_id for h in mine])}
    chunk_entities = [
        (list(chunk_rows[h.chunk_id].get("entity_ids") or []), h.score)
        for h in mine
        if h.chunk_id in chunk_rows
    ]
    weights = seed_weights([(h.ref_id, h.score) for h in ent_found], relation_hits, chunk_entities)
    if not weights:
        return []

    nodes, edges = await _expand(gs, kb_id, set(weights))
    entity_rows = await gs.entities_by_ids(kb_id, sorted(nodes))
    adjacency: dict[str, list[str]] = {}

    def link(a: str, b: str) -> None:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    for row in entity_rows:
        adjacency.setdefault(f"e:{row['_id']}", [])
    for tri in edges:
        link(f"e:{tri['source_id']}", f"e:{tri['target_id']}")
    for row in entity_rows:
        # Hub entities can mention thousands of chunks; cap fan-out to keep PPR bounded.
        for chunk_id in (row.get("chunk_ids") or [])[:CHUNK_LINKS_PER_ENTITY]:
            link(f"e:{row['_id']}", f"c:{chunk_id}")
    personalization = {f"e:{k}": v for k, v in weights.items()}
    ranks = personalized_pagerank(adjacency, personalization)
    ranked = sorted(
        ((n[2:], r) for n, r in ranks.items() if n.startswith("c:")),
        key=lambda pair: (-pair[1], pair[0]),
    )[:GRAPH_TOP_CHUNKS]
    if not ranked:
        return []
    rows = {c["_id"]: c for c in await gs.chunks_by_ids(kb_id, [cid for cid, _ in ranked])}
    hits: list[VectorHit] = []
    for chunk_id, score in ranked:
        chunk_row = rows.get(chunk_id)
        if chunk_row is None or chunk_row.get("graph_status") != GRAPH_DONE:
            continue
        hits.append(
            VectorHit(
                chunk_id=chunk_id,
                kb_id=kb_id,
                doc_id=str(chunk_row["doc_id"]),
                index=int(chunk_row.get("index", 0)),
                text=str(chunk_row.get("text", "")),
                start_line=chunk_row.get("start_line"),
                end_line=chunk_row.get("end_line"),
                score=score,
            )
        )
    return hits


async def augment(
    bases: list[dict[str, Any]],
    candidates: list[VectorHit],
    query: str,
    query_vec: list[float] | None,
) -> tuple[list[VectorHit], dict[str, list[str]]]:
    """Fuse regular hits with graph hits via RRF. Any failure degrades to the regular hits."""
    default_sources = {h.chunk_id: ["chunk"] for h in candidates}
    if not getattr(settings, "KB_ALLOW_GRAPH", True):
        return candidates, default_sources
    try:
        eligible: list[dict[str, Any]] = []
        for kb in bases:
            if not (kb.get("graph_enabled") and kb.get("embedding")):
                continue
            if await GraphStorage(get_kb_storage()).count_status(str(kb["_id"]), GRAPH_DONE):
                eligible.append(kb)
        if not eligible:
            return candidates, default_sources
        card = await embedding.resolve_embedding_card()
        if card is None:
            return candidates, default_sources
        vec = query_vec
        if vec is None:
            vec = (await embedding.embed_texts([query], card))[0]
        graph_hits: list[VectorHit] = []
        for kb in eligible:
            emb = kb["embedding"]
            if card.model_name != emb.get("model_name") or len(vec) != int(emb.get("dim") or 0):
                continue
            graph_hits.extend(await graph_chunk_hits(kb, vec, candidates))
        if not graph_hits:
            return candidates, default_sources
        fused, sources = fusion.rrf_fuse(candidates, graph_hits)
        return fused, sources
    except Exception:
        logger.exception("[KB] graph retrieval failed; falling back to regular results")
        return candidates, default_sources
