# Research: Refreshed Conversation Event Order

- Query: Why does conversation `c1bbfd3a-54e6-4968-b469-2dbc97f98632` show several completed reasoning entries at the beginning after refresh, and where should the fix/tests live?
- Scope: mixed (repository, Git history, local MongoDB, local trellis mem)
- Date: 2026-08-11

## Findings

### Confirmed persisted-data reproduction

The local MongoDB (`mongodb://localhost:27017`, database `agent_state`) contains the reported session. The relevant completed trace is:

- `trace_id=trace_20260729030920802976_815722baf03640098280f6dddd5d5ac1`
- `run_id=run_20260729030920_d7170285`
- `status=completed`, `metadata.merged=true`, `event_count=13`

Its retained trace-array order is chronological and interleaved:

| array index | event | seq | timestamp | merged metadata |
| ---: | --- | ---: | --- | --- |
| 0 | `user:message` | 12 | 03:09:20.821 | |
| 1 | `metadata` | 13 | 03:09:21.232 | |
| 2 | `recommend:questions` | 14 | 03:09:26.386 | |
| 3 | `thinking` (`thinking_id=...20c1...`) | missing | 03:09:28.536 | `merged_count=51` |
| 4 | `tool:start` | 66 | 03:09:31.153 | |
| 5 | `tool:result` | 67 | 03:09:31.161 | |
| 6 | `thinking` (`thinking_id=...4737...`) | missing | 03:09:34.316 | `merged_count=8` |
| 7 | `tool:start` | 76 | 03:09:35.037 | |
| 8 | `tool:result` | 77 | 03:09:35.046 | |
| 9 | `thinking` (`thinking_id=...565c...`) | missing | 03:09:38.536 | `merged_count=10` |
| 10 | `message:chunk` (`text_id` set) | missing | 03:09:39.526 | `merged_count=2` |
| 11 | `token:usage` | 90 | 03:09:40.158 | |
| 12 | `done` | 91 | 03:09:40.163 | |

No `event_id` or `id` is present on these legacy trace-array rows. The preceding trace in the same session has the same shape: a merged `thinking` row with no `seq` between sequenced metadata/recommendation and a sequenced final text row. `trace_events` has zero rows for this session, so this reproduction is the legacy trace-array path, not immutable event-store data.

Replaying this exact array with the repository helper `src.infra.session.history_cursor.event_ordering_key` produces the API/frontend order `thinking(03:09:28), thinking(03:09:34), thinking(03:09:38), message:chunk(03:09:39), user(seq=12), metadata(seq=13), recommend(seq=14), tool:start/result(seq=66/67), tool:start/result(seq=76/77), token(seq=90), done(seq=91)`. This is a direct deterministic reproduction of the reported grouping, not an inferred UI-only effect.

The current frontend sort in `frontend/src/hooks/useAgent/historyLoader.ts:250-276` treats missing sequence as a distinct legacy bucket and explicitly sorts that bucket first (`seqA === null ? -1 : 1` at line 261). Therefore the API rows above become, semantically, `thinking(03:09:28)`, `thinking(03:09:34)`, `thinking(03:09:38)`, `message(03:09:39)`, then `user/metadata/recommendation/tool/tool/token/done`. The visible symptom is exactly the grouped reasoning entries at the beginning.

### How fields were lost

`src/infra/session/event_merger.py` is enabled by default in the local `.env` (`ENABLE_EVENT_MERGER=true`). It scans completed, unmerged traces (`event_merger.py:243-251`), merges `message:chunk` and `thinking` groups (`event_merger.py:37`, `405-457`), and writes the replacement array with `metadata.merged=true` (`350-357`).

For a group larger than one, `_merge_group` returns only `event_type`, a copied `data` payload, and the first timestamp (`event_merger.py:459-498`). It does not carry the source event's top-level `seq`, `event_id`, `id`, `trace_id`, or `run_id`. The reported rows have `merged_count > 1`, making this omission observable. The event merger therefore converts originally sequenced streaming chunks into rows that the history API/frontend must classify as legacy.

This is a pre-existing backend data-loss behavior (introduced with the merger in `742abbb9` and retained through `e45b209b`, `fba2c1aa`, and later refactors), but it became user-visible for mixed rows when the Aug 7 frontend ordering change was applied. Existing merger tests (`tests/infra/test_event_merger.py:206-235`) check content concatenation only; none assert preservation of ordering identity fields.

### Historical-load path

1. `frontend/src/hooks/useAgent.ts:388-411` calls `sessionApi.getAllEvents` during refresh, alongside status/feedback.
2. `frontend/src/services/api/session.ts:51-119` paginates `/api/sessions/{id}/events`, deduplicates by `event_id`/`id` or a content fallback, and preserves API page order. `getEvents` sends the opaque `after` cursor (`session.ts:260-307`).
3. `src/api/routes/session.py:275-348` authenticates ownership and calls `DualEventWriter.read_session_events_page(..., completed_only=True)`, returning events plus `ordering_version`/history metadata.
4. In legacy mode, `src/infra/session/dual_writer.py:891-958` delegates to `TraceStorage.get_session_events_page`. In merge mode it merges immutable and legacy rows and sorts using `event_ordering_key` (`dual_writer.py:853-890`).
5. Legacy aggregation in `src/infra/session/trace_storage.py:1378-1455` uses the same composite bucket (`legacy_bucket`, `seq_sort`, timestamp, trace/event identity, ordinal); missing numeric seq is bucket 0. `src/infra/session/history_cursor.py:89-108` defines the compatibility key identically.
6. `useAgent.ts:430-545` calls `reconstructMessagesFromEvents`. `historyLoader.ts:258-276` re-sorts the received page using the frontend copy of the composite ordering, then iterates it to build assistant messages and parts.
7. `processHistoryEvent` (`historyLoader.ts:84-240`) delegates all message events to `processMessageEvent`. For top-level thinking, `eventProcessor.ts:137-187` merges an event with the most recent same-`thinking_id` part; for missing IDs it searches backward for any undefined-ID thinking part (`eventProcessor.ts:162-174`). That behavior can merge adjacent chunks of a single legacy group, but it is not the primary reported regression: the persisted rows already have distinct IDs and are moved before tools by the outer sort.

### Live-stream comparison

SSE events enter `handleStreamEvent` (`frontend/src/hooks/useAgent/eventHandlers.ts:55-125`), are deduplicated by Redis stream event ID, and are passed directly to `processMessageEvent` in arrival order (`eventHandlers.ts:329-377`) with `isStreaming=true`. Redis delivery order keeps thinking/tool/text interleaving; no historical composite sort runs on this path. This explains why the conversation looks correct before refresh while the same persisted trace is wrong after refresh.

### Regression history

- `8d6f7b0b` (2026-06-25, `feat(useAgent): improve history loader logic`) introduced session `seq` sorting and assistant-bubble reattachment. Its comparator sorted by seq only when both rows had seq, otherwise falling back to timestamps.
- `1795fac0` (2026-08-07, `fix(history): 修正混合事件重建与分页边界`) changed the comparator to match the backend composite key. The new line `if ((seqA !== null) !== (seqB !== null)) return seqA === null ? -1 : 1` intentionally moves all missing-seq rows before sequenced rows. The commit's own test, `frontend/src/hooks/useAgent/__tests__/historyLoader.test.ts:30-57`, asserts this policy (`keeps legacy events before sequenced events`) using user rows, but does not cover a single trace whose merged streaming rows lost seq and must remain timestamp-interleaved.
- `04612bf2` (2026-08-10, SOP history restoration) added a separate `useAgent.ts:481-490` timestamp/seq sort for SOP replay events. It does not feed assistant message reconstruction and is not the cause of the reasoning grouping.

The likely regression boundary is therefore `1795fac0` interacting with existing merger output, not the Aug 10 SOP commit. The merger is the upstream source of missing order fields; the Aug 7 comparator is the change that made the mismatch deterministic and visible.

### Prior-session memory

Read-only `trellis mem` searches found the current report and confirmed the user's exact symptom (refresh causes multiple `已思考` entries to stack at the front). A search for `1795fac0` found the prior history-hardening session describing that commit as the cross-storage merge-order change. No prior decision specifically addressed merger-generated rows losing `seq`; code and Mongo evidence are authoritative.

## Recommended fix boundary and tests

The fix must support already-merged rows without destructive migration. Two compatible layers should be considered:

1. Preserve ordering identity in future merger output: `_merge_group` should retain the first group's top-level ordering fields (at minimum `seq`, event identity, trace/run IDs) or explicitly write a range/first-seq field that the history orderer understands. Add a backend unit test using sequenced thinking/text chunks interleaved with sequenced tool events; assert merged output retains the order key and original relative position.
2. Add a compatibility fallback for existing merged legacy rows that have no seq. A timestamp/trace-array ordinal fallback must be applied consistently in storage/API and frontend reconstruction, or the frontend must detect the merger marker and use the source timestamp order for that trace. Do not silently change the generic legacy-vs-sequenced contract without updating `session-history-pagination.md` and the existing `keeps legacy events before sequenced events` test; that test currently encodes the behavior that caused this incident.

Minimal frontend regression test: call `reconstructMessagesFromEvents` with one user row (`seq=12`), a thinking row at `03:09:28` without seq, sequenced tool start/result (`66/67`), another thinking row at `03:09:34` without seq, and a sequenced final text row at `90` or an unsequenced merged text row at `03:09:39`. Assert the assistant `parts` order is thinking, tool, thinking, tool, text (and that all thinking parts remain separate by distinct `thinking_id`). The current implementation fails because the sorter produces all missing-seq parts before tools.

Required backend/API regression coverage: exercise legacy trace rows with the exact mixed shape through `get_session_events_page`/`DualEventWriter.read_session_events_page`; assert API event order has no gaps/duplicates and preserves the persisted chronological interleaving. Add an event-merger test that verifies merged rows do not discard `seq`/identity fields. Keep a live-path test unchanged or add parity coverage showing Redis/SSE arrival order and historical replay produce the same part sequence for equivalent events.

## Caveats / Not Found

- The local Mongo data is July 29 and already mutated by the background merger; original pre-merge event arrays are not available locally. The `merged_count` and missing top-level fields prove the output shape, while exact original seq values for each merged chunk are inferred from the surrounding sequence gaps.
- `trace_events` has no rows for the reported session, so immutable event-store behavior cannot be compared on this data. The backend specs require a composite order for merge/event-store modes, but the incident is on retained legacy arrays.
- No product code or tests were modified. The working tree remains dirty with unrelated user changes.

## Pagination-safe compatibility design (recommendation)

The generic v2 ordering contract must remain unchanged for genuine mixed sources: `(legacy_bucket, seq, timestamp, trace_id, event_id, ordinal)` with missing-seq rows in bucket 0. The problem is specifically identifiable when a retained row comes from a trace with `metadata.merged=true` and no numeric `seq`; that marker is written by `EventMerger`, not by ordinary pre-seq legacy traces.

Use a scoped history ordering mode:

1. At the start of a legacy-array page read, perform a read-only `find_one`/projection (or equivalent aggregation facet) for the session: `metadata.merged=true` plus an array event whose `seq` is missing. If absent, use the existing v2 pipeline, cursor shape, and `ordering_version=2` unchanged.
2. If present, use `ordering_version=3` for the whole session query. Project `started_at`, `metadata.merged`, and `event_index` along with each event. Emit an internal/public `history_order` tuple on every returned row: `[event_timestamp_or_trace_started_at, trace_started_at, trace_id, event_index]`. `event_timestamp_or_trace_started_at` is the event's timestamp, falling back to the trace's `started_at`; `event_index` is the retained array ordinal. Sort and continue exclusively by this tuple. The event-merger contract says merged output is emitted at the first key occurrence (`event_merger.py:411-413`), so this retained ordinal is the stable chronological position for already-merged rows. It also handles equal timestamps without gaps or duplicates. Add the field to the backend/frontend event schemas (`SSEEventRecord`/`HistoryEvent`) and preserve it in API serialization.
3. Encode v3 cursors with payload version `3`, key length 4, the same scope/filter fingerprint, and the tuple above. Extend `history_cursor.py` validation to accept both v2 and v3 shapes, and have the page reader require the mode it selected; a v2 cursor cannot be applied to a v3 query (return the existing invalid-cursor 400). Do not globally bump/reject v2 cursors for ordinary sessions. Return `ordering_version` and preserve `history_order` through API serialization and `getAllSessionEvents` page accumulation.
4. Make `reconstructMessagesFromEvents` prefer `history_order` when present (lexicographic tuple compare); otherwise retain its existing v2 comparator. This keeps the frontend definition identical to the backend and makes direct unit tests deterministic even if a caller supplies pages out of order. `HistoryEvent` needs an optional typed `history_order` field; `sessionApi.historyEventKey` should include that tuple in its no-ID fallback so an identical payload at two retained ordinals cannot be collapsed during page accumulation. No browser-side cursor synthesis is needed.
5. For future merger writes, `_merge_group` must copy the first group's top-level `seq`, `event_id`/`id`, `trace_id`, and `run_id` when available. That preserves v2 order for newly merged rows and prevents new data from entering the v3 compatibility path. Add a merger test asserting these fields survive a multi-event merge.

Tradeoffs: v3 uses timestamp/trace-start order for all rows in a session once an old merger row is detected, so a pathological trace whose timestamps disagree with native session seq may differ from v2. This is intentional and scoped to data whose original seq was already discarded; it is the only deterministic order recoverable without destructive migration. The retained array/ordinal and opaque v3 cursor guarantee page boundaries, no duplicate rows, and no repeated-page loops.

## Related symptom: only the final turn loads

This is a separate, confirmed Aug 10 regression, not a consequence of the Aug 7 ordering comparator. The local session has two completed traces (plus one `status=running` duplicate for each trace):

- `trace_20260729030913972442_5ffc096712124161bc9a85afd62aa12e`, completed, 7 retained events, user turn `你好呀`.
- `trace_20260729030920802976_815722baf03640098280f6dddd5d5ac1`, completed, 13 retained events, user turn `你有啥技能哦`.

The session document's `metadata.current_run_id` is `run_20260729030920_d7170285`, the second/latest run. The API honors a `run_id` query by adding `match_query["run_id"] = run_id` (`src/infra/session/trace_storage.py:1335-1341`); a request filtered to that value returns only the second trace's 13 events. With all completed traces (no run filter), the API has 20 retained events and both `user:message` rows, so reconstruction can produce both turns.

Git blame pins the accidental filter to `04612bf2` (2026-08-10, SOP DAG history restoration), `frontend/src/hooks/useAgent.ts:387-391`:

```ts
const eventsPromise = sessionApi.getAllEvents(targetSessionId, {
  signal: historyAbortController.signal,
  ...(currentRunId ? { run_id: currentRunId } : {}),
});
```

The same commit added the SOP replay code, but the run filter was not needed for SOP reconstruction and changed the existing behavior from the Aug 7 `a80af817` history-pagination implementation, which called `getAllEvents` without `run_id`. The status request should remain run-scoped for reconnect decisions; the history request must be session-scoped unless the caller explicitly requested a run (for a share/fork/run-detail view).

### Fix boundary and tests for the turn-loss regression

Remove the implicit `currentRunId` filter from the normal `useAgent` session history load, retaining `targetRunId` only when the route explicitly requests a single run. Keep `getStatus(targetSessionId, currentRunId)` and SSE reconnect run-scoped. Add a frontend source/behavior test around `loadHistory` or the request mock asserting refresh calls `/events` without `run_id` and still calls status with `current_run_id`; add a session API test that verifies a supplied explicit `run_id` is forwarded. Add a replay regression using the two persisted-turn shapes and assert `reconstructMessagesFromEvents` returns four messages (`user, assistant, user, assistant`) rather than only the latest pair once v3 order metadata is applied. Without the ordering compatibility fix, removing the run filter will expose the separate missing-seq reorder across both turns. This change is independent of the Aug 7 comparator regression, but the two fixes must be verified together on the reported session.
