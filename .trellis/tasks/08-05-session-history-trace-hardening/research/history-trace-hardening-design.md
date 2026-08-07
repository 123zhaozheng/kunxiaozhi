# Research: session history and trace hardening design

- Query: Design an implementation-ready, backward-compatible hardening plan for complete session/share history, trace uniqueness, duplicate-document repair, and stale-running recovery.
- Scope: internal code and tests; external MongoDB behavior is limited to documented 16 MB BSON/index semantics; no live database writes or migrations.
- Date: 2026-08-05

## Findings

### Worktree boundary and existing fixes

The worktree is intentionally dirty. The history-related edits already present must be treated as user-owned/proposed fixes, not overwritten:

- `frontend/src/services/api/session.ts:184-218` adds an unconditional `limit=10000` request.
- `src/infra/session/trace_storage.py:43-48` raises the single-read clamp from 5,000 to 10,000.
- `src/infra/session/trace_storage.py:751-864` adds stale-running reconciliation and read-time duplicate deletion.
- `frontend/src/hooks/useAgent/historyLoader.ts:62-142` and `frontend/src/hooks/useAgent/historyLoader.ts:220-455` add SOP filtering, session-level `seq` sorting, and run-aware assistant IDs.
- `tests/infra/session/test_trace_storage_token_usage.py:122-170,360-464` was adjusted for the extra dedup aggregate and new 10,000 clamp; `tests/infra/session/test_trace_stale_reconcile.py:1-135` covers the new helper methods.

These changes explain the immediate regression improvement but do not establish a complete-history contract. The remaining design should preserve the behavioral intent (stable ordering, no duplicate assistant keys, terminal-event recovery) while moving cleanup out of reads and removing fixed-limit data loss.

### Current history/read contract

The authenticated route accepts `limit` up to `SESSION_EVENT_RESPONSE_LIMIT_MAX` and probes with `limit + 1` (`src/api/routes/session.py:274-341`). At exactly the maximum, `limit + 1` is clamped by storage, so an event at position 10,001 is not detectable. An omitted limit is described as unbounded but storage applies the configured default (`src/infra/session/trace_storage.py:880-886`, default 1,000 at `:43-54`). The public share route has only `ge=1` and forwards `event_limit + 1` (`src/api/routes/share.py:274-331`), so a caller can request more than the storage clamp and receive a false `events_limited=False` at the clamp boundary.

`TraceStorage.get_session_events` filters by `session_id`, optionally by run, unwinds all trace arrays, then sorts by missing-seq-as-zero, `started_at`, and event timestamp (`src/infra/session/trace_storage.py:869-947`). Missing `seq` values therefore precede all sequence-bearing events, regardless of their timestamp. The sort has no immutable event ID or array-index tie-breaker, so equal sequence/timestamp values are nondeterministic. The frontend repeats a less complete fallback: both present seq values are compared, otherwise timestamps are compared (`frontend/src/hooks/useAgent/historyLoader.ts:239-265`).

`loadHistory` calls exactly one `sessionApi.getEvents` request and reconstructs whatever is returned (`frontend/src/hooks/useAgent.ts:372-407`). It does not inspect `events_limited`, retry a page, or expose an incomplete-history state. Public shares similarly issue one request and reconstruct one response (`frontend/src/components/share/SharedPage.tsx:234-270`), while `frontend/src/types/session.ts:61-63` does not type the response metadata and `frontend/src/types/share.ts:49-72` omits `events_limited`/continuation fields.

### Recommended pagination contract

Use a forward, session-global cursor, with an opaque token and the same contract on session and share endpoints. Do not use offset pagination: `skip` becomes increasingly expensive, and inserts arriving between requests shift the window and cause duplicates or gaps. A raw `seq` cursor alone is insufficient for legacy events without `seq` and for duplicate/identical sequence values. A composite cursor is the smallest compatible robust choice.

#### Request

Keep existing query names and add optional fields:

```text
GET /api/sessions/{session_id}/events
  ?limit=500                 # 1..500 (or the existing max during rollout)
  &after=<opaque cursor>     # omit for the first/oldest page
  &event_types=...
  &run_id=...
  &exclude_run_id=...

GET /api/share/public/{share_id}
  ?limit=500
  &after=<opaque cursor>
  ...                         # existing share/run filtering unchanged
```

The cursor is base64url-encoded JSON (or signed equivalent) containing `version`, `session_id`, filter fingerprint, and an exclusive ordering key. Reject a cursor if its session/share filter or ordering version differs. The cursor is opaque to clients.

#### Response

Add fields without removing old ones:

```json
{
  "events": [],
  "session_id": "...",
  "run_id": "...",
  "events_limited": false,
  "events_limit": 500,
  "has_more": true,
  "next_cursor": "...",
  "history_complete": false,
  "ordering_version": 2
}
```

`has_more` is authoritative; `events_limited` remains a deprecated compatibility alias (`has_more` for a bounded request). `history_complete` is true only when the server has reached the end of durable events, not merely when a 10,000-item probe returned exactly 10,000. Return `next_cursor=null` when `has_more=false`. For an old client that sends `limit=10000` and ignores new fields, the first page remains valid but is not silently called complete; the frontend must loop.

#### Ordering key and legacy policy

New event records should carry a stable `event_id` (UUID/ULID), `seq` when a session is known, `timestamp`, `trace_id`, and an immutable per-trace insertion ordinal. The cursor key is:

```text
(legacy_bucket, seq_or_0, timestamp, trace_id, event_id)
```

where `legacy_bucket=0` for missing-seq events and `1` otherwise, matching the current documented legacy-before-new behavior. `event_id` is the final tie-breaker; `trace_id` is retained for readable/debug ordering. Do not use an array index as a long-lived cursor because `_ensure_token_usage_event` can insert an event before `done` (`src/infra/session/trace_storage.py:362-424`) and shift indexes. For legacy events without an ID, the read layer may temporarily derive a deterministic hash of `(trace_id, legacy array ordinal, event_type, timestamp, canonical data)`, but this is only a compatibility bridge; backfill immutable IDs before claiming exact duplicate elimination.

The Mongo pipeline should `$unwind` with `includeArrayIndex` only for legacy hashing, add a normalized sort key, apply an exclusive `$gt` predicate from the decoded cursor, sort by all key fields, and fetch `limit + 1`. Include `event_id` in the projected API event when available. Sequence gaps are valid (failed writes/retries); the cursor must not assume contiguous seq values.

#### Frontend consumption

`sessionApi.getEvents` should accept `limit`, `after`, and existing filters, and type all response metadata in `SessionEventsResponse` (`frontend/src/services/api/session.ts:193-218`; `frontend/src/types/session.ts:53-63`). `loadHistory` should loop while `has_more`, append pages in order, preserve already loaded pages on abort/error, and set a diagnostic incomplete-history state if a request fails or the server returns `history_complete=false` at an operator safety limit. It must not deduplicate by array position; use `event_id` when present and the cursor key for legacy pages. Share loading should use the same loop and metadata types (`frontend/src/services/api/share.ts:58-68`, `frontend/src/components/share/SharedPage.tsx:234-270`).

This contract is backward compatible because all new request parameters are optional, old response fields remain, and old clients still receive an event array. It fixes the maximum-limit probe bug and makes truncation visible to clients.

### 16 MB BSON and the smallest safe storage boundary

The current write path cannot guarantee complete history. `DualEventWriter` batches events and uses `$push` with `$slice: -max_events` (`src/infra/session/dual_writer.py:117-140`); the configured per-trace cap is 10,000 (`src/kernel/config/base.py:60-62`). This permanently discards older events before pagination can read them. Worse, a Mongo document is limited to 16 MB: a 10,000-event array can exceed that limit depending on reasoning/tool payload sizes, and the update fails rather than safely paging. The buffer overflow branch explicitly drops oldest entries when Mongo is slow (`src/infra/session/dual_writer.py:237-247`), another loss path.

There is no safe bounded interim value. Raising/removing `$slice`, raising the cap, or increasing the API limit only moves the loss/16 MB failure threshold. Do not advertise `history_complete=true` while any writer truncates or drops events.

#### MVP boundary (recommended)

Introduce an immutable `trace_events` collection with one document per event (or a strictly byte-bounded segment whose maximum is well below 16 MB); the one-event document is the smallest correctness-first implementation. Keep `traces` as metadata/legacy storage during rollout. New event writes should:

1. Allocate `seq` and `event_id`.
2. Insert/upsert the event document keyed by `(session_id, trace_id, event_id)` with retryable idempotency.
3. Update trace metadata (`event_count`, `updated_at`, status) separately; metadata failure is repaired by a reconciliation job, never by dropping the event.

Add indexes for `(session_id, seq, event_id)`, `(session_id, trace_id, event_id)` unique, and `(session_id, run_id, seq)` as needed. Read the new collection for events written after the cutover and fall back to legacy trace arrays for pre-cutover data. A backfill job should copy legacy array events into `trace_events` with deterministic IDs and an audit record; only after coverage verification should the read path stop consulting arrays for migrated sessions. Keep the legacy `$slice` field for compatibility but do not use it as the source of truth for new writes.

If the team cannot implement the collection in this task, the safe staged boundary is: remove read-path claims of completeness, return `history_complete=false`/`has_more` at the cap, emit a loss metric, and explicitly defer complete-history acceptance. Do not silently raise the cap or remove `$slice` as an MVP.

The existing EventMerger (`src/infra/session/event_merger.py`, referenced by `trace_storage.py:205-219`) assumes array-backed traces and must be disabled for new event documents or updated to operate on the event collection in a follow-up child task. This is an explicit dependency, not a reason to retain lossy arrays.

### Trace uniqueness and index readiness

`TraceStorage.ensure_indexes_if_needed` sets `_indexes_ensured=True` and launches `_ensure_indexes` with `asyncio.create_task` (`src/infra/session/trace_storage.py:148-155`). The startup initializer awaits only this wrapper (`src/api/main.py:331-335`); the application therefore reports startup readiness before the unique index exists. `_ensure_indexes` has one broad try around all indexes (`src/infra/session/trace_storage.py:157-203`), so duplicate historical documents can make `trace_id_unique_idx` fail and prevent later indexes; the flag then suppresses retries.

Recommended behavior:

- Replace the fire-and-forget flag with an instance-level lock/task and an awaitable readiness state. Startup must await completion of the trace index initializer before serving traffic.
- Create non-unique operational indexes separately, then preflight duplicate groups. Create `trace_id_unique_idx` only after the preflight/migration reports zero conflicting `trace_id` values (or use a session-scoped key if the business contract is actually `(session_id, trace_id)`).
- On `DuplicateKeyError`/network failure, leave readiness false, log the index name and duplicate count/error, and retry with bounded exponential backoff. Do not return a successful startup/index-ready signal.
- If operations deliberately choose degraded startup while a migration is pending, expose a health/readiness failure and disable trace writes, rather than accepting duplicate-producing writes.

`create_trace` should become an idempotent upsert (`$setOnInsert` for the full initial document, plus a guarded merge of missing metadata) after index readiness. Keep the `DuplicateKeyError` fallback for races during rolling deployment, but validate that an existing document has the same `session_id` and `run_id`; a mismatch is a visible data-integrity error, not success. Concurrent callers then converge on one document, and metadata is never overwritten by a later partial create. Existing callers are genuinely concurrent: pre/queued and worker presenters reuse one trace (`src/api/routes/chat.py:436-446,507-524`, `src/infra/task/executor.py:95-129`), while `_trace_created` is only per presenter (`src/infra/writer/presenter_storage.py:99-135`).

### Existing duplicate-document repair

Do not call `dedup_duplicate_traces` from `get_session_events`. The current method sorts only by `event_count`, picks arbitrary ties, deletes by `trace_id` without `session_id`, deletes whole documents instead of merging event subsets, limits to 200 groups, and swallows errors (`src/infra/session/trace_storage.py:784-828`). It can lose unique events and race active writers; the tests currently encode this destructive behavior (`tests/infra/session/test_trace_stale_reconcile.py:67-135`).

Provide an explicit admin/maintenance command (dry-run by default), for example `trace-admin deduplicate --session <id> [--trace <id>] [--apply]`. Required behavior:

1. Acquire a distributed per-session/trace maintenance lease (Redis lock) and re-read the group after the lease. Refuse/skip a group with a running trace or live task heartbeat; allow an operator override only with an explicit force flag and audit entry.
2. Group strictly by `(session_id, trace_id)`, report all document IDs, statuses, event counts, byte estimates, and conflicts. Never infer global uniqueness across sessions without a separate invariant.
3. Write a backup copy of every source document to an audit/backup collection and append an operation record before mutation. Dry-run emits the exact planned merge and deletion set without writes.
4. Select the canonical metadata document deterministically: highest `updated_at`, then highest `completed_at`, then lowest `_id` as tie-breaker. Merge missing metadata from the other documents without overwriting non-empty canonical fields.
5. Merge event arrays by `event_id`; for legacy events use a canonical hash of `(seq, event_type, timestamp, canonical JSON data)` and retain both records when the hash differs. Sort merged events by the pagination ordering key, with source `_id`/array ordinal as a final deterministic tie-breaker. Preserve `event_count` as the logical total, not the retained-array length.
6. Write the merged result, verify counts/checksums, then delete only the exact source `_id`s in the same session scope. Record deleted IDs and result counts. If a transaction is unavailable, use a two-phase marker (`metadata.merge_operation_id`, `merged_into`) and make reruns idempotent.
7. Rollback restores the backup documents and removes the merged document using the recorded operation ID; do not run irreversible deletion without a backup and operator confirmation.

Do not create the unique index until this command (or an equivalent reviewed migration) reports zero duplicates. Leave read-time duplicate visibility/audit metrics in place, but never mutate from a normal GET.

### Stale-running recovery outside reads

The current helper updates every `running` trace containing `done` or `error` (`src/infra/session/trace_storage.py:751-782`) immediately before a read (`:857-864`). That can mark a live run terminal if a terminal-looking event is emitted early and late events follow; it also omits any `complete` event variant. The worker lifecycle shows the stronger source of truth: `TaskExecutor` starts a heartbeat before work and stops it in `finally` (`src/infra/task/executor.py:90-129,203-209`), normal completion calls `presenter.complete("completed")` (`:180-185`), and cancellation/interruption/error paths update task status, emit terminal events, flush, and complete/error the presenter (`:223-371`). Startup cleanup already checks task status and Redis heartbeat before recovering stale sessions (`src/infra/task/startup_cleanup.py:330-464`).

Move trace reconciliation to a scheduled/admin worker (or extend startup cleanup) that:

- selects only `status=running` traces older than a grace period;
- joins/validates the owning session `metadata.task_status` and `current_run_id`;
- checks Redis heartbeat for the `run_id` (and worker/task registry where available);
- requires a terminal task status (`completed`, `failed`, `cancelled`, `expired`) or an absent heartbeat past the timeout plus a persisted terminal event (`done`, `error`, and any explicitly supported `complete` marker);
- atomically updates `status` with a compare-and-set filter still requiring `status=running`, records `reconciled_at`, `reconciled_reason`, and the observed heartbeat/task state;
- retries/alerts on failure and exposes counts/age metrics.

An active writer can therefore continue appending while heartbeat is present; a stale trace without a terminal event is not silently marked completed. The normal history read becomes side-effect free and simply applies the documented completed/running filter.

### Tests and affected files

Backend implementation/tests:

- `src/infra/session/trace_storage.py`, `src/infra/session/dual_writer.py`: event IDs, cursor pipeline, readiness/index retry, event-collection writes, removal of read mutations.
- `src/api/routes/session.py`, `src/api/routes/share.py`, `src/kernel/schemas/share.py`: shared cursor/response schema and validation.
- `src/api/main.py`: await trace index readiness and health failure semantics.
- `src/infra/writer/presenter_storage.py`, `src/infra/task/executor.py`: idempotent trace creation and event write contract.
- New event storage module and migration/admin command under `src/infra/session/` / existing CLI/admin conventions.
- Tests: extend `tests/api/routes/test_session_runs.py:376-415`, `tests/api/routes/test_share_public_limits.py:151-199`, `tests/infra/session/test_trace_storage_token_usage.py:333-464`; add cursor boundary/legacy ordering, event ID idempotency, >16 MB payload rejection, no-loss collection writes, index readiness/retry, upsert races, dry-run/merge/audit/rollback, and heartbeat reconciliation tests. Replace `tests/infra/session/test_trace_stale_reconcile.py:67-135` destructive-dedup expectations with read-is-pure assertions and admin-command tests.

Frontend implementation/tests:

- `frontend/src/services/api/session.ts`, `frontend/src/services/api/share.ts`, `frontend/src/types/session.ts`, `frontend/src/types/share.ts`: request/response cursor types and metadata.
- `frontend/src/hooks/useAgent.ts` / `frontend/src/hooks/useAgent/historyLoader.ts`: page loop, abort/error preservation, event-ID dedup, incomplete-history diagnostic.
- `frontend/src/components/share/SharedPage.tsx`: same continuation loop and visible incomplete state.
- Existing `frontend/src/hooks/useAgent/__tests__/historyLoader.test.ts` and `frontend/src/hooks/__tests__/useAgentLoadHistoryRace.test.ts` should add multi-page ordering, cursor failure retention, and duplicate-event identity cases; add API service tests for query encoding.

### Recommended task decomposition and order

This request should be a parent task with independent children; a single implementation is too broad and crosses storage, API, frontend, migration, and operations.

1. **Child A, contract/read path (dependency foundation):** define shared cursor codec, ordering version, session/share response models, side-effect-free read pipeline, legacy ordering/tie-break behavior, and API/frontend page-loop tests. It can initially read the legacy array source but must report `history_complete=false` at the cap.
2. **Child B, durable event storage (depends on A's event schema):** add `trace_events`, event IDs, idempotent writes, indexes, no `$slice`/buffer-drop loss for new data, dual-read/backfill flags, and 16 MB tests. This is the actual completeness boundary.
3. **Child C, uniqueness/readiness (independent, can run before B):** awaited/retriable indexes, duplicate preflight, readiness/health behavior, and atomic idempotent `create_trace` tests.
4. **Child D, duplicate migration/admin tooling (depends on C and event identity from B):** dry-run/audit/backup, deterministic merge, active-writer protection, rollback, and unique-index gate.
5. **Child E, stale-running reconciler (independent after lifecycle contract):** scheduled/startup worker integration using task status + heartbeat, compare-and-set updates, metrics, and race tests.
6. **Parent integration/check:** run full backend/frontend checks, verify session and share use exactly the same pagination semantics, and perform rollout/rollback rehearsal.

Suggested MVP is A + C + E plus an explicit incomplete-history contract. B is required before the acceptance criterion “complete history over 10,000 events with no loss” can be marked true; D is required before enabling the unique index on a database containing duplicates. Do not merge a fixed-limit-only implementation as complete.

### Rollout and rollback

Roll out the cursor response fields first; old clients remain compatible. Deploy read-side event collection support behind a feature flag, dual-write new events, and monitor write failures, buffer drops, document sizes, duplicate counts, cursor page latency, and `history_complete=false`. Backfill in dry-run/verified batches, pause when active-writer conflicts or checksum mismatches appear, then switch reads per session or globally after coverage verification. Keep legacy arrays for rollback until a retention window passes. Rollback is a flag reversal to legacy reads (which may still be bounded/incomplete), restoration from migration backups for duplicate repair, and disabling the new unique index only under an explicit incident procedure; never delete backups automatically.

## Caveats / Not Found

- No live MongoDB inspection, index creation, migration, or production write was performed; duplicate counts and actual document sizes remain unknown.
- MongoDB's standard 16 MB BSON document limit is assumed; exact event payload distribution must be measured in a staging export before selecting segment size.
- The current repository has no established migration framework (`.trellis/spec/backend/database-guidelines.md` states schema evolution is application-level), so the admin command and audit/backup collection need an explicit owner and deployment procedure.
- `EventMerger` and analytics consumers may read array-backed `traces`; they must be audited in Child B before removing arrays as a source of truth.
- Existing worktree edits unrelated to this design were not changed.
