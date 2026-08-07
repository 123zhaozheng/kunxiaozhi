# Duplicate Trace Migration

## Scenario: Explicit repair of historical duplicate trace documents

### 1. Scope / Trigger

Use this contract only for the explicit maintenance operation that repairs historical duplicate trace documents. Duplicate cleanup must never run from history GETs, readiness probes, application startup, or ordinary writes. The operation is destructive in apply mode, so dry-run, backup, audit, verification, leases, and rollback are mandatory.

### 2. Signatures

- CLI: `python -m src.infra.session.trace_migration` defaults to dry-run.
- Apply requires both `--apply` and `--confirm`; rollback uses `--rollback <operation-id>` and cannot be combined with apply flags.
- Admin API exposes dry-run/apply and `POST .../{operation_id}/rollback` behind administrative permission checks.
- Service operations: plan, apply, and rollback return a stable `DuplicateTraceMigrationResult` containing `operation_id`, `dry_run`, counts, readiness state, and errors.
- Duplicate identity is strictly `(session_id, trace_id)`. Source deletion always uses exact backed-up `_id` values.

### 3. Contracts

- Dry-run performs zero writes and returns the exact duplicate groups, active skips, merge counts/checksums, and global uniqueness-readiness result.
- Apply requires explicit confirmation, validates the operation ID, and rejects reuse with a different session scope.
- Each group holds a maintenance lease across re-read, backup, merge, verification, and deletion. Lease ownership and expiry are rechecked before every destructive phase.
- Active traces/heartbeats are skipped by default. Apply and rollback do not overwrite a currently active writer.
- All source documents are backed up and the audit/operation state is persisted before any mutation.
- Canonical metadata selection and event union are deterministic. Event identity matches immutable `trace_events`/legacy-backfill semantics; ordering uses legacy bucket, seq, timestamp, trace/event identity, source, and ordinal tie-breakers.
- The merged document is verified by full checksum and logical event count. A final source re-read detects non-cooperating writers before exact-ID deletion.
- Deletion count must equal the expected exact source-ID count. Partial or ambiguous deletion is failure.
- Operation markers make apply and rollback idempotent. Rollback reacquires group leases and restores all backed-up source documents within their exact session/trace scope.
- Unique-index readiness is a global assertion even when a migration plan filters one session. Readiness becomes safe only when no duplicate group remains globally.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| No apply flag | Dry-run; zero database mutations |
| `--apply` without `--confirm` | Reject before construction/mutation |
| Empty/unsafe operation ID | Reject with controlled validation error |
| Reused operation ID with different scope | Reject; do not reuse prior result |
| Active writer/heartbeat | Skip or fail safely; never mutate that group |
| Lease lost or expired | Abort before the next destructive phase |
| Backup/audit write fails | Abort with source documents untouched |
| Re-read/checksum/count mismatch | Abort before deletion |
| Exact deletion count mismatch | Mark failed and preserve rollback evidence |
| Rollback while writer is active | Refuse restoration |
| Session-scoped plan leaves duplicates elsewhere | Global readiness remains false |

### 5. Good / Base / Bad Cases

- Good: dry-run, inspect the global plan, apply with confirmation and operation ID, back up, verify, delete exact IDs, then prove duplicate preflight is globally empty.
- Base: rerunning a completed operation returns the persisted idempotent result; repeated rollback does not invent new source state.
- Bad: keep the document with the largest `event_count`, delete by `trace_id` without `session_id`, infer safety from one session, or restore backups without a lease/activity check.

### 6. Tests Required

- Dry-run writes nothing and reports accurate global readiness.
- Deterministic canonical selection, unique-event union, legacy IDs, and total ordering.
- Same trace ID in different sessions is never merged or deleted together.
- Active writer, heartbeat, lease expiry, re-read race, backup failure, checksum/count mismatch, and deletion-count mismatch.
- Apply requires confirmation and validates operation ID/scope.
- Mid-operation rerun is idempotent; applied operations rollback all backed-up sources; rollback checks leases/activity and is repeat-safe.
- Successful migration leaves zero duplicate groups and permits the unique index readiness gate.
- Admin permission and CLI unsafe-flag combinations are rejected.

### 7. Wrong vs Correct

#### Wrong

```python
await traces.delete_many({"trace_id": trace_id, "_id": {"$ne": keep_id}})
```

#### Correct

```python
await backup_all_sources(operation_id, source_docs)
assert verify_checksum_and_count(merged, source_docs)
await assert_lease_owned_and_unexpired(session_id, trace_id)
await traces.delete_many({
    "session_id": session_id,
    "trace_id": trace_id,
    "_id": {"$in": exact_source_ids},
})
```
