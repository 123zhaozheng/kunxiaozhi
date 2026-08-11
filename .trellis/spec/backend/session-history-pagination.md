# Session History Cursor Pagination

## Scenario: Session and public-share history reads

### 1. Scope / Trigger

Use this contract whenever session history or public-share history is read across the storage, FastAPI, and frontend layers. It exists because a fixed event limit can silently make a retained history look complete, while read-time cleanup can mutate or delete source data.

### 2. Signatures

- Storage: `TraceStorage.get_session_events_page(session_id, event_types=None, run_id=None, exclude_run_id=None, completed_only=True, run_ids=None, limit=None, after=None) -> dict`
- Ordering probe: `TraceStorage.get_history_ordering_version(session_id, run_id=None, exclude_run_id=None, completed_only=True, run_ids=None) -> int`
- Cursor codec: `encode_history_cursor(scope, fingerprint, key, ordering_version=2) -> str` and `decode_history_cursor(cursor, scope, fingerprint, ordering_version=None) -> list`
- Authenticated API: `GET /api/sessions/{session_id}/events?limit=<1..10000>&after=<opaque>`
- Public-share API: `GET /api/share/public/{share_id}?limit=<1..10000>&after=<opaque>`; `event_limit` remains a compatibility alias when `limit` is absent.
- Frontend loaders: `getAllSessionEvents(sessionId, { limit?, signal?, run_id?, ...filters })` and `getAllSharedContent(shareId, { limit?, signal? })`.
- Version 3 event transport: `history_order?: [event_timestamp, trace_started_at, trace_id, retained_event_index]`.

### 3. Contracts

- A cursor is opaque to clients and is exclusive. It binds ordering version, session scope, active filters, and the final event ordering key from the previous page.
- Version 2 is the default compatibility order: `(legacy_bucket, seq, timestamp, trace_id, event_id, ordinal)`. Genuine legacy rows without `seq` remain before sequenced rows. Existing v2 cursors and unaffected sessions keep this behavior.
- Version 3 is scoped to a session/run query containing `metadata.merged=true` trace arrays with a missing or non-numeric event `seq`. Its key is `(event_timestamp_or_trace_started_at, trace_started_at, trace_id, retained_event_index)`. Every returned v3 event carries the same typed `history_order` tuple.
- Ordering mode is selected once for the complete scoped query before reading a page. Query sorting, response `ordering_version`, event `history_order`, cursor encoding, and exclusive continuation must all use that same mode.
- A normal conversation history load is session-scoped and must not inherit `metadata.current_run_id`. Only an explicit run-focused caller supplies `run_id`; status and SSE reconnect requests may remain scoped to the active run.
- Storage reads `limit + 1`, returns at most `limit`, and sets `has_more=true` only when the probe finds another retained event.
- `has_more=true` requires a non-null `next_cursor`. The frontend treats a missing or repeated continuation cursor as an incomplete-history error instead of looping forever.
- Responses carry `events`, `has_more`, `next_cursor`, `history_complete`, and `ordering_version`. `events_limited` is a compatibility alias for `has_more`, not proof that history is durable or complete.
- Legacy trace arrays may already be truncated, so reaching their retained end does not prove completeness. Until the durable event-store cutover establishes that proof, storage returns `history_complete=false`.
- Normal history GET paths are read-only: they must not deduplicate trace documents, reconcile stale state, or perform any other database write.
- Frontend loaders preserve already loaded pages on later-page failure or cancellation. Non-abort failures set `history_complete=false` and `history_error`; `AbortError` sets incomplete history without presenting an error.
- Every fetch layer must pass the same `AbortSignal` to the native `fetch`. Components must own an `AbortController`, abort on identity/effect cleanup, and suppress stale state updates.
- History reconstruction must preserve this composite ordering when normalizing mixed legacy and immutable events; a timestamp-only fallback is not equivalent when only some events have `seq`.
- Frontend reconstruction prefers `history_order` only when both compared events have valid v3 tuples; otherwise it retains the v2 comparator. Browsers never synthesize cursor or compatibility keys.
- In merge read mode, legacy and immutable rows use one ordering version before global sort/dedup/page slicing. On continuation, read enough bounded retained history to apply the exclusive cursor before taking `limit + 1`; taking `limit + 1` first can make later pages empty.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Malformed cursor, wrong version, scope, filters, or key types | Storage raises `InvalidHistoryCursor`; API returns HTTP 400 |
| v2 cursor supplied to a v3 query, or v3 cursor supplied to a v2 query | Reject as an ordering mismatch; never reinterpret the key |
| Merger-marked scoped history contains a missing/non-numeric `seq` | Select v3 for every source and page; emit `history_order` on every returned event |
| Normal session reload has a metadata `current_run_id` | Fetch all completed session turns; do not send an implicit `run_id` filter |
| Explicit run-focused history request | Apply the requested `run_id` to mode detection, reads, and cursor fingerprint |
| `limit` outside `1..10000` | FastAPI validation rejects the request |
| `has_more=true` with missing or repeated `next_cursor` | Frontend stops, preserves loaded events, and reports incomplete history |
| Later page returns an error | Preserve prior pages; set `history_complete=false` and `history_error` |
| Request is aborted | Preserve prior pages; set incomplete state; do not surface an error or update an obsolete component |
| Session/share is missing or unauthorized | Preserve the route's existing 404/401/ownership behavior |

### 5. Good / Base / Bad Cases

- Good: request page 1 with `limit=1000`, pass its opaque `next_cursor` unchanged to page 2, deduplicate by stable event identity/`history_order`, and stop when `has_more=false`.
- Good: a merger-marked trace with missing `seq` returns `ordering_version=3`; reasoning, tools, and text remain interleaved by the server-provided tuple after frontend reconstruction.
- Base: a one-page response returns all retained events, `has_more=false`, and `next_cursor=null`; completeness still comes from `history_complete`, not from `has_more`.
- Bad: pass `current_run_id` from session metadata into the normal history request, globally change v2 missing-sequence ordering, decode/synthesize cursors in the browser, or slice a merged source before applying a continuation cursor.

### 6. Tests Required

- Cursor codec: v2 and v3 round trips; malformed payload; wrong/query-mismatched version, scope, and filter fingerprint; version-specific key lengths and types.
- Storage/API: scoped v3 detection; unaffected v2 behavior; exclusive boundaries without gaps or duplicates; deterministic tie breakers; `limit + 1`; HTTP 400 for invalid cursor; session/share contract parity; no write calls from history GET.
- Merge mode: shared v3 order across legacy/immutable rows; continuation cursors are applied before page slicing; later pages remain reachable.
- Frontend session and share loaders: multipage order; stable identity deduplication; cursor forwarding; repeated-cursor guard; partial failure retention and metadata; AbortSignal forwarding and AbortError retention.
- Frontend reconstruction: an affected merged event stream retains reasoning/tool/text interleaving; two completed traces reconstruct all user/assistant turns; normal history omits implicit `run_id` while explicit run history forwards it.
- Components: incomplete-history notice is visible without blocking already loaded content; cleanup aborts obsolete loads and prevents stale updates.

### 7. Wrong vs Correct

#### Wrong

```typescript
const page = await sessionApi.getEvents(sessionId, { limit: 10000 });
setEvents(page.events); // A capped page is treated as the whole history.
```

#### Correct

```typescript
const controller = new AbortController();
const result = await sessionApi.getAllEvents(sessionId, {
  signal: controller.signal,
  // Add run_id only when the user explicitly opened a run-scoped view.
});
setEvents(result.events);
setHistoryIncomplete(result.history_complete === false);
```
