"""SopRunStore 持久化测试（mock motor collection，不连真实 Mongo）。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.agents.team_agent.sop.schemas import SOPPlan, SOPStep, StepStatus
from src.agents.team_agent.sop.store import SopRunStore


class _FakeCollection:
    """内存版 motor collection：支持 create_index/update_one/find_one。"""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.index_calls: list[tuple[list, dict]] = []
        self.update_calls: list[tuple[dict, dict, bool]] = []

    async def create_index(self, keys, **kwargs) -> None:
        self.index_calls.append((keys, kwargs))

    async def update_one(self, query, update, upsert=False, **kwargs) -> SimpleNamespace:
        self.update_calls.append((query, update, upsert))
        key = (query["session_id"], query["team_id"])
        if key not in self.docs:
            if not upsert:
                return SimpleNamespace(modified_count=0, matched_count=0)
            self.docs[key] = {}
        doc = self.docs[key]
        if "plan_id" in query and doc.get("plan_id") != query["plan_id"]:
            return SimpleNamespace(modified_count=0, matched_count=0)
        step_id = query.get("steps.step_id")
        step = next((item for item in doc.get("steps", []) if item.get("step_id") == step_id), None)
        if "steps.step_id" in query and step is None:
            return SimpleNamespace(modified_count=0, matched_count=0)
        for field, value in update.get("$set", {}).items():
            if field.startswith("steps.$."):
                step[field.removeprefix("steps.$.")] = value
            else:
                doc[field] = value
        return SimpleNamespace(modified_count=1, matched_count=1)

    async def find_one(self, query) -> dict | None:
        key = (query["session_id"], query["team_id"])
        doc = self.docs.get(key)
        if doc is None:
            return None
        return {**doc, "_id": "fake-sop-run-id"}


def _make_plan(**overrides) -> SOPPlan:
    steps = [
        SOPStep(
            step_id="s1",
            title="step1",
            assignee="team-m1-role1",
            expected_output="out1",
        ),
        SOPStep(
            step_id="s2",
            title="step2",
            dependencies=["s1"],
            assignee="team-m2-role2",
            expected_output="out2",
        ),
    ]
    return SOPPlan(
        plan_id="plan-1",
        session_id="session-1",
        team_id="team-1",
        goal="goal",
        steps=steps,
        **overrides,
    )


def _make_store(fake: _FakeCollection) -> SopRunStore:
    store = SopRunStore()
    store._collection = fake
    return store


@pytest.mark.asyncio
async def test_upsert_and_get_roundtrip() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)

    plan = _make_plan()
    await store.upsert_plan(plan)

    got = await store.get_plan("session-1", "team-1")
    assert got is not None
    assert got.plan_id == "plan-1"
    assert got.session_id == "session-1"
    assert got.team_id == "team-1"
    assert got.goal == "goal"
    assert got.summary == ""
    assert got.status == "draft"
    assert len(got.steps) == 2
    assert got.steps[0].step_id == "s1"
    assert got.steps[1].dependencies == ["s1"]
    # 时间字段经 JSON 序列化往返后仍可解析回 datetime
    assert isinstance(got.created_at, datetime)
    assert isinstance(got.updated_at, datetime)
    assert got.created_at == plan.created_at
    assert got.updated_at == plan.updated_at


@pytest.mark.asyncio
async def test_upsert_writes_full_doc_via_set() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)

    plan = _make_plan()
    await store.upsert_plan(plan)

    query, update, upsert = fake.update_calls[-1]
    assert query == {"session_id": "session-1", "team_id": "team-1"}
    assert upsert is True
    set_doc = update["$set"]
    assert set_doc["plan_id"] == "plan-1"
    assert set_doc["goal"] == "goal"
    assert set_doc["steps"][1]["dependencies"] == ["s1"]
    assert set_doc["steps"][0]["status"] == "pending"
    # 时间字段以字符串形式落库（JSON 序列化）
    assert isinstance(set_doc["created_at"], str)
    assert isinstance(set_doc["updated_at"], str)


@pytest.mark.asyncio
async def test_get_plan_missing_returns_none() -> None:
    store = _make_store(_FakeCollection())
    assert await store.get_plan("session-x", "team-x") is None


@pytest.mark.asyncio
async def test_set_status_updates_and_returns_snapshot() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)
    await store.upsert_plan(_make_plan())

    snapshot = await store.set_status("session-1", "team-1", "running")
    assert snapshot is not None
    assert snapshot.status == "running"
    # 步骤完整保留（全量快照语义）
    assert [step.step_id for step in snapshot.steps] == ["s1", "s2"]

    got = await store.get_plan("session-1", "team-1")
    assert got is not None
    assert got.status == "running"


@pytest.mark.asyncio
async def test_set_status_missing_plan_returns_none() -> None:
    store = _make_store(_FakeCollection())
    assert await store.set_status("session-x", "team-x", "running") is None


@pytest.mark.asyncio
async def test_set_step_status_updates_and_returns_snapshot() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)
    await store.upsert_plan(_make_plan())

    snapshot = await store.set_step_status(
        "session-1", "team-1", "s1", StepStatus.succeeded, output="done"
    )
    assert snapshot is not None
    s1 = next(step for step in snapshot.steps if step.step_id == "s1")
    assert s1.status == StepStatus.succeeded
    assert s1.output == "done"
    # 其他步骤不受影响
    s2 = next(step for step in snapshot.steps if step.step_id == "s2")
    assert s2.status == StepStatus.pending

    # 再次读取验证已持久化
    got = await store.get_plan("session-1", "team-1")
    assert got is not None
    s1 = next(step for step in got.steps if step.step_id == "s1")
    assert s1.status == StepStatus.succeeded
    assert s1.output == "done"


@pytest.mark.asyncio
async def test_set_step_status_accepts_raw_status_string() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)
    await store.upsert_plan(_make_plan())

    snapshot = await store.set_step_status("session-1", "team-1", "s1", "failed", error="boom")
    assert snapshot is not None
    s1 = next(step for step in snapshot.steps if step.step_id == "s1")
    assert s1.status == StepStatus.failed
    assert s1.error == "boom"


@pytest.mark.asyncio
async def test_set_step_status_missing_plan_or_step_returns_none() -> None:
    store = _make_store(_FakeCollection())
    assert (
        await store.set_step_status("session-x", "team-x", "s1", StepStatus.succeeded) is None
    )

    fake = _FakeCollection()
    store = _make_store(fake)
    await store.upsert_plan(_make_plan())
    assert (
        await store.set_step_status("session-1", "team-1", "s99", StepStatus.succeeded) is None
    )


@pytest.mark.asyncio
async def test_set_feedback_updates_and_returns_snapshot() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)
    await store.upsert_plan(_make_plan())

    snapshot = await store.set_feedback("session-1", "team-1", "粒度太粗，请重新规划")
    assert snapshot is not None
    assert snapshot.user_feedback == "粒度太粗，请重新规划"

    got = await store.get_plan("session-1", "team-1")
    assert got is not None
    assert got.user_feedback == "粒度太粗，请重新规划"


@pytest.mark.asyncio
async def test_set_feedback_missing_plan_returns_none() -> None:
    store = _make_store(_FakeCollection())
    assert await store.set_feedback("session-x", "team-x", "feedback") is None


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_compound_index() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)
    await store.ensure_indexes()

    assert fake.index_calls == [
        ([("session_id", 1), ("team_id", 1)], {"unique": True, "background": True})
    ]


@pytest.mark.asyncio
async def test_first_upsert_creates_index_once() -> None:
    # 重置类级一次性标记，验证首次写入前惰性建索引、后续写入不再重复建
    SopRunStore._indexes_initialized = False
    try:
        fake = _FakeCollection()
        store = _make_store(fake)

        await store.upsert_plan(_make_plan())
        assert len(fake.index_calls) == 1
        assert fake.index_calls[0][0] == [("session_id", 1), ("team_id", 1)]

        await store.upsert_plan(_make_plan())
        # 二次写入不重复建索引
        assert len(fake.index_calls) == 1
    finally:
        SopRunStore._indexes_initialized = False


@pytest.mark.asyncio
async def test_upsert_preserves_existing_created_at() -> None:
    fake = _FakeCollection()
    store = _make_store(fake)

    plan = _make_plan()
    first = await store.upsert_plan(plan)
    original_created_at = first.created_at

    # 读取快照后再次全量替换，created_at 应保持首次写入时间
    second = await store.upsert_plan(_make_plan())
    assert second.created_at == original_created_at
    got = await store.get_plan("session-1", "team-1")
    assert got is not None
    assert got.created_at == original_created_at
