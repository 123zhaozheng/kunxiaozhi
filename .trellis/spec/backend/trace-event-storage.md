# Durable Trace Event Storage

## Scenario: Immutable event persistence and legacy rollout

### 1. Scope / Trigger

Use this contract for every trace event write, history read, backfill, or rollout that crosses the legacy `traces.events` array and the immutable `trace_events` collection. The separate collection removes the Mongo 16 MB single-document boundary, fixed `$slice` truncation, and silent in-memory buffer eviction from the authoritative path.

### 2. Signatures

- Configuration:
  - `TRACE_EVENT_WRITE_MODE=legacy|dual|event_store` (default `legacy`)
  - `TRACE_EVENT_READ_MODE=legacy|merge|event_store` (default `legacy`)
  - `TRACE_EVENT_BACKFILL_ENABLED: bool` (default `false`)
  - `MONGODB_TRACE_EVENTS_COLLECTION` (default `trace_events`)
- Storage: `TraceStorage.write_trace_events(events: list[dict])`
- Storage: immutable read/page helpers and `TraceStorage.backfill_legacy_events(..., dry_run=True)`.
- Writer: `DualEventWriter.write_event(session_id, event_type, data, trace_id=None, agent_id=None, run_id=None, event_id=None) -> bool` accepts or creates the stable identity propagated to Redis, legacy arrays, and `trace_events`.
- Compactor: `EventMerger._merge_group(group) -> dict` emits one retained representative at the first source row's ordering position.

Each immutable document contains at least `session_id`, `trace_id`, `run_id`, `event_id`, `seq`, `event_type`, `timestamp`, and `data`.

### 3. Contracts

- The unique key is `(session_id, trace_id, event_id)`. Retrying the same event uses `$setOnInsert` and produces one immutable document.
- Query indexes cover session cursor order and trace/run reads. Index readiness is fail-closed before event-store writes.
- Sequence allocation and `event_id` generation occur before buffering. The same identity is used by Redis, legacy dual-write, immutable storage, merge reads, and backfill.
- `legacy` write mode retains the old array path for rollback. `dual` writes both stores. `event_store` treats immutable documents as authoritative and does not append or rewrite legacy arrays.
- `legacy` read mode uses retained arrays. `merge` merges both stores by stable event identity and deterministic order. `event_store` reads only immutable documents.
- `completed_only` applies in every read mode. Immutable reads join trace metadata and exclude unknown/running trace identities fail-closed.
- Authoritative modes do not use `$slice`, whole-array token-event rewrites, buffer eviction, or upserting event updates into trace metadata documents.
- Buffer pressure applies backpressure. Preflight/write failure requeues events in order, re-arms flushing, raises/logs visibly, and never discards a batch.
- Metadata counts are repairable projections. Event durability happens first; metadata failure cannot delete immutable events.
- Backfill is source-preserving and dry-run capable. Deterministic legacy IDs must match merge fallback IDs. If `event_count` exceeds the retained array length, coverage is incomplete and cutover must be blocked.
- Merge fallback IDs use the event's ordinal within its source trace array (not the flattened session ordinal), so a backfilled legacy event replaces rather than duplicates the legacy read.
- Multi-row `thinking` and `message:chunk` compaction preserves the first source row's available top-level `seq`, `event_id`, `id`, `trace_id`, and `run_id`. The merged payload may change content/metadata, but must remain anchored to the first occurrence for v2 ordering and cross-store identity.
- A merger-marked retained array that predates identity preservation and contains missing/non-numeric `seq` is read through the scoped v3 history compatibility contract in `session-history-pagination.md`; do not rewrite it during a GET.
- Rollback changes read/write modes only; it never deletes `trace_events`.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Unsupported write/read mode | Settings validation fails at startup |
| Unsafe Mongo collection name | Settings validation fails at startup |
| Duplicate event retry | Idempotent success; no second document or metadata over-count |
| Unknown/running trace with `completed_only=true` | Exclude immutable events |
| Buffer is full | Wait/backpressure; never evict old events |
| Index/preflight/bulk write fails | Requeue in order and propagate/alert; do not report persistence success |
| Legacy array is already truncated during backfill | Report incomplete coverage and block cutover |
| Multi-row merger input has ordering/identity fields on its first event | Copy every available field to the representative event; do not regenerate identity |
| Pre-fix merged array has missing/non-numeric `seq` | Keep storage read-only and select scoped v3 history ordering |
| Read mode changes back to `legacy` | Serve legacy data without deleting immutable events |

### 5. Good / Base / Bad Cases

- Good: allocate `seq` and `event_id`, enqueue once, idempotently write the immutable document, then repair metadata counts from actual upserts.
- Good: merge adjacent chunks by copying the first event, replace only merged payload/timestamp fields, and retain its sequence and identity.
- Base: dual mode writes the same identity to both stores; merge mode returns one event in deterministic order.
- Bad: construct a merged event from only `event_type`, `data`, and `timestamp`; this discards sequence/identity and makes later history ordering ambiguous.

### 6. Tests Required

- More than 10,000 events and large payloads persist without a growing single trace document or lost events.
- Same `event_id` retried within and across batches remains one document; metadata counts use actual upserts.
- Legacy/dual/event-store write modes and legacy/merge/event-store read modes have explicit coverage.
- Merge preserves total order and has no gaps/duplicates for shared IDs and deterministic legacy fallback IDs.
- Event merger tests cover multi-row `thinking` and `message:chunk` groups, preserve first-row ordering/identity fields, and keep single-row/content behavior unchanged.
- Pre-fix merger data selects scoped v3 ordering without mutation; future merger output remains eligible for normal v2 ordering.
- `completed_only`, run filters, event filters, cursor boundaries, and page continuation behave consistently across modes.
- Backpressure, preflight failure, bulk failure, requeue ordering, and subsequent retry are asserted.
- Backfill dry-run, idempotent apply, truncated-source detection, coverage reporting, and rollback are asserted.
- Configuration rejects invalid modes and unsafe collection names.

### 7. Wrong vs Correct

#### Wrong

```python
{"$push": {"events": {"$each": batch, "$slice": -10000}}}
```

#### Correct

```python
await trace_storage.write_trace_events([
    {
        "session_id": session_id,
        "trace_id": trace_id,
        "event_id": event_id,
        "seq": seq,
        "event_type": event_type,
        "data": data,
        "timestamp": timestamp,
    }
])
```

For retained-array compaction, preserve the first event as the identity anchor:

```python
# Wrong: drops seq/event identity.
merged = {"event_type": event_type, "data": merged_data, "timestamp": first["timestamp"]}

# Correct: replace merged content while retaining first-row ordering fields.
merged = {**first, "event_type": event_type, "data": merged_data, "timestamp": first["timestamp"]}
```
