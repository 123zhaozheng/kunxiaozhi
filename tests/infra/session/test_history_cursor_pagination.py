"""Cursor codec and page-reader tests for the session/share history contract."""

from typing import Any, Dict, List, Optional

import pytest

from src.infra.session.history_cursor import (
    InvalidHistoryCursor,
    decode_history_cursor,
    encode_history_cursor,
    event_ordering_key,
    filter_fingerprint,
)
from src.infra.session.trace_storage import TraceStorage


class _FakeAggregationCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for doc in self._docs:
            yield doc

    async def to_list(self, length=None):
        return list(self._docs)


def _extract_cursor_key(pipeline: List[Dict[str, Any]]) -> Optional[List[Any]]:
    """Recover the cursor ordering key from the `$match` `$or` predicate."""
    for stage in pipeline:
        match = stage.get("$match")
        if match and isinstance(match, dict) and "$or" in match:
            last = match["$or"][-1]
            return [
                last["events.legacy_bucket"],
                last["events.seq_sort"],
                last["events.timestamp_sort"],
                last["trace_id"],
                last["events.event_id_sort"],
                last["events.event_index_sort"]["$gt"],
            ]
    return None


def _simulate_aggregate(events: List[Dict[str, Any]], pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Simulate the read pipeline: ordering key, cursor filter, sort, limit."""
    rows = []
    for idx, event in enumerate(events):
        key = event_ordering_key(event, ordinal=idx)
        rows.append({**event, "_event_index": idx, "_sort_key": key})

    cursor_key = _extract_cursor_key(pipeline)
    limit: Optional[int] = None
    for stage in pipeline:
        if "$limit" in stage:
            limit = stage["$limit"]

    if cursor_key is not None:
        rows = [row for row in rows if row["_sort_key"] > cursor_key]
    rows.sort(key=lambda row: row["_sort_key"])
    if limit is not None:
        rows = rows[:limit]

    return [
        {
            "trace_id": row.get("trace_id", "trace-1"),
            "run_id": row.get("run_id", "run-1"),
            "event_type": row["event_type"],
            "data": row.get("data", {}),
            "timestamp": row.get("timestamp"),
            "seq": row.get("seq"),
            "event_id": row.get("event_id"),
            "_event_index": row["_event_index"],
        }
        for row in rows
    ]


class _FakeCursorCollection:
    def __init__(self, events: List[Dict[str, Any]]):
        self._events = events
        self.aggregate_calls: List[List[Dict[str, Any]]] = []

    def aggregate(self, pipeline):
        self.aggregate_calls.append(pipeline)
        return _FakeAggregationCursor(_simulate_aggregate(self._events, pipeline))


# ---------------------------------------------------------------------------
# Cursor codec
# ---------------------------------------------------------------------------


def test_cursor_round_trip() -> None:
    fp = filter_fingerprint(scope="session-1", event_types=["user:message"], run_id="run-1")
    key = [1, 5, "2026-04-25T00:00:00Z", "trace-1", "event-1", 2]
    cursor = encode_history_cursor(scope="session-1", fingerprint=fp, key=key)
    assert decode_history_cursor(cursor, scope="session-1", fingerprint=fp) == key


def test_cursor_rejects_wrong_scope() -> None:
    fp = filter_fingerprint(scope="session-1")
    cursor = encode_history_cursor(scope="session-1", fingerprint=fp, key=[0, 0, "", "", "", 0])
    with pytest.raises(InvalidHistoryCursor):
        decode_history_cursor(cursor, scope="session-2", fingerprint=fp)


def test_cursor_rejects_wrong_filter() -> None:
    fp = filter_fingerprint(scope="session-1", run_id="run-1")
    other = filter_fingerprint(scope="session-1", run_id="run-2")
    cursor = encode_history_cursor(scope="session-1", fingerprint=fp, key=[0, 0, "", "", "", 0])
    with pytest.raises(InvalidHistoryCursor):
        decode_history_cursor(cursor, scope="session-1", fingerprint=other)


def test_cursor_rejects_malformed_key() -> None:
    fp = filter_fingerprint(scope="session-1")
    for bad_key in (
        [1, 5, "ts", "trace-1", "event-1"],  # wrong length
        ["x", 5, "ts", "trace-1", "event-1", 0],  # legacy_bucket not int
        [1, "x", "ts", "trace-1", "event-1", 0],  # seq not int
        [1, 5, 123, "trace-1", "event-1", 0],  # timestamp not str
        [1, 5, "ts", 123, "event-1", 0],  # trace_id not str
        [1, 5, "ts", "trace-1", "event-1", "x"],  # ordinal not int
    ):
        cursor = encode_history_cursor(scope="session-1", fingerprint=fp, key=bad_key)
        with pytest.raises(InvalidHistoryCursor):
            decode_history_cursor(cursor, scope="session-1", fingerprint=fp)


def test_event_ordering_key_legacy_and_seq_buckets() -> None:
    legacy = event_ordering_key({"event_type": "done", "timestamp": "t1"})
    assert legacy[0] == 0
    assert legacy[1] == 0
    assert legacy[2] == "t1"

    seq = event_ordering_key({"seq": 3, "timestamp": "t1", "trace_id": "trace-1", "event_id": "e1"})
    assert seq[0] == 1
    assert seq[1] == 3
    assert seq[4] == "e1"


def test_event_ordering_key_uses_id_or_event_id() -> None:
    a = event_ordering_key({"event_id": "evt-1", "timestamp": "t1"})
    b = event_ordering_key({"id": "evt-2", "timestamp": "t1"})
    assert a[4] == "evt-1"
    assert b[4] == "evt-2"


# ---------------------------------------------------------------------------
# Page reader
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_events_page_probes_limit_plus_one() -> None:
    storage = TraceStorage()
    collection = _FakeCursorCollection(
        [
            {"event_type": "user:message", "data": {"content": "one"}, "seq": 1},
            {"event_type": "message:chunk", "data": {"content": "two"}, "seq": 2},
            {"event_type": "done", "data": {}, "seq": 3},
        ]
    )
    storage._collection = collection

    page = await storage.get_session_events_page("session-1", limit=2)

    assert page["has_more"] is True
    assert page["next_cursor"] is not None
    assert [event["seq"] for event in page["events"]] == [1, 2]
    assert page["events_limit"] == 2
    assert page["history_complete"] is False
    pipeline = collection.aggregate_calls[-1]
    assert {"$limit": 3} in pipeline


@pytest.mark.asyncio
async def test_get_session_events_page_round_trip_no_gaps_or_duplicates() -> None:
    events = [
        {"event_type": "user:message", "data": {"content": "a"}, "seq": 1, "timestamp": "2026-04-25T00:00:00Z"},
        {"event_type": "message:chunk", "data": {"content": "b"}, "seq": 2, "timestamp": "2026-04-25T00:00:01Z"},
        {"event_type": "done", "data": {}, "seq": 3, "timestamp": "2026-04-25T00:00:02Z"},
        {"event_type": "user:message", "data": {"content": "c"}, "seq": 4, "timestamp": "2026-04-25T00:00:03Z"},
    ]
    storage = TraceStorage()
    collection = _FakeCursorCollection(events)
    storage._collection = collection

    page1 = await storage.get_session_events_page("session-1", limit=2)
    assert [event["seq"] for event in page1["events"]] == [1, 2]
    assert page1["has_more"] is True

    page2 = await storage.get_session_events_page(
        "session-1",
        limit=2,
        after=page1["next_cursor"],
    )
    assert [event["seq"] for event in page2["events"]] == [3, 4]
    assert page2["has_more"] is False
    assert page2["next_cursor"] is None

    seen = [event["seq"] for event in page1["events"] + page2["events"]]
    assert seen == [1, 2, 3, 4]
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_get_session_events_page_uses_ordinal_tie_breaker() -> None:
    # Two events share the same seq/timestamp; the immutable ordinal must
    # prevent one of them being dropped or repeated across pages.
    events = [
        {"event_type": "user:message", "data": {"content": "a"}, "seq": 1, "timestamp": "t1"},
        {"event_type": "message:chunk", "data": {"content": "b"}, "seq": 1, "timestamp": "t1"},
        {"event_type": "done", "data": {}, "seq": 2, "timestamp": "t1"},
    ]
    storage = TraceStorage()
    collection = _FakeCursorCollection(events)
    storage._collection = collection

    page1 = await storage.get_session_events_page("session-1", limit=2)
    page2 = await storage.get_session_events_page("session-1", limit=2, after=page1["next_cursor"])

    seqs1 = [event["seq"] for event in page1["events"]]
    seqs2 = [event["seq"] for event in page2["events"]]
    assert len(page1["events"]) == 2
    assert len(page2["events"]) == 1
    assert seqs1 + seqs2 == [1, 1, 2]


@pytest.mark.asyncio
async def test_get_session_events_page_applies_cursor_predicate() -> None:
    storage = TraceStorage()
    collection = _FakeCursorCollection(
        [
            {"event_type": "done", "data": {}, "seq": 3, "timestamp": "t1"},
        ]
    )
    storage._collection = collection

    fp = filter_fingerprint(scope="session-1")
    cursor = encode_history_cursor(
        scope="session-1",
        fingerprint=fp,
        key=[1, 2, "t1", "trace-1", "event-1", 0],
    )

    await storage.get_session_events_page("session-1", limit=5, after=cursor)

    pipeline = collection.aggregate_calls[-1]
    # The $or predicate uses the decoded key.
    or_predicate = next(
        stage["$match"]["$or"]
        for stage in pipeline
        if "$match" in stage and "$or" in stage["$match"]
    )
    assert or_predicate[-1]["events.legacy_bucket"] == 1
    assert or_predicate[-1]["events.seq_sort"] == 2
    assert or_predicate[-1]["events.timestamp_sort"] == "t1"
    assert or_predicate[-1]["trace_id"] == "trace-1"
    assert or_predicate[-1]["events.event_id_sort"] == "event-1"
    assert or_predicate[-1]["events.event_index_sort"] == {"$gt": 0}


@pytest.mark.asyncio
async def test_get_session_events_page_propagates_invalid_cursor() -> None:
    storage = TraceStorage()
    collection = _FakeCursorCollection([{"event_type": "done", "data": {}, "seq": 1}])
    storage._collection = collection

    with pytest.raises(InvalidHistoryCursor):
        await storage.get_session_events_page("session-1", limit=5, after="not-a-cursor")


@pytest.mark.asyncio
async def test_get_session_events_page_clamps_to_legacy_cap() -> None:
    storage = TraceStorage()
    collection = _FakeCursorCollection([{"event_type": "done", "data": {}, "seq": 1}])
    storage._collection = collection

    await storage.get_session_events_page("session-1", limit=100_000)

    pipeline = collection.aggregate_calls[-1]
    # Probe adds one slot above the 10,000 clamp so a request exactly at the
    # cap can still report has_more.
    assert {"$limit": 10001} in pipeline
