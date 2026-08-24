# Research: Historical Trace Document Write Paths

- Query: Which pre-`dd15687e` code paths could create duplicate MongoDB documents for one `trace_id`?
- Scope: internal Git history and source inspection
- Date: 2026-08-11

## Findings

### Executive conclusion

The duplicate-producing mechanism is confirmed at the code level, with two independent variants:

1. **Immediate user-message pre-write followed by worker creation** (most likely): commit `dfdd45f0` added a Presenter that creates a trace before dispatch, while the executor/ARQ worker creates a new Presenter with the same `existing_trace_id` and calls `_ensure_trace` again. Before `dd15687e`, `TraceStorage.create_trace` used plain `insert_one`. If `trace_id_unique_idx` was absent (or had failed creation), every such request could produce two documents; ARQ retries could produce more.
2. **Cross-process buffered upsert race**: historical `DualEventWriter` flushed with `update_one(..., upsert=True, $setOnInsert)`. Its `asyncio.Lock` was process-local. Concurrent flushes in API/worker processes could both observe no matching document and insert when no unique index existed.

The unique index was intended from the initial trace implementation, but readiness was not fail-closed. In the pre-`dd15687e` code, `ensure_indexes_if_needed()` launched `_ensure_indexes()` as a background task and returned immediately (`trace_storage.py:156-163`); the startup caller awaited this non-awaiting method (`api/main.py:336-342`). If `_collection` had not already been loaded, `_ensure_indexes()` returned at `trace_storage.py:165-168` without creating any index. Even when started, all index errors were logged as non-critical and ignored (`trace_storage.py:169-211`). A duplicate created during the race made the later unique-index build fail, leaving the collection non-unique and allowing subsequent duplicates.

### Path A: direct `TraceStorage.create_trace` insert

- Initial implementation `73d75fa4` and the pre-fix tree (`dd15687e^`) construct a complete trace document and call `await self.collection.insert_one(doc)` (`trace_storage.py:229-287; especially 269-274`). There is no find-then-insert check and no process-shared lock.
- Duplicate-key handling (`trace_storage.py:275-281`) only converts a Mongo `DuplicateKeyError` into idempotent success. It prevents no duplicate when the unique index is missing.
- The unique index was declared in the original implementation (`73d75fa4`, `trace_storage.py:79-83`) and retained in pre-fix `_ensure_indexes()` (`dd15687e^`, `trace_storage.py:184-190`), but creation was asynchronous/non-blocking as described above.

**Sequence (confirmed mechanism; occurrence requires missing/failed unique index):**

1. Request A constructs a Presenter and calls `_ensure_trace`; `create_trace` inserts document D1.
2. A second Presenter for the same run/ID calls `_ensure_trace`; plain `insert_one` inserts D2 because Mongo has no unique constraint.
3. Both documents have the same `trace_id`. Subsequent historical `update_one` calls match an arbitrary one of the duplicate documents (Mongo's default is a single match, not a multi-update), so event/status history can diverge between the duplicate source documents rather than being reliably merged.

With a working unique index, step 2 raises `DuplicateKeyError`, is caught by `create_trace`, and no duplicate document is created.

### Path B: API/queued immediate pre-write + executor/worker repeated insert

This is the most concrete historical path.

- `dfdd45f0` added `BackgroundTaskManager._persist_initial_user_message`: it constructs a Presenter with the caller's trace ID, calls `await presenter._ensure_trace()`, then emits the user message (`manager.py:116-147`; blame attributes these lines to `dfdd45f0`).
- The same commit passes `existing_trace_id=trace_id` to `TaskExecutor.run_task` (`manager.py:318-340`).
- `TaskExecutor.run_task` constructs a **new** Presenter with `trace_id=existing_trace_id` and then calls `await presenter._ensure_trace()` (`executor.py:95-129`; `existing_trace_id` and reuse assignment are from `10c10628`). The new Presenter has `_trace_created=False`, so it invokes `create_trace` again.
- The chat API always generated a trace ID before dispatch (`chat.py:420-437`, blame `10c10628`) and passed `write_user_message_immediately=True` to both local and ARQ submissions (`chat.py:552-598`, `dfdd45f0`). WeCom also passes this flag (`wecom/handler.py:901-912`).
- The queued path similarly creates a Presenter and calls `_ensure_trace` before the worker is dequeued (`chat.py:490-523`), while the queued worker receives the same trace ID through Redis and repeats `_ensure_trace` via `arq_worker.py:89-110`.

**Reproducible sequence:**

1. API generates `trace_id=T` and stores it in task context.
2. Immediate pre-write Presenter calls `TraceStorage.create_trace(T, ...)` -> D1.
3. Local executor or ARQ worker receives `existing_trace_id=T`, builds a fresh Presenter, and calls `create_trace(T, ...)` -> D2.
4. If the worker raises after creation, `arq_worker.py:137-138` retains the payload for retry; the retry repeats step 3 and can create D3, D4, etc. when uniqueness is absent.

Classification: **confirmed code-level duplicate mechanism; production frequency depends on unique-index readiness and use of immediate pre-write (which the chat/WeCom paths enabled by default).** This mechanism predicts one duplicate group per affected run, matching the shape of a large number of duplicate groups better than random UUID collision.

### Path C: `DualEventWriter` buffered upsert

Before `dd15687e`, both the initial writer (`73d75fa4`) and the later Redis-stream writer (`87909484`) used a grouped update with `upsert=True` and `$setOnInsert` (`dual_writer.py` initial lines 147-182; pre-fix `dd15687e^` helper `_build_mongo_bulk_operations`, lines 95-140). The operation matched only `{"trace_id": trace_id}`.

**Sequence (confirmed possible; requires concurrent processes and no unique index):**

1. API process P1 and worker process P2 buffer events for the same `trace_id` (or two worker processes flush a retried task).
2. Each performs `update_one({trace_id:T}, ..., upsert=True)` while no document is visible to the other.
3. Without a unique index, both upserts may insert separate documents D1/D2. The `$setOnInsert` fields do not make the operation globally unique.
4. The historical `_mongo_lock` protects only one `DualEventWriter` instance/process (`dual_writer.py:158-166`); it cannot serialize P1/P2.

Within one process/singleton writer, the lock serializes buffer extraction/flush and makes this race unlikely. With a working unique index, one upsert wins and the other receives a duplicate-key error (which historical flush code logged and swallowed), so no duplicate document is produced.

Classification: **plausible/confirmed race class**, secondary to Path B because it needs cross-process overlap and an absent index. It is nevertheless an independent creation path and could add extra duplicates to runs already affected by the repeated Presenter insert.

### Path D: direct append/complete, EventMerger, and event-store writes

- Historical `append_event` and `complete_trace` use `update_one` without `upsert` (`trace_storage.py:325-368` and `trace_storage.py:435-475` in `dd15687e^`), so they cannot create a missing trace document.
- `event_merger.py` uses `UpdateOne({"trace_id": ...}, {"$set": ...})` without `upsert`; it only updates existing documents (`event_merger.py:364` and historical equivalent).
- Immutable `trace_events` writes introduced by `87d98309` use a separate collection and `(session_id, trace_id, event_id)` upserts; they do not create legacy `traces` documents.

Classification: **ruled out as a source of duplicate legacy trace documents**.

### Path E: trace ID generation/collision

`PresenterConfig._generate_trace_id` has used a timestamp plus `uuid.uuid4().hex` since `2c0501dd` (`presenter_config.py:44-47`). Accidental random collisions are therefore not a credible explanation. Reuse is explicit in the pre-write/worker handoff (`existing_trace_id`) and is the relevant identity behavior.

Classification: **ruled out for random collision; explicit reuse is the trigger in Path B**.

### Commit timeline and behavior changes

- `73d75fa4` (2026-02-27): introduced plain `insert_one` trace creation, asynchronous unique-index creation, and buffered `upsert=True` event writes.
- `fdcdfc6e` (2026-03-16): moved trace index initialization behind `ensure_indexes_if_needed()` but retained fire-and-forget task semantics (the change briefly scheduled two background tasks). This made startup ordering/index absence possible: when startup called it before the collection getter, both tasks returned without doing work, while the method still returned before index creation.
- `10c10628` (2026-03-25): added early trace ID generation and executor reuse (`existing_trace_id`).
- `dfdd45f0` (2026-05-26): added immediate initial user-message persistence and passed the same trace ID into the executor, creating the deterministic repeated `insert_one` sequence.
- `87d98309` (2026-08-07 10:35, after `dd15687e`): added immutable event storage; not a legacy trace-creation fix.
- `c7d95012` (2026-08-07): added explicit duplicate migration/rollback; it repairs historical data and creates no trace documents.
- `dd15687e` (2026-08-07 10:08): fixed the write bypass. It makes readiness awaitable/fail-closed, performs duplicate preflight, changes `create_trace` to atomic `$setOnInsert`, and changes buffered legacy updates to `upsert=False` after canonical creation/identity validation. This is the first commit that closes both Path B and Path C for production writes.

## Most-likely ranking

1. **Index-readiness failure (confirmed strongest enabling condition):** `fdcdfc6e` moved initialization behind a fire-and-forget method that can run while `_collection is None`; startup then marks initialization attempted without creating indexes. Later duplicate inserts leave the unique-index build failing permanently because errors were non-critical.
2. **Path B (confirmed, highest creation pressure):** immediate pre-write + executor/ARQ Presenter repeated plain insert; enabled on normal chat and WeCom flows. Expected shape: generally two source documents per trace ID, with more only after worker retries.
3. **Path C (plausible, secondary):** cross-worker `upsert=True` race under the same missing-index condition; can create duplicates even without the pre-write handoff.
4. **Random UUID collision or append/complete/event-merger paths:** ruled out / not document-creating.

## Safe read-only discriminators

- Group `traces` by `trace_id`, count documents, and compare `session_id`, `run_id`, `started_at`, and event counts. Near-uniform two-document groups with matching identity support Path B; differing identities or event batches support cross-worker upsert races.
- Compare duplicate documents' `_id` creation times (if retained) and event distribution. One empty/near-empty document plus one event-bearing document is characteristic of pre-write then worker insert.
- Inspect `system.indexes`/`listIndexes` for `trace_id_unique_idx` and its build/error history. Absence plus historical startup logs showing only “TraceStorage initialized” is consistent with the non-awaiting initializer.
- Use migration dry-run only; it reports exact duplicate groups without mutating data.

## Caveats / Not Found

- No production MongoDB data or index catalog was available, so the report proves code mechanisms and required conditions, not which individual 424 groups followed which sequence.
- MongoDB's unique-index behavior is the required external database condition: when present, duplicate inserts/upserts fail with `DuplicateKeyError`; when absent, concurrent upserts/inserts can create multiple documents.
- No other pre-`dd15687e` legacy trace-document insert/upsert path was found outside `TraceStorage.create_trace` and `DualEventWriter`'s buffered upsert. `EventMerger`, append, complete, and immutable event-store operations are updates only.
