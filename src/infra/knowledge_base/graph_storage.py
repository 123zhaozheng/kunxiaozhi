"""MongoDB persistence for the knowledge graph (entities, triples, chunk graph state)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, DESCENDING

from src.infra.knowledge_base.graph_extract import Extraction, normalize_entity_name
from src.infra.utils.datetime import utc_now

if TYPE_CHECKING:
    from src.infra.knowledge_base.storage import KnowledgeBaseStorage

ENTITIES_COLLECTION = "kb_graph_entities"
TRIPLES_COLLECTION = "kb_graph_triples"

GRAPH_PENDING = "pending"
GRAPH_DONE = "done"
GRAPH_FAILED = "failed"
VECTOR_PENDING = "pending"
VECTOR_INDEXED = "indexed"
VECTOR_FAILED = "failed"

_CHUNK_RESET = {"graph_status": None, "graph_attempts": 0, "graph_error": None}
_BATCH = 500


def doc_id_of(chunk_id: str) -> str:
    """Chunk ids are ``{doc_id}:{index}``."""
    return chunk_id.rsplit(":", 1)[0]


class GraphStorage:
    def __init__(self, base: KnowledgeBaseStorage) -> None:
        self._base = base

    @property
    def entities(self) -> Any:
        return self._base.db[ENTITIES_COLLECTION]

    @property
    def triples(self) -> Any:
        return self._base.db[TRIPLES_COLLECTION]

    @property
    def chunks(self) -> Any:
        return self._base.chunks

    async def ensure_indexes(self) -> None:
        ent, tri = self.entities, self.triples
        await ent.create_index(
            [("kb_id", ASCENDING), ("normalized_name", ASCENDING), ("label", ASCENDING)],
            unique=True,
            name="kbg_entity_unique",
        )
        await ent.create_index([("kb_id", ASCENDING), ("mention_count", DESCENDING)])
        await ent.create_index([("kb_id", ASCENDING), ("doc_ids", ASCENDING)])
        await tri.create_index([("kb_id", ASCENDING), ("source_id", ASCENDING)])
        await tri.create_index([("kb_id", ASCENDING), ("target_id", ASCENDING)])
        await tri.create_index([("kb_id", ASCENDING), ("doc_ids", ASCENDING)])
        await self.chunks.create_index([("kb_id", ASCENDING), ("graph_status", ASCENDING)])

    # ── chunk state ─────────────────────────────────────────

    async def mark_pending(self, kb_id: str, cap: int) -> int:
        """Move not-yet-graphed chunks to ``pending`` within the per-KB cap; returns the count."""
        used = await self.chunks.count_documents(
            {"kb_id": kb_id, "graph_status": {"$in": [GRAPH_PENDING, GRAPH_DONE, GRAPH_FAILED]}}
        )
        room = max(0, cap - used)
        if room == 0:
            return 0
        rows = (
            await self.chunks.find({"kb_id": kb_id, "graph_status": None}, {"_id": 1})
            .sort("_id", 1)
            .limit(room)
            .to_list(length=room)
        )
        ids = [r["_id"] for r in rows]
        for start in range(0, len(ids), _BATCH):
            await self.chunks.update_many(
                {"_id": {"$in": ids[start : start + _BATCH]}, "graph_status": None},
                {
                    "$set": {
                        "graph_status": GRAPH_PENDING,
                        "graph_attempts": 0,
                        "graph_error": None,
                        "entity_ids": [],
                        "triple_ids": [],
                    }
                },
            )
        return len(ids)

    async def retry_failed(self, kb_id: str) -> int:
        result = await self.chunks.update_many(
            {"kb_id": kb_id, "graph_status": GRAPH_FAILED},
            {"$set": {"graph_status": GRAPH_PENDING, "graph_attempts": 0, "graph_error": None}},
        )
        return int(result.modified_count)

    async def count_status(self, kb_id: str, status: str | None) -> int:
        return int(await self.chunks.count_documents({"kb_id": kb_id, "graph_status": status}))

    async def count_chunks(self, kb_id: str) -> int:
        return int(await self.chunks.count_documents({"kb_id": kb_id}))

    async def list_pending(self, kb_id: str, limit: int) -> list[dict[str, Any]]:
        cursor = self.chunks.find({"kb_id": kb_id, "graph_status": GRAPH_PENDING})
        return await cursor.sort("_id", 1).limit(limit).to_list(length=limit)

    async def list_failed(self, kb_id: str, limit: int) -> list[dict[str, Any]]:
        cursor = self.chunks.find({"kb_id": kb_id, "graph_status": GRAPH_FAILED})
        return await cursor.sort("_id", 1).limit(limit).to_list(length=limit)

    async def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        return await self.chunks.find_one({"_id": chunk_id})

    async def chunks_by_ids(self, kb_id: str, chunk_ids: list[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        cursor = self.chunks.find({"kb_id": kb_id, "_id": {"$in": chunk_ids}})
        return await cursor.to_list(length=len(chunk_ids))

    async def bump_attempt(self, chunk_id: str) -> None:
        await self.chunks.update_one({"_id": chunk_id}, {"$inc": {"graph_attempts": 1}})

    async def mark_chunk_failed(self, chunk_id: str, error: str) -> None:
        await self.chunks.update_one(
            {"_id": chunk_id, "graph_status": GRAPH_PENDING},
            {"$set": {"graph_status": GRAPH_FAILED, "graph_error": error[:500]}},
        )

    async def mark_chunk_done(
        self, chunk_id: str, entity_ids: list[str], triple_ids: list[str]
    ) -> bool:
        result = await self.chunks.update_one(
            {"_id": chunk_id, "graph_status": GRAPH_PENDING},
            {
                "$set": {
                    "graph_status": GRAPH_DONE,
                    "graph_error": None,
                    "entity_ids": entity_ids,
                    "triple_ids": triple_ids,
                }
            },
        )
        return result.modified_count > 0

    # ── upserts ─────────────────────────────────────────────

    async def upsert_extraction(
        self, kb_id: str, chunk_id: str, doc_id: str, extraction: Extraction
    ) -> None:
        now = utc_now()
        for ent in extraction.entities:
            update: dict[str, Any] = {
                "$setOnInsert": {
                    "kb_id": kb_id,
                    "name": ent.name,
                    "normalized_name": ent.normalized_name,
                    "label": ent.label,
                    "vector_status": VECTOR_PENDING,
                    "created_at": now,
                },
                "$set": {
                    "updated_at": now,
                    **{f"attributes.{k}": v for k, v in ent.attributes.items()},
                },
                "$addToSet": {"chunk_ids": chunk_id, "doc_ids": doc_id},
                "$inc": {"mention_count": 1},
            }
            await self.entities.update_one({"_id": ent.id}, update, upsert=True)
        for tri in extraction.triples:
            await self.triples.update_one(
                {"_id": tri.id},
                {
                    "$setOnInsert": {
                        "kb_id": kb_id,
                        "source_id": tri.source_id,
                        "target_id": tri.target_id,
                        "label": tri.label,
                        "text": tri.text,
                        "vector_status": VECTOR_PENDING,
                        "created_at": now,
                    },
                    "$addToSet": {"chunk_ids": chunk_id, "doc_ids": doc_id},
                },
                upsert=True,
            )

    # ── reclaim ─────────────────────────────────────────────

    async def reclaim(
        self, kb_id: str, *, doc_ids: list[str] | None = None, chunk_ids: list[str] | None = None
    ) -> tuple[list[str], list[str]]:
        """Remove the contribution of documents / chunks from entities and triples.

        Rows left without any chunk are deleted. Returns the deleted (entity ids, triple ids)
        so the caller can drop their vectors.
        """
        gone_docs = set(doc_ids or [])
        gone_chunks = set(chunk_ids or [])

        def removed(chunk_id: str) -> bool:
            return chunk_id in gone_chunks or doc_id_of(chunk_id) in gone_docs

        flt: dict[str, Any] = {"kb_id": kb_id}
        if gone_docs:
            flt["doc_ids"] = {"$in": list(gone_docs)}
        elif gone_chunks:
            flt["chunk_ids"] = {"$in": list(gone_chunks)}
        else:
            return [], []

        deleted: list[list[str]] = []
        for collection, extra in ((self.entities, True), (self.triples, False)):
            touched: list[str] = []
            while True:
                rows = await collection.find(flt).limit(_BATCH).to_list(length=_BATCH)
                rows = [r for r in rows if any(removed(c) for c in r.get("chunk_ids") or [])]
                if not rows:
                    break
                for row in rows:
                    drop = [c for c in row.get("chunk_ids") or [] if removed(c)]
                    keep_docs = {doc_id_of(c) for c in row.get("chunk_ids") or [] if not removed(c)}
                    drop_docs = [d for d in row.get("doc_ids") or [] if d not in keep_docs]
                    update: dict[str, Any] = {"$pullAll": {"chunk_ids": drop, "doc_ids": drop_docs}}
                    if extra:
                        update["$inc"] = {"mention_count": -len(drop)}
                    await collection.update_one({"_id": row["_id"]}, update)
                    touched.append(row["_id"])
            gone: list[str] = []
            for start in range(0, len(touched), _BATCH):
                batch = touched[start : start + _BATCH]
                empties = await collection.find(
                    {"_id": {"$in": batch}, "chunk_ids": []}, {"_id": 1}
                ).to_list(length=len(batch))
                gone.extend(r["_id"] for r in empties)
            if gone:
                await collection.delete_many({"_id": {"$in": gone}, "chunk_ids": []})
            deleted.append(gone)
        return deleted[0], deleted[1]

    async def purge_kb(self, kb_id: str) -> None:
        """Drop every graph row of the KB and reset its chunks' graph state."""
        await self.entities.delete_many({"kb_id": kb_id})
        await self.triples.delete_many({"kb_id": kb_id})
        await self.chunks.update_many(
            {"kb_id": kb_id, "graph_status": {"$ne": None}},
            {"$set": {**_CHUNK_RESET, "entity_ids": [], "triple_ids": []}},
        )

    async def delete_kb_rows(self, kb_id: str) -> None:
        await self.entities.delete_many({"kb_id": kb_id})
        await self.triples.delete_many({"kb_id": kb_id})

    # ── vectors ─────────────────────────────────────────────

    async def list_vector_backlog(self, kb_id: str, kind: str, limit: int) -> list[dict[str, Any]]:
        collection = self.entities if kind == "entity" else self.triples
        cursor = collection.find(
            {"kb_id": kb_id, "vector_status": {"$in": [VECTOR_PENDING, VECTOR_FAILED]}}
        )
        return await cursor.limit(limit).to_list(length=limit)

    async def set_vector_status(self, kind: str, ids: list[str], status: str) -> None:
        if not ids:
            return
        collection = self.entities if kind == "entity" else self.triples
        await collection.update_many({"_id": {"$in": ids}}, {"$set": {"vector_status": status}})

    # ── read side ───────────────────────────────────────────

    async def count_entities(self, kb_id: str) -> int:
        return int(await self.entities.count_documents({"kb_id": kb_id}))

    async def count_triples(self, kb_id: str) -> int:
        return int(await self.triples.count_documents({"kb_id": kb_id}))

    async def entities_by_ids(self, kb_id: str, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        cursor = self.entities.find({"kb_id": kb_id, "_id": {"$in": list(ids)}})
        return await cursor.to_list(length=len(ids))

    async def get_entity(self, kb_id: str, entity_id: str) -> dict[str, Any] | None:
        return await self.entities.find_one({"kb_id": kb_id, "_id": entity_id})

    async def triples_by_ids(self, kb_id: str, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        cursor = self.triples.find({"kb_id": kb_id, "_id": {"$in": list(ids)}})
        return await cursor.to_list(length=len(ids))

    async def top_entities(self, kb_id: str, limit: int) -> list[dict[str, Any]]:
        cursor = self.entities.find({"kb_id": kb_id}).sort(
            [("mention_count", -1), ("normalized_name", 1)]
        )
        return await cursor.limit(limit).to_list(length=limit)

    async def search_entities(self, kb_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        needle = normalize_entity_name(query)
        if not needle:
            return []
        cursor = self.entities.find(
            {"kb_id": kb_id, "normalized_name": {"$regex": re.escape(needle)}}
        ).sort([("mention_count", -1), ("normalized_name", 1)])
        return await cursor.limit(limit).to_list(length=limit)

    async def triples_touching(
        self, kb_id: str, entity_ids: list[str], limit: int
    ) -> list[dict[str, Any]]:
        if not entity_ids or limit <= 0:
            return []
        cursor = self.triples.find(
            {
                "kb_id": kb_id,
                "$or": [
                    {"source_id": {"$in": list(entity_ids)}},
                    {"target_id": {"$in": list(entity_ids)}},
                ],
            }
        )
        return await cursor.sort("_id", 1).limit(limit).to_list(length=limit)

    async def triples_among(
        self, kb_id: str, entity_ids: list[str], limit: int
    ) -> list[dict[str, Any]]:
        if not entity_ids:
            return []
        cursor = self.triples.find(
            {
                "kb_id": kb_id,
                "source_id": {"$in": list(entity_ids)},
                "target_id": {"$in": list(entity_ids)},
            }
        )
        return await cursor.sort("_id", 1).limit(limit).to_list(length=limit)
