# Research: trace event storage and migration boundary

- Query: Trace current event write/read paths, feature flags, Mongo constraints, and a safe immutable `trace_events` migration design compatible with cursor pagination.
- Scope: mixed (internal code/specs; MongoDB behavior from project dependencies and documented BSON/index constraints)
- Date: 2026-08-05

## Findings

### Current write path and loss points

- `src/api/routes/chat.py:433-446,506-524,552-598` creates one `trace_id` early, writes queued `user:message` immediately through `Presenter`, and passes the same ID to queued/local task execution. `src/infra/task/executor.py:95-140,161-181` creates/reuses the presenter and persists stream events; terminal/error paths write and flush additional events (`:223-256,281-313,337-365`).
- `src/infra/writer/presenter_storage.py:99-133` calls `DualEventWriter.create_trace`; `:141-188` turns each presenter event into `dual_writer.write_event`; `:191-226` adds zero token usage before completion, flushes, then updates trace metadata/status. Presenter-local `_done_recorded`/`_goal_end_recorded` flags are not durable event identity.
- `src/infra/session/dual_writer.py:88-142` currently creates legacy array event docs without `event_id`, groups by `trace_id`, and performs `UpdateOne(..., upsert=True)` against `traces`. The `$push` uses `$slice: -SESSION_MAX_EVENTS_PER_TRACE` (`:121-130`), permanently discarding older array entries.
- `DualEventWriter.write_event` places the event in Redis immediately, then appends a tuple to `_mongo_buffer` (`:206-270`). At the configured maximum it drops the oldest half and logs an error (`:237-248`). `_do_flush` removes the batch before `bulk_write`; a failed bulk write is only logged (`:326-387`), so those events are lost. New durable storage must retain failed batches for retry or fail the write visibly; no silent drop is acceptable.
- Session sequence numbers are allocated atomically per session from `session_events_counter` (`src/infra/session/trace_storage.py:161-183`; batch allocation in `dual_writer.py:339-366`). Sequence allocation must happen before buffering/insert and be retained with a retry so the same event ID never receives a new sequence.

### Current read paths and cursor contract

- `src/infra/session/trace_storage.py:988-1176` reads legacy `traces.events` by matching session/run/status, unwinding arrays, and sorting the six-field compatibility key: `legacy_bucket`, `seq_sort`, timestamp, `trace_id`, event ID (or legacy `id`), and array index. `src/infra/session/history_cursor.py:11-108` encodes/validates cursor version 2 and the same six-element key; `get_session_events_page` probes `limit+1` and emits the cursor (`trace_storage.py:1178-1239`).
- New collection reads should project the same response fields (`trace_id`, `run_id`, `event_type`, `data`, `timestamp`, `seq`, `event_id`) and feed `event_ordering_key`. Use ordinal `0` for immutable event documents. Keep ordering version 2 during rollout: legacy missing-seq events remain bucket 0, new sequenced events bucket 1, and `event_id` remains a deterministic tie-breaker. Cursor filtering must remain exclusive (`$gt` on all key components), with filter fingerprints from `history_cursor.py` unchanged.
- Authenticated and share APIs both delegate to `DualEventWriter.read_session_events_page` (`src/api/routes/session.py:275-372`; `src/api/routes/share.py:310-421`). `src/api/routes/session.py:472-519` still exposes a raw diagnostic endpoint using `{"events":{"$slice": -events_limit}}`; this endpoint must remain explicitly legacy/diagnostic or be changed to read `trace_events` when the new source is selected.
- The legacy page result intentionally reports `history_complete: false` because arrays may already be sliced (`trace_storage.py:1231-1237`). Event-store reads may only report complete after the new collection has been selected and the query has exhausted durable records.

### Existing Mongo/index/runtime constraints

- Mongo access is Motor (`pyproject.toml:31,45`, `motor>=3.7.1`, `pymongo>=4.10.0`) through storage classes; there is no schema migration framework (`.trellis/spec/backend/database-guidelines.md`). New collection/index creation therefore belongs in application initialization or an explicit maintenance command.
- The existing trace metadata collection is configured by `settings.MONGODB_TRACES_COLLECTION` (`src/kernel/config/base.py:132-140`, default `traces`). `TraceStorage` currently initializes indexes and globally unique `trace_id` (`src/infra/session/trace_storage.py:212-257`); the separate uniqueness/readiness child owns that gate. `AnalyticsStorage` indexes and aggregates `traces.events` (`src/infra/analytics/storage.py:97-170` and event pipelines), while `EventMerger` reads and rewrites legacy arrays (`src/infra/session/event_merger.py:230-369`). These consumers must be audited or disabled before event-store cutover.
- MongoDB's 16 MB BSON document limit applies to each legacy trace document. Removing `$slice` or merely increasing the cap cannot make an array-backed trace safe. One-event-per-document in `trace_events` is the smallest correctness boundary; reject an individual payload that cannot fit in a BSON document instead of falling back to an oversized legacy update.
- Metadata and event writes are separate operations. Treat the event insert as the source of truth and update `traces.event_count/updated_at/status` as repairable metadata. Do not delete an event when the metadata update fails. Without guaranteed transactions, make every operation idempotent and expose reconciliation metrics.

### Recommended immutable `trace_events` schema and indexes

Each document should contain at least:

```text
event_id   stable UUID/ULID generated before buffering (immutable)
session_id required session scope
trace_id   owning trace
run_id     optional run identifier
seq        session-global monotonic sequence when session is known
timestamp  server event timestamp
event_type normalized presenter event name
data       sanitized JSON payload
```

Recommended indexes:

1. Unique `(session_id, trace_id, event_id)` to make retries idempotent and scope identity correctly.
2. `(session_id, seq, event_id)` for cursor pagination across runs; include timestamp/trace ID in the sort only as tie-breakers when a sequence is absent.
3. `(session_id, run_id, seq, event_id)` (or a prefix-equivalent index) for run-filtered reads.
4. Optional `(trace_id, event_type, timestamp)` for first/last event and analytics lookups.

Use an upsert keyed by the unique identity and `$setOnInsert` for immutable fields. A retry with the same `event_id` must be a no-op (or verify the payload hash and report a conflict), never create a second event or allocate a new sequence. Keep `traces` metadata and legacy `events` during rollout, but do not use the sliced array as the source of truth for new events.

### Feature-flag and rollout recommendation

No event-store flags currently exist. Existing settings are only legacy controls: `SESSION_MAX_EVENTS_PER_TRACE`, `SESSION_EVENT_READ_DEFAULT_LIMIT`, `SESSION_EVENT_MONGO_BUFFER_MAX`, `SESSION_EVENT_TTL_CACHE_MAX`, and `SESSION_EVENT_REDIS_REPLAY_BATCH_SIZE` (`src/kernel/config/base.py:58-64`), plus `ENABLE_EVENT_MERGER` and merger limits (`:142-148`). Do not overload these settings for cutover semantics.

Add internal/runtime flags with explicit, non-contradictory modes (exact names are an implementation choice):

- write mode: `legacy`, `dual`, or `event_store` (default `legacy` during deployment); dual mode writes the immutable collection first, then best-effort legacy compatibility write, with the immutable write failure surfaced/retried;
- read mode: `legacy`, `merge`, or `event_store`; `merge` unions new and legacy records and de-duplicates by `event_id` (legacy records use a deterministic compatibility ID);
- backfill enablement/lease and a per-session/global cutover marker; do not report `history_complete=true` for a session until coverage verification passes.

Keep flags outside admin-configurable settings unless operators need runtime control; if exposed, define them in `SETTING_DEFINITIONS` with safe defaults and restart/rollout semantics. Rollback is a read-mode reversal to legacy/merge and retaining both collections; never delete `trace_events` or legacy arrays as part of cutover.

### Backfill and dual-read design

1. Scan `traces` in bounded batches, projecting metadata and events only. For each `(session_id, trace_id, array ordinal)`, derive a deterministic legacy `event_id` from canonical `(trace_id, ordinal, event_type, timestamp, canonical JSON data)`; preserve existing `seq` when present and retain source ordinal only as a transient tie-breaker.
2. Upsert each event into `trace_events` with the unique key and record an audit row containing source document `_id`, copied count, logical count, checksum, and errors. Retry batches safely; never delete or mutate legacy arrays during backfill.
3. Verify per-session/per-trace coverage and checksums. A legacy trace already truncated by `$slice` cannot be proven complete; mark it incomplete and keep merge/legacy fallback. A payload larger than the BSON limit is an explicit failed row, not a dropped event.
4. In merge reads, query both sources, normalize to the cursor fields, de-duplicate exact `event_id`s, and sort once using `event_ordering_key`. Do not use a legacy array index as a long-lived cursor because `_ensure_token_usage_event` can insert before `done` and shift indexes (`trace_storage.py:450-511`).
5. After coverage verification, switch a session/global marker to event-store reads. Keep legacy arrays for the rollback window; update EventMerger and AnalyticsStorage before removing their array dependency. `history_complete` is then meaningful only for event-store-backed sessions.

### Caveats / Not Found

- No live Mongo instance was inspected; duplicate counts, payload sizes, and actual index build times are unknown.
- Existing `TraceStorage` readiness and globally unique `trace_id` behavior is being handled by sibling task `08-05-trace-uniqueness-readiness`; this research assumes its write gate is awaited and does not redesign it.
- `EventMerger`, analytics aggregations, fork/clone code (`src/infra/session/manager.py:380-453`), recovery/status helpers, and the raw-traces endpoint all directly consume `traces.events`; event-store cutover is incomplete until each consumer is adapted or explicitly left on legacy mode.
- Backfill cannot recover events already discarded by `$slice`, buffer overflow, or failed bulk writes; coverage must report those gaps rather than infer completeness.

## Related specs and task artifacts

- `.trellis/spec/backend/database-guidelines.md` (Motor storage conventions; no migration framework).
- `.trellis/tasks/08-05-session-history-trace-hardening/research/history-trace-hardening-design.md` (parent contract for cursor ordering, 16 MB boundary, and child decomposition).
- `.trellis/tasks/08-05-trace-event-storage/design.md` (one-event-per-document recommendation and dual-write/backfill requirements).
- `.trellis/tasks/08-05-session-history-cursor/design.md` (cursor API/order contract).
