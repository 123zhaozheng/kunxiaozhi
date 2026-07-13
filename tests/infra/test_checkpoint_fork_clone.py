from types import SimpleNamespace
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

import src.infra.storage.checkpoint as checkpoint_mod
from src.infra.storage.checkpoint import build_messages_from_trace_events


class _MongoLikeSaver(InMemorySaver):
    """InMemorySaver variant that restores BaseCheckpointSaver.get_next_version.

    MongoDBSaver does NOT override get_next_version (unlike InMemorySaver which
    uses str versions and masks the str-version bug). This subclass restores the
    base int-version behavior so tests can reproduce the MongoDB crash path.
    """

    def get_next_version(self, current, channel):  # type: ignore[override]
        return BaseCheckpointSaver.get_next_version(self, current, channel)


class _State(TypedDict):
    messages: Annotated[list, add_messages]


def _reply_node(state: _State):
    last = state["messages"][-1]
    return {"messages": [AIMessage(content=f"reply to {last.content}")]}


@pytest.mark.asyncio
async def test_clone_checkpoints_for_fork_copies_assistant_turn(monkeypatch: pytest.MonkeyPatch):
    builder = StateGraph(_State)
    builder.add_node("reply", _reply_node)
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)

    source_saver = InMemorySaver()
    target_saver = InMemorySaver()
    graph = builder.compile(checkpointer=source_saver)
    config = {"configurable": {"thread_id": "source-thread"}}

    graph.invoke({"messages": [HumanMessage(content="one")]}, config)
    graph.invoke({"messages": [HumanMessage(content="two")]}, config)

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        if thread_id == "source-thread":
            return source_saver
        if thread_id == "target-thread":
            return target_saver
        raise AssertionError(f"unexpected thread_id {thread_id}")

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)

    copied = await checkpoint_mod.clone_checkpoints_for_fork(
        "source-thread",
        "target-thread",
        turn_index=1,
        target_type="assistant",
    )

    assert copied > 0

    checkpoints = [
        item async for item in target_saver.alist({"configurable": {"thread_id": "target-thread"}})
    ]
    latest_messages = checkpoints[0].checkpoint["channel_values"]["messages"]
    assert [type(message).__name__ for message in latest_messages] == ["HumanMessage", "AIMessage"]
    assert latest_messages[0].content == "one"
    assert latest_messages[1].content == "reply to one"


@pytest.mark.asyncio
async def test_clone_checkpoints_for_fork_only_lists_default_namespace(
    monkeypatch: pytest.MonkeyPatch,
):
    source_saver = InMemorySaver()
    target_saver = InMemorySaver()

    first_config = {"configurable": {"thread_id": "source-thread", "checkpoint_ns": ""}}
    first_checkpoint = {
        "v": 1,
        "id": "0001",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {"messages": [HumanMessage(content="one")]},
        "channel_versions": {"messages": "0001"},
        "versions_seen": {},
    }
    second_config = await source_saver.aput(
        first_config,
        first_checkpoint,
        {},
        first_checkpoint["channel_versions"],
    )
    second_checkpoint = {
        **first_checkpoint,
        "id": "0002",
        "channel_values": {"messages": [HumanMessage(content="one"), AIMessage(content="reply")]},
        "channel_versions": {"messages": "0002"},
    }
    await source_saver.aput(
        second_config,
        second_checkpoint,
        {},
        second_checkpoint["channel_versions"],
    )

    list_calls: list[tuple[dict, dict]] = []
    original_alist = source_saver.alist

    async def _recording_alist(config, *args, **kwargs):
        list_calls.append((config, kwargs))
        async for item in original_alist(config, *args, **kwargs):
            yield item

    source_saver.alist = _recording_alist  # type: ignore[method-assign]

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        if thread_id == "source-thread":
            return source_saver
        if thread_id == "target-thread":
            return target_saver
        raise AssertionError(f"unexpected thread_id {thread_id}")

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)

    await checkpoint_mod.clone_checkpoints_for_fork(
        "source-thread",
        "target-thread",
        turn_index=1,
        target_type="assistant",
    )

    assert [config for config, _ in list_calls] == [
        {"configurable": {"thread_id": "source-thread", "checkpoint_ns": ""}}
    ]
    assert list_calls[0][1]["limit"] == 25


@pytest.mark.asyncio
async def test_clone_checkpoints_for_fork_offloads_checkpoint_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inside_blocking_io = False
    copied_objects: list[object] = []

    boundary_checkpoint = {
        "channel_values": {"messages": [HumanMessage(content="one"), AIMessage(content="reply")]},
        "channel_versions": {"messages": "0002"},
    }
    boundary_metadata = {"source": "loop"}

    class _TargetSaver:
        async def aput(self, *args):
            return None

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        return _TargetSaver()

    async def _fake_find_boundary(*args, **kwargs):
        return SimpleNamespace(
            config={"configurable": {"checkpoint_ns": ""}},
            checkpoint=boundary_checkpoint,
            metadata=boundary_metadata,
        )

    async def _fake_run_blocking_io(func, /, *args, **kwargs):
        nonlocal inside_blocking_io
        assert inside_blocking_io is False
        inside_blocking_io = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_blocking_io = False

    def _fake_deepcopy(value):
        assert inside_blocking_io, "checkpoint deepcopy must be offloaded"
        copied_objects.append(value)
        return value

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)
    monkeypatch.setattr(checkpoint_mod, "_find_fork_boundary_checkpoint", _fake_find_boundary)
    monkeypatch.setattr(checkpoint_mod, "run_blocking_io", _fake_run_blocking_io, raising=False)
    monkeypatch.setattr(checkpoint_mod.copy, "deepcopy", _fake_deepcopy)

    copied = await checkpoint_mod.clone_checkpoints_for_fork(
        "source-thread",
        "target-thread",
        turn_index=1,
        target_type="assistant",
    )

    assert copied == 1
    assert copied_objects == [
        boundary_checkpoint,
        boundary_metadata,
        boundary_checkpoint["channel_versions"],
    ]


def test_build_messages_from_trace_events_preserves_user_and_assistant_text() -> None:
    messages = build_messages_from_trace_events(
        [
            {
                "events": [
                    {"event_type": "user:message", "data": {"content": "hello"}},
                    {"event_type": "thinking", "data": {"content": "private reasoning"}},
                    {"event_type": "message:chunk", "data": {"content": "hi"}},
                    {"event_type": "message:chunk", "data": {"content": " there"}},
                    {"event_type": "done", "data": {"status": "completed"}},
                ]
            }
        ]
    )

    assert [type(message).__name__ for message in messages] == ["HumanMessage", "AIMessage"]
    assert messages[0].content == "hello"
    assert messages[1].content == "hi there"


@pytest.mark.asyncio
async def test_seed_checkpoint_from_messages_offloads_message_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inside_blocking_io = False
    messages = [HumanMessage(content="hello")]

    class _TargetSaver:
        async def aput(self, *args):
            return None

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        return _TargetSaver()

    async def _fake_run_blocking_io(func, /, *args, **kwargs):
        nonlocal inside_blocking_io
        assert inside_blocking_io is False
        inside_blocking_io = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_blocking_io = False

    def _fake_deepcopy(value):
        assert inside_blocking_io, "message deepcopy must be offloaded"
        assert value is messages
        return value

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)
    monkeypatch.setattr(checkpoint_mod, "run_blocking_io", _fake_run_blocking_io, raising=False)
    monkeypatch.setattr(checkpoint_mod.copy, "deepcopy", _fake_deepcopy)

    seeded = await checkpoint_mod.seed_checkpoint_from_messages("target-thread", messages)

    assert seeded == 1


@pytest.mark.asyncio
async def test_delete_checkpoints_for_thread_uses_checkpointer_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Saver:
        def __init__(self) -> None:
            self.deleted_threads: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted_threads.append(thread_id)

    saver = _Saver()

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        assert thread_id == "session-1"
        return saver

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)

    await checkpoint_mod.delete_checkpoints_for_thread("session-1")

    assert saver.deleted_threads == ["session-1"]


@pytest.mark.asyncio
async def test_delete_checkpoints_for_thread_offloads_sync_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inside_blocking_io = False

    class _Saver:
        def __init__(self) -> None:
            self.deleted_threads: list[str] = []

        def delete_thread(self, thread_id: str) -> None:
            assert inside_blocking_io, "sync delete_thread must be offloaded"
            self.deleted_threads.append(thread_id)

    saver = _Saver()

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        assert thread_id == "session-1"
        return saver

    async def _fake_run_blocking_io(func, /, *args, **kwargs):
        nonlocal inside_blocking_io
        assert inside_blocking_io is False
        inside_blocking_io = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_blocking_io = False

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)
    monkeypatch.setattr(checkpoint_mod, "run_blocking_io", _fake_run_blocking_io, raising=False)

    await checkpoint_mod.delete_checkpoints_for_thread("session-1")

    assert saver.deleted_threads == ["session-1"]


@pytest.mark.asyncio
async def test_delete_checkpoints_for_thread_skips_when_checkpoint_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_get_async_checkpointer(thread_id: str | None = None):
        raise AssertionError(f"should not initialize checkpointer for {thread_id}")

    monkeypatch.setattr(checkpoint_mod.settings, "CHECKPOINT_BACKEND", "none")
    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fail_get_async_checkpointer)

    await checkpoint_mod.delete_checkpoints_for_thread("session-1")


def test_get_next_version_contract_str_raises_int_increments() -> None:
    """Lock in the LangGraph version contract that seed_checkpoint_from_messages relies on.

    BaseCheckpointSaver.get_next_version raises NotImplementedError for str versions
    (MongoDBSaver inherits this; only InMemorySaver overrides it to str). seed must
    therefore write int versions.
    """
    saver = _MongoLikeSaver()

    # str version -> NotImplementedError (the crash root cause)
    with pytest.raises(NotImplementedError):
        saver.get_next_version("1", None)

    # None -> 1 (fresh channel)
    assert saver.get_next_version(None, None) == 1
    # int -> increments (the post-fix path)
    assert saver.get_next_version(1, None) == 2


@pytest.mark.asyncio
async def test_seed_checkpoint_writes_int_version_incrementable_by_mongo_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed must write an int version that MongoDBSaver-style get_next_version can increment.

    Regression for the fork-session crash: a str version caused
    NotImplementedError on the first message in a forked session (Mongo backend).
    Uses _MongoLikeSaver (base get_next_version) to avoid InMemorySaver masking the bug.
    """
    target_saver = _MongoLikeSaver()

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        return target_saver

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)

    seeded = await checkpoint_mod.seed_checkpoint_from_messages(
        "target-thread",
        [HumanMessage(content="seeded"), AIMessage(content="seed-reply")],
    )

    assert seeded == 1

    tuples = [
        item
        async for item in target_saver.alist(
            {"configurable": {"thread_id": "target-thread"}}
        )
    ]
    assert tuples, "seeded checkpoint must be persisted"
    current_version = tuples[0].checkpoint["channel_versions"]["messages"]
    # Must be int, not str — str triggers NotImplementedError in base get_next_version.
    assert isinstance(current_version, int)
    assert current_version == 1

    # Simulate apply_writes computing the next version: must not raise.
    next_version = target_saver.get_next_version(current_version, None)
    assert next_version == 2


@pytest.mark.asyncio
async def test_seed_int_version_does_not_break_memory_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory backend regression: InMemorySaver.get_next_version accepts int|str|None.

    Confirms the int-version fix does not break the MemorySaver fallback path
    (its override handles int via the isinstance(current, int) branch).
    """
    target_saver = InMemorySaver()

    async def _fake_get_async_checkpointer(thread_id: str | None = None):
        return target_saver

    monkeypatch.setattr(checkpoint_mod, "get_async_checkpointer", _fake_get_async_checkpointer)

    await checkpoint_mod.seed_checkpoint_from_messages(
        "target-thread",
        [HumanMessage(content="seeded"), AIMessage(content="seed-reply")],
    )

    tuples = [
        item
        async for item in target_saver.alist(
            {"configurable": {"thread_id": "target-thread"}}
        )
    ]
    current_version = tuples[0].checkpoint["channel_versions"]["messages"]
    assert current_version == 1  # int seed value
    # InMemorySaver must accept the int and produce a usable (str) next version.
    next_version = target_saver.get_next_version(current_version, None)
    assert isinstance(next_version, str)
    assert next_version  # non-empty
