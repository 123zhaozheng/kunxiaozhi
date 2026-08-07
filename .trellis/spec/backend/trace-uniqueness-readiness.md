# Trace Uniqueness and Write Readiness

## Scenario: Fail-closed trace creation and event writes

### 1. Scope / Trigger

Apply this contract to every path that can create a trace document, append trace events, complete a trace, or report trace-storage readiness. It prevents the application from accepting replies whose trace history cannot be stored safely while indexes are missing, duplicates exist, or a bulk writer bypasses the canonical creation gate.

### 2. Signatures

- `TraceStorage.ensure_indexes_if_needed() -> bool`
- `TraceStorage.create_trace(trace_id, session_id, run_id=None, ...) -> bool`
- `TraceStorage.append_event(trace_id, event) -> bool`
- `TraceStorage.complete_trace(trace_id, status="completed", ...) -> bool`
- `DualEventWriter.create_trace(...)`, `append_event(...)`, `complete_trace(...)`, and buffered bulk flushes
- `GET /ready` for deployment readiness; `GET /health` remains the liveness endpoint.
- Errors: `TraceWriteUnavailableError` for unavailable uniqueness/write readiness and `TraceIdentityConflictError` for trace identity reuse.

The unique index is on the globally unique `trace_id`. Existing documents must still be validated against their `session_id` and non-empty `run_id` before an idempotent create is accepted.

### 3. Contracts

- Index initialization is awaitable, coalesced by a lock/shared task, observable per index, and retryable after failure.
- The unique index is created only after a duplicate-`trace_id` preflight. Any duplicate or index error keeps readiness false and trace writes fail closed.
- `create_trace` uses atomic `$setOnInsert`; a later partial create may merge missing metadata but must not overwrite existing values.
- An existing `trace_id` is success only when its session/run identity matches. Conflicts raise `TraceIdentityConflictError`.
- Every public writer path must await readiness. Direct append/complete, Presenter creation/completion, and buffered bulk flushes cannot bypass the gate.
- Bulk event writes use updates without `upsert`. Missing trace documents are created only through the canonical `create_trace` path.
- A failed preflight or bulk write requeues the batch and re-arms the flush event. It must propagate the failure rather than report a successful trace.
- `/ready` may retry initialization with serialized bounded backoff. Kubernetes `readinessProbe` targets `/ready`; liveness continues to target `/health`.
- Historical duplicates are never deleted by readiness checks or read requests. Child D's explicit migration is the only path that repairs them.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Index initialization succeeds and duplicate preflight is empty | Readiness true; writes may proceed |
| Any index creation fails | Readiness false; record the index error; later calls may retry |
| Duplicate `trace_id` values exist | Unique index is not created; readiness and writes remain fail-closed |
| Same trace/session/run is created concurrently | One document; all compatible callers receive idempotent success |
| Existing trace belongs to another session or run | Raise `TraceIdentityConflictError`; do not append or complete |
| Presenter/storage writer is unavailable | Raise `TraceWriteUnavailableError`; do not claim successful tracing |
| Bulk preflight or write fails | Requeue in original batch order, re-arm flushing, and propagate failure |
| Readiness endpoint is probed repeatedly during failure | Serialize retry attempts and respect cooldown; return non-ready |

### 5. Good / Base / Bad Cases

- Good: await readiness, atomically create/validate the trace, then append events with non-upsert updates.
- Base: repeated creation of the same trace and identity merges only missing metadata and produces one document.
- Bad: let `$setOnInsert` in a bulk event operation create a trace, catch all creation errors and continue the run, or mark readiness true before duplicate preflight finishes.

### 6. Tests Required

- Concurrent same-identity creation produces one authoritative document.
- Session/run identity conflicts fail before event writes.
- Per-index failure is visible, keeps readiness false, and retries on a later attempt.
- Duplicate preflight blocks the unique index and every public write path.
- Direct storage, DualEventWriter, Presenter, complete-only, and buffered bulk paths all exercise the readiness gate.
- Failed preflight/bulk writes retain the batch, preserve order, re-arm flushing, and do not silently succeed.
- `/ready` concurrency/cooldown and Kubernetes readiness/liveness paths are asserted.
- Metadata merge tests prove that existing values are not overwritten.

### 7. Wrong vs Correct

#### Wrong

```python
UpdateOne(
    {"trace_id": trace_id},
    {"$setOnInsert": trace_doc, "$push": {"events": event}},
    upsert=True,
)
```

#### Correct

```python
if not await trace_storage.ensure_indexes_if_needed():
    raise TraceWriteUnavailableError("trace indexes are not ready")
await trace_storage.create_trace(trace_id, session_id, run_id=run_id)
UpdateOne({"trace_id": trace_id}, {"$push": {"events": event}}, upsert=False)
```
