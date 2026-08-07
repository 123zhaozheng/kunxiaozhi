# Stale Running Trace Recovery

## Scenario: Conservative lifecycle repair outside read paths

### 1. Scope / Trigger

Use this contract for startup or explicitly scheduled maintenance that repairs genuinely abandoned `running` trace documents. History/session/share GET requests remain strictly read-only and must never invoke lifecycle repair. Recovery is conservative because a false terminal transition can hide a live response from streaming and history consumers.

### 2. Signatures

- `TraceStorage.reconcile_stale_running_traces(...) -> int`
- `TaskHeartbeat.check_exists_strict(...)` for recovery; ordinary callers may keep the legacy best-effort heartbeat API.
- Startup cleanup receives an optional reconciliation callback under its existing cleanup lease.
- Configuration:
  - `TRACE_STALE_RECOVERY_ENABLED=true`
  - `TRACE_STALE_RECOVERY_GRACE_SECONDS=120` (`> 0`)
  - `TRACE_STALE_RECOVERY_BATCH_SIZE=100` (`> 0`)

Recovery exposes counters through `TraceStorage.stale_recovery_metrics` and emits reasoned success/failure logs.

### 3. Contracts

- Candidate selection is bounded and includes only `status=running` traces older than the grace cutoff.
- Decision evidence combines session task status, current run identity, strict Redis/task heartbeat, and terminal-event evidence.
- A live heartbeat or an active/current run always blocks reconciliation, even when a done/error child event exists.
- Heartbeat lookup failure is unknown, not expired. Recovery fails closed and leaves the trace running.
- Terminal-event evidence alone is insufficient when task/current-run/heartbeat evidence says active.
- The target terminal status preserves failure semantics: failed, cancelled, expired, or error-event traces become `error`; successful terminal evidence becomes `completed`.
- Update uses CAS requiring the exact trace identity (`_id` when available), expected session/trace identity, and `status=running`; a concurrent writer wins safely.
- Successful repair records `reconciled_at`, `reconciled_reason`, and the observed task/run/heartbeat/event state.
- Recovery runs under startup cleanup leasing and can be disabled without affecting normal trace writes or reads. Startup-only invocation is permitted; adding a scheduler must reuse the same method and lease/decision contract.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Recovery disabled | No scan or mutation |
| Grace or batch configuration is zero/negative | Settings validation fails |
| Trace newer than grace cutoff | Not a candidate |
| Live heartbeat | Keep `running` |
| Heartbeat lookup throws/fails | Fail closed; keep `running`; log/count failure |
| Session task/current run is active | Keep `running` regardless of child events |
| Conservative terminal evidence, no live heartbeat | Attempt CAS to `completed` or `error` |
| Writer changes status/identity before CAS | CAS modifies zero documents; do not claim recovery |
| One candidate lookup/update fails | Log/count failure and continue bounded processing safely |

### 5. Good / Base / Bad Cases

- Good: startup cleanup acquires its lease, scans old running candidates, checks strict heartbeat and authoritative task state, then applies an identity-scoped CAS with audit fields.
- Base: no candidates or recovery disabled returns zero without side effects.
- Bad: reconcile from GET, infer stale from a done event alone, treat Redis errors as missing heartbeat, update all running traces by session, or always mark terminal traces completed.

### 6. Tests Required

- GET history/session/share paths perform no lifecycle writes.
- Grace cutoff, bounded batch, disabled mode, and invalid configuration.
- Live heartbeat, heartbeat miss, and heartbeat lookup failure.
- Active task/current run with done/error events remains running.
- Successful terminal evidence becomes completed; failure/cancel/expire/error evidence becomes error.
- CAS includes identity/status and loses safely to a concurrent writer.
- Audit fields, metrics, error logging, startup callback wiring, and cleanup lease behavior.

### 7. Wrong vs Correct

#### Wrong

```python
await traces.update_many(
    {"status": "running", "events.event_type": {"$in": ["done", "error"]}},
    {"$set": {"status": "completed"}},
)
```

#### Correct

```python
if await heartbeat.check_exists_strict(session_id, run_id):
    return False
await traces.update_one(
    {"_id": trace_id, "status": "running", "session_id": session_id},
    {"$set": {"status": terminal_status, "reconciled_at": now, **audit}},
)
```
