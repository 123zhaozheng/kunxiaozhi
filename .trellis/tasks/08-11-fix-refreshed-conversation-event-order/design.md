# Design: Refreshed Conversation History Regressions

## Boundaries

The fix spans two independent defects on the same history path:

1. Session scope: `useAgent` must fetch ordinary history for the whole session, while status/reconnect and explicit run views remain run-scoped.
2. Event order: legacy merger output that already lost sequence identity needs a scoped, pagination-safe compatibility order; future merger output must stop losing those fields.

No stored document is rewritten as part of the request path.

## Data Flow

```text
Mongo trace arrays / immutable events
  -> TraceStorage / DualEventWriter page ordering
  -> session events API (ordering_version + optional history_order)
  -> frontend page accumulation and deduplication
  -> reconstructMessagesFromEvents
  -> ordered user/assistant messages and assistant parts
```

Live SSE continues to call the existing event processor in Redis arrival order. Tests compare the resulting part order with historical replay.

## Session-Scope Repair

- The normal `useAgent` history request uses `targetSessionId` and its abort signal without injecting metadata-derived `currentRunId`.
- An explicit route/caller run selection may still pass `targetRunId` to the events API.
- The status request and SSE reconnect logic continue using the active/current run ID.
- The API's existing optional `run_id` filter remains supported; the repair is at the accidental caller boundary.

## Scoped Compatibility Ordering

### Mode selection

Before producing the first page, the backend determines whether the selected session contains a trace marked `metadata.merged=true` with at least one retained event missing numeric `seq`.

- No affected trace: retain ordering version 2 and its existing `(legacy_bucket, seq, timestamp, trace_id, event_id, ordinal)` contract unchanged.
- Affected trace: select ordering version 3 for the entire query and every page.

The selected mode is encoded into the opaque cursor. A cursor from another version or a mismatched scope/filter remains invalid and returns the existing 400 behavior.

### Version 3 key

For affected retained arrays, derive a stable compatibility key from source data that survived the merger:

```text
(event_timestamp_or_trace_started_at, trace_started_at, trace_id, retained_event_index)
```

This mode is scoped to data whose original sequence identity is already unavailable. `retained_event_index` is the deterministic tie-breaker and preserves the merger's first-occurrence position. Backend query sorting and exclusive cursor continuation must use the same tuple.

Every v3 event returned by the API carries typed `history_order` data. The frontend prefers that server-provided key when reconstructing events; events without it keep the existing v2 comparator. Page accumulation and fallback deduplication must retain the field.

`DualEventWriter` must propagate one ordering mode for the complete response. If legacy and immutable sources are merged for an affected session, all returned rows must receive a comparable v3 key before global sorting and pagination; source-local v2/v3 ordering must not be mixed.

### Future merger output

For a multi-row `thinking` or `message:chunk` group, build the merged event from a copy of the first source row and replace only the merged payload/timestamp fields. Preserve available top-level `seq`, `event_id`/`id`, `trace_id`, and `run_id`. New merger output therefore remains on the normal v2 path.

## Contracts And Compatibility

- Existing version 2 cursors continue to work for unaffected sessions.
- Version 3 cursors are accepted only when the current query selects compatibility mode.
- `history_order` is transport metadata, not a replacement for original event `seq` and not synthesized by the browser.
- Existing stored data is read-only and remains valid.
- An explicit `run_id` still scopes both mode detection and event reads to the requested run.

## Failure Handling

- Invalid or mismatched v2/v3 cursor: existing `InvalidHistoryCursor` path and HTTP 400.
- Missing event timestamp in v3: fall back to trace `started_at`; retained index remains the final deterministic tie-breaker.
- Later-page frontend failure/cancellation: retain the existing partial-history behavior and already loaded pages.

## Rollback

- The session-scope repair can be reverted independently by restoring the history request filter.
- The ordering repair can revert v3 mode selection/serialization while leaving future merger field preservation in place.
- No rollback deletes or rewrites Mongo data.

## Test Strategy

- Backend merger unit test: a multi-row group preserves first-row ordering/identity fields.
- Backend cursor/storage tests: affected merged arrays select v3, paginate exclusively without gaps/duplicates, and preserve retained chronology; unaffected sessions remain v2; mismatched cursor versions fail.
- Merge-read test: v3 ordering remains global and deterministic when legacy and immutable sources are combined.
- Frontend history reconstruction test: missing-`seq` merged thinking/text stays interleaved with sequenced tools via `history_order`.
- Frontend load test: ordinary history omits metadata-derived `run_id`, explicit run selection forwards it, and status remains run-scoped.
- Two-turn replay test: retained events reconstruct to `user, assistant, user, assistant`.
