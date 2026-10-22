"""Pure scoring helpers for hybrid retrieval (design §7.4)."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from src.infra.knowledge_base.vector_store import VectorHit


def _best_by_chunk(hits: Sequence[VectorHit]) -> dict[str, VectorHit]:
    best: dict[str, VectorHit] = {}
    for hit in hits:
        current = best.get(hit.chunk_id)
        if current is None or hit.score > current.score:
            best[hit.chunk_id] = hit
    return best


def weighted_fusion(
    dense_hits: Sequence[VectorHit],
    sparse_hits: Sequence[VectorHit],
    *,
    vector_weight: float = 0.7,
) -> list[VectorHit]:
    """Merge by chunk_id: score = w*dense + (1-w)*minmax(sparse); missing leg counts 0.

    Returns hits sorted by fused score desc (ties broken by chunk_id for determinism).
    """
    weight = min(1.0, max(0.0, float(vector_weight)))
    dense = _best_by_chunk(dense_hits)
    sparse = _best_by_chunk(sparse_hits)

    sparse_norm: dict[str, float] = {}
    if sparse:
        low = min(hit.score for hit in sparse.values())
        high = max(hit.score for hit in sparse.values())
        for chunk_id, hit in sparse.items():
            if high > low:
                sparse_norm[chunk_id] = (hit.score - low) / (high - low)
            else:
                sparse_norm[chunk_id] = 1.0 if high > 0 else 0.0

    fused: list[VectorHit] = []
    for chunk_id in dense.keys() | sparse.keys():
        base = dense.get(chunk_id) or sparse[chunk_id]
        dense_score = max(0.0, dense[chunk_id].score) if chunk_id in dense else 0.0
        score = weight * dense_score + (1.0 - weight) * sparse_norm.get(chunk_id, 0.0)
        fused.append(dataclasses.replace(base, score=score))
    fused.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    return fused


def apply_threshold(hits: Sequence[VectorHit], threshold: float) -> list[VectorHit]:
    """Keep hits whose score is at least the threshold (order preserved)."""
    return [hit for hit in hits if hit.score >= threshold]


def apply_rerank_scores(hits: Sequence[VectorHit], scores: Sequence[float]) -> list[VectorHit]:
    """Replace scores with rerank scores and sort desc; len mismatch -> original order."""
    if len(hits) != len(scores):
        return list(hits)
    rescored = [dataclasses.replace(hit, score=float(score)) for hit, score in zip(hits, scores)]
    rescored.sort(key=lambda hit: -hit.score)  # stable: ties keep the retrieval order
    return rescored


RRF_K = 60


def rrf_fuse(
    regular: Sequence[VectorHit],
    graph: Sequence[VectorHit],
    *,
    k: int = RRF_K,
    regular_weight: float = 1.0,
    graph_weight: float = 1.0,
) -> tuple[list[VectorHit], dict[str, list[str]]]:
    """Reciprocal-rank fusion of regular and graph chunk hits.

    Scores are rescaled so a chunk ranked first by every leg scores 1.0. Returns the fused
    hits (score desc, chunk id asc) and ``chunk_id -> sources`` (``chunk`` and/or ``graph``).
    """
    scores: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    hits: dict[str, VectorHit] = {}
    for source, leg, weight in (("chunk", regular, regular_weight), ("graph", graph, graph_weight)):
        ranked = sorted(_best_by_chunk(leg).values(), key=lambda h: (-h.score, h.chunk_id))
        for rank, hit in enumerate(ranked, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + weight / (k + rank)
            sources.setdefault(hit.chunk_id, []).append(source)
            hits.setdefault(hit.chunk_id, hit)
    ceiling = (regular_weight + graph_weight) / (k + 1) or 1.0
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))
    return (
        [dataclasses.replace(hits[cid], score=scores[cid] / ceiling) for cid in ordered],
        sources,
    )
