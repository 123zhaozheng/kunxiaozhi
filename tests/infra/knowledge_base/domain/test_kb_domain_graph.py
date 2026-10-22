"""Knowledge graph engine: normalisation, JSON tolerance, build, reclaim, PPR and RRF."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from src.infra.knowledge_base import fusion, graph_build, graph_search
from src.infra.knowledge_base.graph_extract import (
    ExtractionParseError,
    entity_id,
    normalize_entity_name,
    normalize_extraction,
    parse_extraction_json,
)
from src.infra.knowledge_base.graph_storage import GraphStorage
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.knowledge_base.vector_store import VectorHit
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings

# ── pure helpers ─────────────────────────────────────────────


def test_normalize_entity_name_collapses_case_and_whitespace() -> None:
    assert normalize_entity_name("  Foo   BAR\tbaz ") == "foo bar baz"


def test_normalize_extraction_defaults_ids_and_merging() -> None:
    data = {
        "entities": [
            {"name": "Alice", "attributes": {"role": "dev", "a.b": 1}},
            {"name": " alice ", "type": "Entity", "attributes": {"city": "X"}},
            {"name": "Acme", "type": "Org"},
        ],
        "relations": [
            {"source": "ALICE", "target": "acme", "type": "works at"},
            {"source": "Alice", "target": "Acme"},
            {"source": "Alice", "target": "Alice", "type": "self"},
            {"source": "Alice", "target": "Ghost", "type": "knows"},
        ],
    }
    first = normalize_extraction("kb", data)
    again = normalize_extraction("kb", json.loads(json.dumps(data)))
    assert [e.id for e in first.entities] == [e.id for e in again.entities]
    assert [t.id for t in first.triples] == [t.id for t in again.triples]

    alice = next(e for e in first.entities if e.normalized_name == "alice")
    assert alice.label == "Entity" and alice.id == entity_id("kb", "alice", "Entity")
    assert alice.attributes == {"role": "dev", "a_b": "1", "city": "X"}
    labels = sorted(t.label for t in first.triples)
    assert labels == ["RELATED_TO", "knows", "works at"]
    assert any(e.normalized_name == "ghost" and e.label == "Entity" for e in first.entities)
    assert next(t for t in first.triples if t.label == "works at").text == "Alice → works at → Acme"
    # different KBs never share ids
    assert normalize_extraction("kb2", data).entities[0].id != first.entities[0].id


def test_normalize_extraction_drops_overlong_names_labels_and_relations() -> None:
    data = {
        "entities": [
            {"name": "x" * 513},
            {"name": "ok", "type": "t" * 129},
            {"name": "fine"},
            {"name": "other"},
        ],
        "relations": [
            {"source": "x" * 513, "target": "fine", "type": "r"},
            {"source": "fine", "target": "other", "type": "r" * 257},
            {"source": "fine", "target": "other", "type": "r" * 256},
        ],
    }
    result = normalize_extraction("kb", data)
    assert sorted(e.name for e in result.entities) == ["fine", "other"]
    assert [len(t.label) for t in result.triples] == [256]


@pytest.mark.parametrize(
    "raw",
    [
        '{"entities": [], "relations": []}',
        '```json\n{"entities": [], "relations": []}\n```',
        'Sure! Here it is: {"entities": [], "relations": []} hope it helps',
    ],
)
def test_parse_extraction_json_is_tolerant(raw: str) -> None:
    assert parse_extraction_json(raw) == {"entities": [], "relations": []}


@pytest.mark.parametrize("raw", ["", "no json here", "[1, 2]", "{broken"])
def test_parse_extraction_json_rejects_garbage(raw: str) -> None:
    with pytest.raises(ExtractionParseError):
        parse_extraction_json(raw)


def test_personalized_pagerank_ranks_near_seed_first() -> None:
    # a - b - c - d chain, restart at a
    adjacency = {"a": ["b"], "b": ["a", "c"], "c": ["b", "d"], "d": ["c"], "lonely": []}
    ranks = graph_search.personalized_pagerank(adjacency, {"a": 1.0})
    order = sorted(("a", "b", "c", "d"), key=lambda n: -ranks[n])
    # b is the seed's only neighbour and also touches c, so it edges out the seed itself
    assert order == ["b", "a", "c", "d"]
    assert ranks["lonely"] == pytest.approx(0.0)
    assert sum(ranks.values()) == pytest.approx(1.0, abs=1e-6)
    assert graph_search.personalized_pagerank({}, {"a": 1.0}) == {}
    assert graph_search.personalized_pagerank({"a": []}, {"zzz": 1.0}) == {}


def test_seed_weights_are_weighted_and_normalised() -> None:
    weights = graph_search.seed_weights([("e1", 0.5)], [("e1", "e2", 0.5)], [(["e3"], 1.0)])
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["e1"] == pytest.approx(0.9 / 1.6)
    assert weights["e2"] == pytest.approx(0.4 / 1.6)
    assert weights["e3"] == pytest.approx(0.3 / 1.6)


def _hit(chunk_id: str, score: float) -> VectorHit:
    return VectorHit(chunk_id, "kb", "d", 0, chunk_id, None, None, score)


def test_rrf_fuse_labels_sources_and_orders() -> None:
    regular = [_hit("a", 0.9), _hit("b", 0.5)]
    graph = [_hit("b", 0.8), _hit("c", 0.4)]
    fused, sources = fusion.rrf_fuse(regular, graph)
    assert [h.chunk_id for h in fused] == ["b", "a", "c"]
    assert sources == {"a": ["chunk"], "b": ["chunk", "graph"], "c": ["graph"]}
    assert all(0 < h.score <= 1 for h in fused)
    assert fused[0].score == pytest.approx((1 / 62 + 1 / 61) / (2 / 61))


# ── build ────────────────────────────────────────────────────


@dataclass
class FakeLLM:
    replies: dict[str, str] = field(default_factory=dict)
    fail_first: int = 0
    calls: list[str] = field(default_factory=list)
    active: int = 0
    peak: int = 0

    async def ainvoke(self, prompt: str):
        self.calls.append(prompt)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail_first > 0:
                self.fail_first -= 1
                raise RuntimeError("llm down")
            for marker, reply in self.replies.items():
                if marker in prompt:
                    return SimpleNamespace(content=reply)
            return SimpleNamespace(content="garbage")
        finally:
            self.active -= 1


ALICE = json.dumps(
    {
        "entities": [{"name": "Alice", "type": "Person"}, {"name": "Acme", "type": "Org"}],
        "relations": [{"source": "Alice", "target": "Acme", "type": "works at"}],
    }
)
BOB = json.dumps(
    {
        "entities": [{"name": "Bob", "type": "Person"}, {"name": "ACME", "type": "Org"}],
        "relations": [{"source": "Bob", "target": "acme", "type": "works at"}],
    }
)


async def _kb(kb_id: str = "kb1", *, graph: bool = True, schema: str = "") -> dict:
    storage = get_kb_storage()
    await storage.insert_base(
        {
            "_id": kb_id,
            "owner_id": "u1",
            "name": "n",
            "status": "active",
            "graph_enabled": graph,
            "graph_schema": schema,
            "embedding": {
                "model_id": "card-1",
                "model_name": "fake-embed",
                "dim": 8,
                "collection": "kb_8_x",
            },
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
    )
    return await storage.get_base(kb_id)


async def _chunk(kb_id: str, doc_id: str, index: int, text: str, status=None) -> str:
    chunk_id = f"{doc_id}:{index}"
    await get_kb_storage().chunks.insert_one(
        {
            "_id": chunk_id,
            "kb_id": kb_id,
            "doc_id": doc_id,
            "index": index,
            "text": text,
            "start_line": 1,
            "end_line": 2,
            "graph_status": status,
        }
    )
    return chunk_id


async def _run(kb_id: str = "kb1", llm: FakeLLM | None = None, monkeypatch=None, cancel=None):
    storage = get_kb_storage()
    await storage.ensure_indexes()
    task = await storage.enqueue_task(kb_id, [], "u1", "graph")
    claimed = await storage.claim_task(task["_id"], "w", utc_now(), 60)
    cancel = cancel or asyncio.Event()
    await graph_build.run_graph_task(claimed, "w", cancel, asyncio.Event())
    return await storage.get_task(task["_id"])


@pytest.fixture
def graph_env(kb_env, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(graph_build, "_sleep", fake_sleep)
    monkeypatch.setattr(settings, "KB_GRAPH_CONCURRENCY", 2)
    monkeypatch.setattr(settings, "KB_GRAPH_MAX_CHUNKS_PER_KB", 100)
    holder = SimpleNamespace(
        llm=FakeLLM({"Alice works": ALICE, "Bob works": BOB}), sleeps=sleeps, fakes=kb_env
    )

    async def _llm():
        return holder.llm

    monkeypatch.setattr(graph_build, "get_extraction_llm", _llm)
    return holder


async def test_build_merges_entities_across_chunks_and_writes_vectors(graph_env) -> None:
    await _kb()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    await _chunk("kb1", "d2", 0, "Bob works at Acme", "pending")
    task = await _run()
    assert task["status"] == "succeeded"
    assert task["progress"]["total"] == 2 and task["progress"]["done"] == 2

    gs = GraphStorage(get_kb_storage())
    acme = await gs.entities.find_one({"normalized_name": "acme"})
    assert acme["mention_count"] == 2 and sorted(acme["chunk_ids"]) == ["d1:0", "d2:0"]
    assert sorted(acme["doc_ids"]) == ["d1", "d2"]
    assert await gs.count_entities("kb1") == 3
    assert await gs.count_triples("kb1") == 2
    assert await gs.count_status("kb1", "done") == 2
    chunk = await gs.get_chunk("d1:0")
    assert len(chunk["entity_ids"]) == 2 and len(chunk["triple_ids"]) == 1

    collection = next(iter(graph_env.fakes.vectors.graph))
    assert collection.startswith("kbg_8_")
    kinds = sorted(kind for kind, _ in graph_env.fakes.vectors.graph[collection])
    assert kinds == ["entity"] * 3 + ["triple"] * 2
    assert {"Alice", "Alice → works at → Acme"} <= {
        p.text for p in graph_env.fakes.vectors.graph[collection].values()
    }
    assert await gs.entities.count_documents({"vector_status": "indexed"}) == 3
    assert "works at" not in "".join(
        graph_env.fakes.embed_calls[0]
    )  # entities first, relations second


async def test_build_prompt_carries_schema(graph_env) -> None:
    await _kb(schema="只抽取人物与公司")
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    await _run()
    assert (
        "只抽取人物与公司" in graph_env.llm.calls[0]
        and "Alice works at Acme" in graph_env.llm.calls[0]
    )


async def test_chunk_retries_with_backoff_then_succeeds(graph_env) -> None:
    await _kb()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    graph_env.llm.fail_first = 2
    task = await _run()
    assert task["status"] == "succeeded"
    assert graph_env.sleeps == [2.0, 10.0]
    assert len(graph_env.llm.calls) == 3
    assert (await GraphStorage(get_kb_storage()).get_chunk("d1:0"))["graph_status"] == "done"


async def test_chunk_failure_is_recorded_and_retry_resumes(graph_env) -> None:
    await _kb()
    await _chunk("kb1", "d1", 0, "unrelated text", "pending")
    await _chunk("kb1", "d2", 0, "Bob works at Acme", "pending")
    task = await _run()
    # failures count, but the task itself still succeeds
    assert task["status"] == "succeeded" and task["progress"]["failed"] == 1
    assert graph_env.sleeps == [2.0, 10.0]
    gs = GraphStorage(get_kb_storage())
    failed = await gs.get_chunk("d1:0")
    assert failed["graph_status"] == "failed" and "ExtractionParseError" in failed["graph_error"]
    assert await gs.count_status("kb1", "done") == 1

    graph_env.llm.replies["unrelated"] = ALICE
    assert await gs.retry_failed("kb1") == 1
    task = await _run()
    assert task["progress"]["total"] == 1 and task["status"] == "succeeded"
    assert await gs.count_status("kb1", "done") == 2
    acme = await gs.entities.find_one({"normalized_name": "acme"})
    assert acme["mention_count"] == 2


async def test_build_fails_without_chat_model(graph_env, monkeypatch) -> None:
    await _kb()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")

    async def _no_model():
        raise graph_build.GraphConfigError(graph_build.NO_MODEL_MESSAGE)

    monkeypatch.setattr(graph_build, "get_extraction_llm", _no_model)
    task = await _run()
    assert task["status"] == "failed" and task["error"] == graph_build.NO_MODEL_MESSAGE
    assert "管理员" in task["error"]
    assert (await GraphStorage(get_kb_storage()).get_chunk("d1:0"))["graph_status"] == "pending"


async def test_extraction_llm_requires_a_chat_card(monkeypatch) -> None:
    from src.infra.agent import model_storage

    cards: dict[str, object] = {}

    class _Storage:
        async def get(self, model_id: str):
            return cards.get(model_id)

    monkeypatch.setattr(model_storage, "get_model_storage", lambda: _Storage())
    monkeypatch.setattr(settings, "DEFAULT_MODEL_ID", "")
    monkeypatch.setattr(settings, "KB_GRAPH_LLM_MODEL_ID", "")
    with pytest.raises(graph_build.GraphConfigError):
        await graph_build.get_extraction_llm()

    cards["emb"] = SimpleNamespace(kind="embedding")
    monkeypatch.setattr(settings, "KB_GRAPH_LLM_MODEL_ID", "emb")
    with pytest.raises(graph_build.GraphConfigError):
        await graph_build.get_extraction_llm()
    monkeypatch.setattr(settings, "KB_GRAPH_LLM_MODEL_ID", "missing")
    with pytest.raises(graph_build.GraphConfigError):
        await graph_build.get_extraction_llm()


async def test_concurrency_is_limited(graph_env, monkeypatch) -> None:
    monkeypatch.setattr(settings, "KB_GRAPH_CONCURRENCY", 2)
    await _kb()
    for i in range(6):
        await _chunk("kb1", f"d{i}", 0, "Alice works at Acme", "pending")
    await _run()
    assert graph_env.llm.peak == 2


async def test_cancel_stops_and_keeps_remaining_chunks_pending(graph_env) -> None:
    await _kb()
    for i in range(5):
        await _chunk("kb1", f"d{i}", 0, "Alice works at Acme", "pending")
    cancel = asyncio.Event()
    cancel.set()
    task = await _run(cancel=cancel)
    assert task["status"] == "cancelled"
    assert await GraphStorage(get_kb_storage()).count_status("kb1", "pending") == 5


async def test_disabled_kb_makes_the_task_a_noop(graph_env) -> None:
    await _kb(graph=False)
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    task = await _run()
    assert task["status"] == "succeeded" and graph_env.llm.calls == []


# ── incremental + cap ────────────────────────────────────────


async def test_queue_new_chunks_marks_and_enqueues_once(graph_env) -> None:
    await _kb()
    storage = get_kb_storage()
    await storage.ensure_indexes()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "done")
    await _chunk("kb1", "d2", 0, "Bob works at Acme")
    await _chunk("kb1", "d2", 1, "more")
    assert await graph_build.queue_new_chunks("kb1", "u1") == 2
    assert await graph_build.queue_new_chunks("kb1", "u1") == 2
    tasks = [t for t in storage.tasks.docs if t["type"] == "graph"]
    assert len(tasks) == 1 and tasks[0]["status"] == "queued"
    assert graph_env.fakes.published.count(tasks[0]["_id"]) == 2


async def test_queue_new_chunks_ignores_disabled_kb_and_idle_kb(graph_env) -> None:
    await _kb("off", graph=False)
    await _chunk("off", "d1", 0, "x")
    assert await graph_build.queue_new_chunks("off", "u1") == 0
    await _kb("idle")
    assert await graph_build.queue_new_chunks("idle", "u1") == 0
    assert not [t for t in get_kb_storage().tasks.docs if t["type"] == "graph"]


async def test_mark_pending_respects_the_chunk_cap(graph_env) -> None:
    await _kb()
    for i in range(5):
        await _chunk("kb1", f"d{i}", 0, "x")
    gs = GraphStorage(get_kb_storage())
    assert await gs.mark_pending("kb1", 3) == 3
    assert await gs.mark_pending("kb1", 3) == 0
    assert await gs.count_status("kb1", None) == 2


async def test_graph_tasks_do_not_block_ingest_throttling(graph_env) -> None:
    await _kb()
    storage = get_kb_storage()
    await storage.ensure_indexes()
    graph_task = await storage.enqueue_task("kb1", [], "u1", "graph")
    await storage.claim_task(graph_task["_id"], "w", utc_now(), 60)
    ingest = await storage.enqueue_task("kb1", ["d1"], "u1")
    assert not await storage.has_running_for_user("u1", utc_now(), ingest["_id"])
    assert await storage.has_running_for_user("u1", utc_now(), "other", "graph")


# ── reclaim ──────────────────────────────────────────────────


async def test_reclaim_documents_removes_contribution_and_empty_rows(graph_env) -> None:
    kb = await _kb()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    await _chunk("kb1", "d2", 0, "Bob works at Acme", "pending")
    await _run()
    gs = GraphStorage(get_kb_storage())
    store = graph_env.fakes.vectors
    collection = next(iter(store.graph))
    assert len(store.graph[collection]) == 5

    await graph_build.reclaim_documents(kb, ["d1"])
    acme = await gs.entities.find_one({"normalized_name": "acme"})
    assert (
        acme["mention_count"] == 1 and acme["chunk_ids"] == ["d2:0"] and acme["doc_ids"] == ["d2"]
    )
    assert await gs.entities.find_one({"normalized_name": "alice"}) is None
    assert await gs.count_triples("kb1") == 1
    assert sorted(k for k, _ in store.graph[collection]) == ["entity", "entity", "triple"]

    await graph_build.reclaim_documents(kb, ["d2"])
    assert await gs.count_entities("kb1") == 0 and await gs.count_triples("kb1") == 0
    assert store.graph[collection] == {}


async def test_purge_graph_resets_chunks_and_vectors(graph_env) -> None:
    kb = await _kb()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    await _run()
    await graph_build.purge_graph(kb)
    gs = GraphStorage(get_kb_storage())
    assert await gs.count_entities("kb1") == 0
    assert (await gs.get_chunk("d1:0"))["graph_status"] is None
    assert next(iter(graph_env.fakes.vectors.graph.values())) == {}


# ── retrieval ────────────────────────────────────────────────


async def test_graph_search_degrades_to_regular_results(graph_env, monkeypatch) -> None:
    kb = await _kb()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    await _run()
    regular = [_hit("d1:0", 0.7)]
    regular[0] = VectorHit("d1:0", "kb1", "d1", 0, "t", None, None, 0.7)

    fused, sources = await graph_search.augment([kb], regular, "Alice", None)
    assert sources["d1:0"] == ["chunk", "graph"] and fused[0].chunk_id == "d1:0"

    async def boom(*_a, **_k):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(graph_search, "graph_chunk_hits", boom)
    fused, sources = await graph_search.augment([kb], regular, "Alice", None)
    assert fused == regular and sources == {"d1:0": ["chunk"]}


async def test_graph_search_adds_chunks_regular_search_missed(graph_env) -> None:
    kb = await _kb()
    await _chunk("kb1", "d1", 0, "Alice works at Acme", "pending")
    await _chunk("kb1", "d2", 0, "Bob works at Acme", "pending")
    await _run()
    regular = [VectorHit("d1:0", "kb1", "d1", 0, "Alice works at Acme", 1, 2, 0.9)]
    fused, sources = await graph_search.augment([kb], regular, "Alice", None)
    # Bob's chunk is two hops from Alice via Acme and is surfaced by the graph leg only.
    assert sources["d2:0"] == ["graph"]
    assert {h.chunk_id for h in fused} == {"d1:0", "d2:0"}


async def test_graph_search_skips_kbs_without_a_built_graph(graph_env) -> None:
    kb = await _kb()
    await _chunk("kb1", "d1", 0, "x", "pending")  # nothing done yet
    regular = [VectorHit("d1:0", "kb1", "d1", 0, "x", None, None, 0.7)]
    fused, sources = await graph_search.augment([kb], regular, "x", None)
    assert fused == regular and sources == {"d1:0": ["chunk"]}


async def test_only_one_graph_build_runs_per_kb(graph_env) -> None:
    await _kb("kbx")
    storage = get_kb_storage()
    await storage.ensure_indexes()
    first = await storage.enqueue_task("kbx", [], "manager-a", "graph")
    await storage.claim_task(first["_id"], "w", utc_now(), 60)
    second = await storage.enqueue_task("kbx", [], "manager-b", "graph")
    assert await storage.has_running_graph_for_kb("kbx", utc_now(), second["_id"])
    assert not await storage.has_running_graph_for_kb("kbx", utc_now(), first["_id"])
    assert not await storage.has_running_graph_for_kb("kby", utc_now(), second["_id"])


async def test_global_graph_switch_stops_builds_and_search(graph_env, monkeypatch) -> None:
    kb = await _kb("kbx")
    await _chunk("kbx", "docx", 0, "Alice works at Acme", "pending")
    monkeypatch.setattr(settings, "KB_ALLOW_GRAPH", False, raising=False)
    task = await _run("kbx")
    assert task["status"] == "succeeded" and graph_env.llm.calls == []
    regular = [VectorHit("docx:0", "kbx", "docx", 0, "Alice works at Acme", 1, 2, 0.9)]
    fused, sources = await graph_search.augment([kb], regular, "Alice", None)
    assert fused == regular and sources == {"docx:0": ["chunk"]}
