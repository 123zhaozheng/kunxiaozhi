# Session History Cursor Pagination

## Scenario: Session and public-share history reads

### 1. Scope / Trigger

Use this contract whenever session history or public-share history is read across the storage, FastAPI, and frontend layers. It exists because a fixed event limit can silently make a retained history look complete, while read-time cleanup can mutate or delete source data.

### 2. Signatures

- Storage: `TraceStorage.get_session_events_page(session_id, event_types=None, run_id=None, exclude_run_id=None, completed_only=True, run_ids=None, limit=None, after=None) -> dict`
- Cursor codec: `encode_history_cursor(scope, fingerprint, key) -> str` and `decode_history_cursor(cursor, scope, fingerprint) -> list`
- Authenticated API: `GET /api/sessions/{session_id}/events?limit=<1..10000>&after=<opaque>`
- Public-share API: `GET /api/share/public/{share_id}?limit=<1..10000>&after=<opaque>`; `event_limit` remains a compatibility alias when `limit` is absent.
- Frontend loaders: `getAllSessionEvents(sessionId, { limit?, signal?, ...filters })` and `getAllSharedContent(shareId, { limit?, signal? })`.

### 3. Contracts

- A cursor is opaque to clients and is exclusive. It binds ordering version, session scope, active filters, and the final event ordering key from the previous page.
- The compatibility ordering key is `(legacy_bucket, seq, timestamp, trace_id, event_id, ordinal)`. The query sort, cursor encoding, and exclusive continuation predicate must use the same fields in the same order.
- Storage reads `limit + 1`, returns at most `limit`, and sets `has_more=true` only when the probe finds another retained event.
- `has_more=true` requires a non-null `next_cursor`. The frontend treats a missing or repeated continuation cursor as an incomplete-history error instead of looping forever.
- Responses carry `events`, `has_more`, `next_cursor`, `history_complete`, and `ordering_version`. `events_limited` is a compatibility alias for `has_more`, not proof that history is durable or complete.
- Legacy trace arrays may already be truncated, so reaching their retained end does not prove completeness. Until the durable event-store cutover establishes that proof, storage returns `history_complete=false`.
- Normal history GET paths are read-only: they must not deduplicate trace documents, reconcile stale state, or perform any other database write.
- Frontend loaders preserve already loaded pages on later-page failure or cancellation. Non-abort failures set `history_complete=false` and `history_error`; `AbortError` sets incomplete history without presenting an error.
- Every fetch layer must pass the same `AbortSignal` to the native `fetch`. Components must own an `AbortController`, abort on identity/effect cleanup, and suppress stale state updates.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Malformed cursor, wrong version, scope, filters, or key types | Storage raises `InvalidHistoryCursor`; API returns HTTP 400 |
| `limit` outside `1..10000` | FastAPI validation rejects the request |
| `has_more=true` with missing or repeated `next_cursor` | Frontend stops, preserves loaded events, and reports incomplete history |
| Later page returns an error | Preserve prior pages; set `history_complete=false` and `history_error` |
| Request is aborted | Preserve prior pages; set incomplete state; do not surface an error or update an obsolete component |
| Session/share is missing or unauthorized | Preserve the route's existing 404/401/ownership behavior |

### 5. Good / Base / Bad Cases

- Good: request page 1 with `limit=1000`, pass its opaque `next_cursor` unchanged to page 2, deduplicate by stable event identity, and stop when `has_more=false`.
- Base: a one-page response returns all retained events, `has_more=false`, and `next_cursor=null`; completeness still comes from `history_complete`, not from `has_more`.
- Bad: increase one fixed limit and assume the result is complete, decode or synthesize cursors in the browser, restart from page 1 after a partial failure, or run cleanup from a GET handler.

### 6. Tests Required

- Cursor codec: round trip; malformed payload; wrong version, scope, and filter fingerprint; invalid key types.
- Storage/API: exclusive boundary without gaps or duplicates; deterministic tie breakers; `limit + 1`; HTTP 400 for invalid cursor; session/share contract parity; no write calls from history GET.
- Frontend session and share loaders: multipage order; stable identity deduplication; cursor forwarding; repeated-cursor guard; partial failure retention and metadata; AbortSignal forwarding and AbortError retention.
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
});
setEvents(result.events);
setHistoryIncomplete(result.history_complete === false);
```

