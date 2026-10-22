# ruff: noqa: F811
"""Route contract for the knowledge graph endpoints and graph-enhanced search."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.infra.knowledge_base import graph_build, graph_search
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from tests.api.test_knowledge_base_routes import BASE, Env, env, kb_env  # noqa: F401

REPLIES = {
    "alice works": {
        "entities": [
            {"name": "Alice", "type": "Person", "attributes": {"role": "dev"}},
            {"name": "Acme", "type": "Org"},
        ],
        "relations": [{"source": "Alice", "target": "Acme", "type": "works at"}],
    },
    "bob works": {
        "entities": [{"name": "Bob", "type": "Person"}, {"name": "Acme", "type": "Org"}],
        "relations": [{"source": "Bob", "target": "Acme", "type": "works at"}],
    },
}


class _LLM:
    async def ainvoke(self, prompt: str):
        for marker, reply in REPLIES.items():
            if marker in prompt.lower():
                return SimpleNamespace(content=json.dumps(reply))
        return SimpleNamespace(content="not json")


@pytest.fixture
def llm(monkeypatch):
    async def _get():
        return _LLM()

    async def _sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(graph_build, "get_extraction_llm", _get)
    monkeypatch.setattr(graph_build, "_sleep", _sleep)
    monkeypatch.setattr(settings, "KB_ALLOW_GRAPH", True)
    monkeypatch.setattr(settings, "KB_GRAPH_MAX_CHUNKS_PER_KB", 100)


async def _run_graph_tasks() -> None:
    storage = get_kb_storage()
    for task in list(storage.tasks.docs):
        if task["type"] == "graph" and task["status"] == "queued":
            claimed = await storage.claim_task(task["_id"], "w", utc_now(), 60)
            await graph_build.run_graph_task(claimed, "w", asyncio.Event(), asyncio.Event())


async def _built_kb(env: Env, files: dict[str, bytes] | None = None) -> str:
    kb = await env.create(name="图谱库")
    for name, data in (
        files or {"a.md": b"Alice works at Acme", "b.md": b"Bob works at Acme"}
    ).items():
        assert (await env.upload(kb["id"], name, data)).status_code == 201
    await env.index_all()
    resp = await env.client.post(f"{BASE}/{kb['id']}/graph/enable", json={"schema": "只要人物"})
    assert resp.status_code == 200, resp.text
    await _run_graph_tasks()
    return kb["id"]


async def test_enable_build_and_status_contract(env: Env, llm) -> None:
    kb_id = await _built_kb(env)
    status = (await env.client.get(f"{BASE}/{kb_id}/graph/status")).json()
    assert set(status) == {
        "enabled", "schema", "total_chunks", "pending", "done", "failed", "excluded",
        "entity_count", "relation_count", "active_task", "last_error", "allow_graph",
    }  # fmt: skip
    assert status["enabled"] is True and status["schema"] == "只要人物"
    assert (status["total_chunks"], status["done"], status["pending"], status["failed"]) == (
        2,
        2,
        0,
        0,
    )
    assert (status["entity_count"], status["relation_count"]) == (3, 2)
    assert status["active_task"] is None and status["allow_graph"] is True

    graph_tasks = [t for t in get_kb_storage().tasks.docs if t["type"] == "graph"]
    assert len(graph_tasks) == 1 and graph_tasks[0]["status"] == "succeeded"
    listed = (await env.client.get(f"{BASE}/{kb_id}")).json()
    assert listed["graph_enabled"] is True and listed["active_task"] is None


async def test_enable_reports_cap_exclusions_and_active_task(env: Env, llm, monkeypatch) -> None:
    monkeypatch.setattr(settings, "KB_GRAPH_MAX_CHUNKS_PER_KB", 1)
    kb = await env.create()
    for name, data in {"a.md": b"Alice works at Acme", "b.md": b"Bob works at Acme"}.items():
        await env.upload(kb["id"], name, data)
    await env.index_all()
    status = (await env.client.post(f"{BASE}/{kb['id']}/graph/enable", json={})).json()
    assert status["pending"] == 1 and status["excluded"] == 1
    assert status["active_task"]["type"] == "graph" and status["active_task"]["status"] == "queued"
    # The graph task never shows up as the KB's ingest task.
    assert (await env.client.get(f"{BASE}/{kb['id']}")).json()["active_task"] is None
    assert (await env.client.get(f"{BASE}/{kb['id']}/tasks")).json()["items"][0]["type"] == "ingest"


async def test_enable_validations_and_switches(env: Env, llm, monkeypatch) -> None:
    kb = await env.create()
    too_long = await env.client.post(f"{BASE}/{kb['id']}/graph/enable", json={"schema": "x" * 1001})
    assert too_long.status_code == 400
    monkeypatch.setattr(settings, "KB_ALLOW_GRAPH", False)
    blocked = await env.client.post(f"{BASE}/{kb['id']}/graph/enable", json={})
    assert blocked.status_code == 403
    assert (await env.client.get(f"{BASE}/{kb['id']}/graph/status")).json()["allow_graph"] is False
    monkeypatch.setattr(settings, "KB_ALLOW_GRAPH", True)
    not_enabled = await env.client.post(f"{BASE}/{kb['id']}/graph/build")
    assert not_enabled.status_code == 400


async def test_permissions_owner_manages_editor_reads_stranger_404(env: Env, llm) -> None:
    kb_id = await _built_kb(env)
    storage = get_kb_storage()
    await storage.insert_member({"_id": "m-ed", "kb_id": kb_id, "user_id": "ed", "role": "editor"})
    await storage.insert_member({"_id": "m-vi", "kb_id": kb_id, "user_id": "vi", "role": "viewer"})

    for user in ("ed", "vi"):
        env.as_user(user)
        for path in ("graph/enable", "graph/build", "graph/disable"):
            assert (await env.client.post(f"{BASE}/{kb_id}/{path}", json={})).status_code == 403
        assert (await env.client.get(f"{BASE}/{kb_id}/graph/status")).status_code == 200
        assert (await env.client.get(f"{BASE}/{kb_id}/graph")).status_code == 200
    env.as_user("ed")
    assert (await env.client.get(f"{BASE}/{kb_id}/graph/failed")).status_code == 200
    env.as_user("vi")
    assert (await env.client.get(f"{BASE}/{kb_id}/graph/failed")).status_code == 403
    env.as_user("stranger")
    for path in ("graph/status", "graph", "graph/failed"):
        assert (await env.client.get(f"{BASE}/{kb_id}/{path}")).status_code == 404
    assert (await env.client.post(f"{BASE}/{kb_id}/graph/enable", json={})).status_code == 404


async def test_subgraph_browse_search_and_truncation(env: Env, llm) -> None:
    kb_id = await _built_kb(env)
    body = (await env.client.get(f"{BASE}/{kb_id}/graph")).json()
    assert set(body) == {"nodes", "edges", "truncated"} and body["truncated"] is False
    names = {n["name"]: n for n in body["nodes"]}
    assert set(names) == {"Alice", "Bob", "Acme"} and names["Acme"]["mention_count"] == 2
    assert set(names["Acme"]) == {"id", "name", "label", "mention_count"}
    assert len(body["edges"]) == 2 and set(body["edges"][0]) == {
        "id",
        "source",
        "target",
        "label",
        "text",
    }
    assert {e["label"] for e in body["edges"]} == {"works at"}

    hit = (
        await env.client.get(f"{BASE}/{kb_id}/graph", params={"q": "ALI", "max_depth": 1})
    ).json()
    assert {n["name"] for n in hit["nodes"]} == {"Alice", "Acme"} and len(hit["edges"]) == 1
    regex_safe = (await env.client.get(f"{BASE}/{kb_id}/graph", params={"q": ".*"})).json()
    assert regex_safe["nodes"] == []

    for params in ({"max_nodes": 9}, {"max_nodes": 501}, {"max_depth": 0}, {"max_depth": 4}):
        assert (await env.client.get(f"{BASE}/{kb_id}/graph", params=params)).status_code == 400

    # many entities: the node cap truncates and edges only connect returned nodes
    gs_entities = get_kb_storage().db["kb_graph_entities"]
    gs_triples = get_kb_storage().db["kb_graph_triples"]
    for i in range(30):
        await gs_entities.insert_one(
            {"_id": f"x{i}", "kb_id": kb_id, "name": f"X{i}", "normalized_name": f"x{i}",
             "label": "Entity", "mention_count": 1, "chunk_ids": [], "doc_ids": []}
        )  # fmt: skip
        await gs_triples.insert_one(
            {"_id": f"t{i}", "kb_id": kb_id, "source_id": f"x{i}", "target_id": "x0",
             "label": "r", "text": "", "chunk_ids": [], "doc_ids": []}
        )  # fmt: skip
    big = (await env.client.get(f"{BASE}/{kb_id}/graph", params={"q": "x", "max_nodes": 10})).json()
    assert len(big["nodes"]) == 10 and big["truncated"] is True
    ids = {n["id"] for n in big["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in big["edges"])


async def test_entity_detail_limits_relations_and_mentions(env: Env, llm) -> None:
    kb_id = await _built_kb(env)
    nodes = (await env.client.get(f"{BASE}/{kb_id}/graph")).json()["nodes"]
    acme = next(n for n in nodes if n["name"] == "Acme")
    detail = (await env.client.get(f"{BASE}/{kb_id}/graph/entities/{acme['id']}")).json()
    assert set(detail) == {"entity", "relations", "mentions", "relations_total", "mentions_total"}
    assert detail["entity"]["label"] == "Org" and detail["mentions_total"] == 2
    assert {m["filename"] for m in detail["mentions"]} == {"a.md", "b.md"}
    assert set(detail["mentions"][0]) == {
        "doc_id",
        "filename",
        "chunk_id",
        "start_line",
        "end_line",
        "snippet",
    }
    assert {r["source_name"] for r in detail["relations"]} == {"Alice", "Bob"}
    alice = next(n for n in nodes if n["name"] == "Alice")
    attrs = (await env.client.get(f"{BASE}/{kb_id}/graph/entities/{alice['id']}")).json()["entity"]
    assert attrs["attributes"] == {"role": "dev"}

    entities = get_kb_storage().db["kb_graph_entities"]
    triples = get_kb_storage().db["kb_graph_triples"]
    for i in range(60):
        await triples.insert_one(
            {"_id": f"r{i}", "kb_id": kb_id, "source_id": acme["id"], "target_id": acme["id"],
             "label": "r", "text": "", "chunk_ids": [], "doc_ids": []}
        )  # fmt: skip
    await entities.update_one(
        {"_id": acme["id"]},
        {"$addToSet": {"chunk_ids": {"$each": [f"a.md:{i}" for i in range(40)]}}},
    )
    capped = (await env.client.get(f"{BASE}/{kb_id}/graph/entities/{acme['id']}")).json()
    assert len(capped["relations"]) == 50 and capped["relations_total"] == 62
    assert len(capped["mentions"]) <= 20 and capped["mentions_total"] == 42

    assert (await env.client.get(f"{BASE}/{kb_id}/graph/entities/missing")).status_code == 404


async def test_failed_samples_and_retry_via_build(env: Env, llm) -> None:
    kb_id = await _built_kb(env, {"a.md": b"Alice works at Acme", "z.md": b"nothing to see"})
    status = (await env.client.get(f"{BASE}/{kb_id}/graph/status")).json()
    assert status["done"] == 1 and status["failed"] == 1
    failed = (await env.client.get(f"{BASE}/{kb_id}/graph/failed", params={"limit": 5})).json()
    assert failed["total"] == 1 and failed["items"][0]["filename"] == "z.md"
    assert set(failed["items"][0]) == {"chunk_id", "doc_id", "filename", "error", "snippet"}
    assert (
        await env.client.get(f"{BASE}/{kb_id}/graph/failed", params={"limit": 0})
    ).status_code == 400

    REPLIES["nothing to see"] = REPLIES["bob works"]
    try:
        built = (await env.client.post(f"{BASE}/{kb_id}/graph/build")).json()
        assert built["count"] == 1 and built["task_id"]
        await _run_graph_tasks()
    finally:
        del REPLIES["nothing to see"]
    status = (await env.client.get(f"{BASE}/{kb_id}/graph/status")).json()
    assert status["done"] == 2 and status["failed"] == 0
    assert (await env.client.post(f"{BASE}/{kb_id}/graph/build")).json() == {
        "task_id": None,
        "count": 0,
    }


async def test_missing_chat_model_surfaces_admin_message(env: Env, llm, monkeypatch) -> None:
    async def _none():
        raise graph_build.GraphConfigError(graph_build.NO_MODEL_MESSAGE)

    monkeypatch.setattr(graph_build, "get_extraction_llm", _none)
    kb_id = await _built_kb(env)
    status = (await env.client.get(f"{BASE}/{kb_id}/graph/status")).json()
    assert status["pending"] == 2 and status["active_task"] is None
    assert status["last_error"] == graph_build.NO_MODEL_MESSAGE


async def test_disable_deletes_graph_data_and_vectors(env: Env, llm) -> None:
    kb_id = await _built_kb(env)
    assert any(env.fakes.vectors.graph.values())
    status = (await env.client.post(f"{BASE}/{kb_id}/graph/disable")).json()
    assert (
        status["enabled"] is False and status["entity_count"] == 0 and status["relation_count"] == 0
    )
    assert status["done"] == 0 and status["pending"] == 0
    assert not any(env.fakes.vectors.graph.values())
    chunks = get_kb_storage().chunks.docs
    assert all(c["graph_status"] is None for c in chunks)


async def test_disable_cancels_a_queued_graph_task(env: Env, llm) -> None:
    kb = await env.create()
    await env.upload(kb["id"], "a.md", b"Alice works at Acme")
    await env.index_all()
    await env.client.post(f"{BASE}/{kb['id']}/graph/enable", json={})
    await env.client.post(f"{BASE}/{kb['id']}/graph/disable")
    tasks = [t for t in get_kb_storage().tasks.docs if t["type"] == "graph"]
    assert [t["status"] for t in tasks] == ["cancelled"]


async def test_deleting_a_document_reclaims_its_graph_contribution(env: Env, llm) -> None:
    kb_id = await _built_kb(env)
    docs = (await env.client.get(f"{BASE}/{kb_id}/documents")).json()["items"]
    alice_doc = next(d for d in docs if d["filename"] == "a.md")
    resp = await env.client.post(
        f"{BASE}/{kb_id}/documents/delete", json={"doc_ids": [alice_doc["id"]]}
    )
    assert resp.status_code == 200
    status = (await env.client.get(f"{BASE}/{kb_id}/graph/status")).json()
    assert (status["entity_count"], status["relation_count"]) == (2, 1)
    assert not any(
        n["name"] == "Alice"
        for n in (await env.client.get(f"{BASE}/{kb_id}/graph")).json()["nodes"]
    )


async def test_reindex_purges_graph_and_ingest_requeues_new_chunks(env: Env, llm) -> None:
    kb_id = await _built_kb(env)
    resp = await env.client.post(f"{BASE}/{kb_id}/reindex")
    assert resp.status_code == 200
    assert (await env.client.get(f"{BASE}/{kb_id}/graph/status")).json()["entity_count"] == 0
    await env.index_all()
    # the ingest worker hook queues graph work for the re-created chunks
    await graph_build.queue_new_chunks(kb_id, "owner-1")
    await _run_graph_tasks()
    status = (await env.client.get(f"{BASE}/{kb_id}/graph/status")).json()
    assert status["done"] == 2 and status["entity_count"] == 3


async def test_search_use_graph_labels_sources_and_degrades(env: Env, llm, monkeypatch) -> None:
    kb_id = await _built_kb(env)

    async def search(**extra):
        resp = await env.client.post(
            f"{BASE}/{kb_id}/search",
            json={"query": "Alice", "top_k": 5, "mode": "hybrid", "use_rerank": False, **extra},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    plain = await search()
    assert all(hit["sources"] == ["chunk"] for hit in plain["results"])

    graph = await search(use_graph=True)
    by_file = {hit["filename"]: hit for hit in graph["results"]}
    assert set(by_file) == {"a.md", "b.md"}
    assert by_file["a.md"]["sources"] == ["chunk", "graph"]
    assert "graph" in by_file["b.md"]["sources"]

    async def boom(*_a, **_k):
        raise RuntimeError("graph stage down")

    monkeypatch.setattr(graph_search, "graph_chunk_hits", boom)
    degraded = await search(use_graph=True)
    assert [h["chunk_id"] for h in degraded["results"]] == [h["chunk_id"] for h in plain["results"]]
    assert all(hit["sources"] == ["chunk"] for hit in degraded["results"])


async def test_cleanup_kb_removes_graph_rows_and_vectors(env: Env, llm) -> None:
    from src.infra.knowledge_base import service

    kb_id = await _built_kb(env)
    storage = get_kb_storage()
    await storage.mark_base_deleting(kb_id)
    assert await service.cleanup_kb(kb_id) is True
    assert storage.db["kb_graph_entities"].docs == [] and storage.db["kb_graph_triples"].docs == []
    assert not any(env.fakes.vectors.graph.values())
