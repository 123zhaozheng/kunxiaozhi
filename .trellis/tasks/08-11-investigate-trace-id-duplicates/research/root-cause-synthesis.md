# Historical `trace_id` Duplicate Root-Cause Synthesis

Date: 2026-08-11

## Executive conclusion

The duplicate documents were not caused by random UUID collisions. Historical code intentionally propagated one generated `trace_id` through more than one `Presenter` instance. Starting with commit `dfdd45f0` (2026-05-26), a normal chat request could create the same logical trace twice:

1. The API/task-manager presenter persisted the user message immediately and called `create_trace`.
2. The local or ARQ worker received the same `trace_id`, constructed another presenter, and called `create_trace` again.

That repeated create was expected to be idempotent, but the old implementation used `insert_one` and depended entirely on a MongoDB unique index for idempotency. The startup initializer normally ran before the lazy trace collection had been loaded: it marked indexes as ensured, scheduled a background task, and that task returned immediately because `_collection is None`. The collection getter did not retrigger initialization. Index failures were also logged as non-critical and never retried. Therefore the unique index could remain absent for the whole process lifetime; both inserts then succeeded. Once even one duplicate existed, any later unique-index build also failed, leaving all subsequent runs exposed. This creates a self-reinforcing failure mode that explains hundreds of groups.

The historical runtime sample from 2026-08-04 exactly matches this mechanism: two documents had the same `trace_id`, `session_id`, and `run_id`; the first was a large completed trace and the second was a running one-event shell created about 522 ms later.

## Evidence timeline

### Original storage behavior

The repository's initial trace storage already had a `trace_id_unique_idx`, but initialization was not a readiness boundary:

- Startup called `ensure_indexes_if_needed()` on a newly created singleton whose lazy `_collection` was normally still `None`.
- `ensure_indexes_if_needed()` set the process-local `_indexes_ensured` flag before scheduling `_ensure_indexes()` with `asyncio.create_task`, then returned without awaiting index completion.
- `_ensure_indexes()` immediately returned when `_collection is None`; the later collection getter did not call index initialization again. The already-set flag also prevented a retry.
- `_ensure_indexes()` created two regular indexes before attempting the unique trace index.
- One broad `except` logged `Failed to create indexes (non-critical)` and left `_indexes_ensured` true, so the process did not retry.
- `TraceStorage.create_trace()` used `insert_one` and interpreted `DuplicateKeyError` as idempotent success. Without the unique index, no `DuplicateKeyError` occurred.

This behavior is visible in `dd15687e^:src/infra/session/trace_storage.py` and was replaced by commit `dd15687e` on 2026-08-07.

### Repeated creation became a normal request path

Commit `dfdd45f0` added immediate user-message persistence before background execution:

- `chat_stream` passed one generated `trace_id` and `write_user_message_immediately=True` into task submission.
- `BackgroundTaskManager._persist_initial_user_message()` built presenter A, called `_ensure_trace()`, and wrote `user:message`.
- `TaskExecutor.run_task()` built presenter B with `existing_trace_id` and unconditionally called `_ensure_trace()` again, even when `user_message_written=True` suppressed a second user-message event.
- The ARQ payload also preserved the same `trace_id`, so API and worker could perform these calls in different processes.

The per-presenter `_trace_created` flag prevented repeats only inside one presenter instance. It did not deduplicate across the API presenter and worker presenter.

### A second creation bypass existed

Before `dd15687e`, buffered event flushes used:

```python
UpdateOne(
    {"trace_id": trace_id},
    {"$push": ..., "$setOnInsert": {"session_id": ..., "run_id": ..., "status": "running"}},
    upsert=True,
)
```

This allowed an event flush to create a partial trace document without going through `TraceStorage.create_trace`. It was a real bypass and a plausible contributor during retries, create failures, or unusual ordering. It is not needed to explain the known large-document-plus-shell sample, because the API and worker already performed two direct inserts.

Commit `dd15687e` changed bulk event updates to `upsert=False`, pre-created and identity-validated each trace, made index initialization awaitable/retryable, and blocked writes while uniqueness was not ready.

## Exact failure sequence

The primary sequence requires only one request while the unique index is absent; the startup/lazy-loading defect made that a normal old-version state rather than merely a narrow timing window:

1. The route creates a `trace_id` as timestamp plus a full UUID4.
2. The API task manager passes that exact ID to presenter A.
3. Presenter A calls `insert_one`, creating document A, then buffers the initial `user:message`.
4. The worker receives the same ID and constructs presenter B.
5. Presenter B calls `insert_one`, creating document B because no unique index rejects it.
6. Buffered `UpdateOne({"trace_id": ...})` operations update only one matching document, normally the first inserted document A, so A accumulates the run's events.
7. On completion, `_ensure_token_usage_event` searches for a matching document lacking `token:usage`. If A already contains real token usage, document B is the matching shell and receives one synthetic zero-usage event.
8. `complete_trace` then performs a separate `update_one({"trace_id": ...})` without identity by `_id`; this normally updates document A, leaving B as `running`.

Expected result:

- A: earlier `started_at`, `completed`, thousands of events, sequenced data.
- B: slightly later `started_at`, `running`, exactly one unsequenced `token:usage` event.

Observed 2026-08-04 result:

- Same trace/run identity on both documents.
- A: `completed`, 6,215 events with contiguous `seq` 1-6,215.
- B: `running`, one event without `seq`.
- B started 522 ms after A.
- No unique trace index existed.

This is a field-for-field match, making repeated API/worker insertion the highest-confidence root cause.

## Cause classification

| Candidate | Classification | Reason |
|---|---|---|
| API pre-write presenter plus worker presenter both call `insert_one` | Confirmed mechanism; highest-confidence production cause | Concrete code path and exact match to the inspected duplicate pair |
| Startup/lazy-loading index initialization defect | Confirmed primary enabling defect | Startup could permanently mark indexes ensured while `_collection is None`, create no index, and never retry |
| Fire-and-forget/non-retryable index errors | Confirmed additional enabling defect | Requests could write before readiness; later build failures were swallowed and not retried |
| Existing duplicate prevents later unique-index creation | Confirmed amplification loop | MongoDB cannot create a unique index over duplicates, so protection never recovers automatically |
| Buffered event `upsert=True` creates a shell | Confirmed historical bypass; plausible contributor | Code could create trace documents outside the canonical creation path |
| ARQ retry/recovery repeats the same logical create | Plausible amplifier | Payloads preserve `trace_id`; every new presenter retried `create_trace` while the index was absent |
| Multiple application processes | Plausible amplifier, not root cause | Process-local flags/locks cannot enforce uniqueness, but Mongo's missing unique index is the decisive condition |
| UUID4 collision | Ruled out for known samples | Duplicate documents share the intentionally propagated trace/run/session identity; timestamp plus 128-bit UUID4 collision is not credible |
| Two unrelated sessions happened to generate the same ID | Unlikely and not seen in inspected sample | The inspected duplicate pair shares both session and run; production aggregation should verify all groups |

## Why the count reached hundreds

The old system had a latch-like failure:

1. The startup/lazy-collection bug, a startup race, a transient index error, or a pre-existing duplicate leaves the unique index absent.
2. Immediate-message persistence makes two inserts for each normal run.
3. Those duplicates ensure every future attempt to build the unique index fails.
4. The old code treats that failure as non-critical and continues writing.

Therefore the first duplicate is the hard transition; accumulation afterward is expected rather than rare.

The current preflight's `duplicates=424` means 424 distinct duplicated `trace_id` groups, not 424 excess documents. Each group contains at least two documents. `_find_duplicate_trace_ids` limits returned groups to 1,000; because 424 is below the cap, it is normally the exact observed group count. An uncapped count query below can confirm it.

The separate 312-group observation on 2026-08-07 came from a local MongoDB instance. It must not be subtracted from 424 unless deployment/database identity is independently confirmed.

## Safe read-only production checks

### Exact duplicate-group count

```javascript
db.traces.aggregate([
  {$match: {trace_id: {$exists: true, $ne: null}}},
  {$group: {_id: "$trace_id", documents: {$sum: 1}}},
  {$match: {documents: {$gt: 1}}},
  {$count: "duplicate_trace_id_groups"}
], {allowDiskUse: true})
```

### Metadata-only duplicate shape

This query does not return event payloads:

```javascript
db.traces.aggregate([
  {$match: {trace_id: {$exists: true, $ne: null}}},
  {$project: {
    trace_id: 1,
    session_id: 1,
    run_id: 1,
    status: 1,
    event_count: {$ifNull: ["$event_count", 0]},
    retained_events: {$size: {$ifNull: ["$events", []]}},
    has_token_usage: {$in: ["token:usage", {$ifNull: ["$events.event_type", []]}},
    started_at: 1,
    updated_at: 1,
    completed_at: 1
  }},
  {$group: {
    _id: "$trace_id",
    documents: {$sum: 1},
    session_ids: {$addToSet: "$session_id"},
    run_ids: {$addToSet: "$run_id"},
    docs: {$push: "$$ROOT"}
  }},
  {$match: {documents: {$gt: 1}}},
  {$sort: {documents: -1}}
], {allowDiskUse: true})
```

Interpretation:

- One session ID and one run ID per group rules out random ID collision and confirms repeated creation of one logical run.
- Earlier completed/high-count plus later running/0-or-1-count is the API-presenter/worker-presenter signature.
- A one-event shell whose only event type is `token:usage` is the strongest match to the completion sequence above.
- Different session or run identities require separate investigation for manual ID reuse, malformed clone/import data, or a propagation bug.
- More than two documents for one group suggests retry/recovery or multiple worker attempts amplified the two-presenter pattern.

### Index state

```javascript
db.traces.getIndexes()
```

The affected state lacks a unique `trace_id_unique_idx`. Do not create it until duplicates are migrated; direct creation will fail and does not safely merge data.

## Remaining uncertainty

The repository and the inspected 2026-08-04 sample establish the mechanism, but the pasted 424-group log alone does not prove every one of those 424 groups has the same shape. Run the metadata-only aggregation or the migration CLI's default dry-run to measure the distribution before applying any migration.

## Production dry-run evidence (2026-08-11)

A complete, read-only migration plan was inspected without retaining identifiers or payloads. It reported:

- `dry_run=true` and `index_preflight_clear=false`; this invocation made no database changes.
- 345 duplicate groups and 690 source documents: every group contains exactly two documents.
- All 345 groups have `active=true` and `skip_reason=active_writer`; default apply therefore skips every remaining group.
- The plan contains no missing session/trace identities and no malformed group with fewer than two sources.
- Total planned logical merged events: 29,629; per-group retained input ranges from 6 to 10,001 events, with a median of 13.
- 343 groups have merged logical count equal to the sum of retained source arrays, consistent with split writes whose retained events can be unioned without overlap loss.
- Two groups preserve logical counters larger than retained arrays by 413 and 459 events. This is compatible with historical `$slice` retention/counter divergence; migration preserves all retained events and the logical count but cannot reconstruct payloads already truncated before migration.

If the earlier 424-group log came from the same database, some prior operation removed 79 groups; the complete dry-run itself did not do so. The remaining all-active shape means maintenance must stop every writer before an explicit `--include-active` apply. The `running` flag alone is enough for the migration to fail closed, even when it belongs to a stale historical shell.

## Sources

- `.trellis/tasks/archive/2026-08/08-05-session-history-loss-review/research/session-history-loss-review.md`
- `.trellis/tasks/archive/2026-08/08-05-session-history-trace-hardening/research/session-c1a46a28-runtime-evidence.md`
- `.trellis/spec/backend/trace-uniqueness-readiness.md`
- `.trellis/spec/backend/trace-duplicate-migration.md`
- Commit `dfdd45f0`: immediate user-message persistence and repeated presenter creation
- Commit `dd15687e`: awaited readiness, atomic creation, identity validation, and bulk-write gate
