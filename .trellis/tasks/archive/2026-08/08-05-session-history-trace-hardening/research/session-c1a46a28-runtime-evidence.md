# Research: Persisted/runtime evidence for session c1a46a28

- Query: Read-only inspection of all persisted traces, legacy event arrays, immutable event storage, Redis streams/keys, and local artifacts for session `c1a46a28-931d-4d9e-8a7c-1b458413505d`.
- Scope: mixed (internal code/specs plus local MongoDB/Redis runtime evidence)
- Date: 2026-08-07

## Findings

### Runtime configuration and reachability

- The effective application settings are MongoDB `mongodb://localhost:27017`, database `agent_state`, collections `traces` and `trace_events`; Redis `redis://localhost:6379/0`. No credentials were printed; the relevant environment variables were unset and the imported settings reported empty username/password values.
- Both services were reachable read-only (`MongoDB ping: ok`; `Redis PING: true`). The database contained 37 collections, including `traces`, but did not list `trace_events`.
- Effective trace modes are `TRACE_EVENT_WRITE_MODE=legacy`, `TRACE_EVENT_READ_MODE=legacy`, `TRACE_EVENT_BACKFILL_ENABLED=false`; effective `SSE_CACHE_TTL=3600` seconds.
- The repository root has no `.env*` file, and `rg` found no local source/log/artifact containing the target session ID. No secrets or event payloads were printed.

### MongoDB traces for the target session

There are exactly two `traces` documents for the session. Both use the same trace and run identities:

`trace_20260804114855614862_ba30f047046146648b3a11e8c192f281` / `run_20260804114855_99fd0a28`.

| status | event_count | retained `events` length | seq evidence | event_id evidence | timestamps |
|---|---:|---:|---|---|---|
| `completed` | 6,215 | 6,215 | 6,215 numeric values, min 1/max 6,215, all unique and contiguous (no gaps) | all 6,215 legacy events lack `event_id` | started `2026-08-04T11:48:55.668`, updated/completed `2026-08-04T11:57:16.381` |
| `running` | 1 | 1 | no `seq` | event lacks `event_id` | started `2026-08-04T11:48:56.190`, updated `2026-08-04T11:57:16.381`, no completed timestamp |

- The two documents have identical `trace_id`, `session_id`, and `run_id`, but contradictory statuses and counts. This is a target-session duplicate trace group, not two independent runs.
- The session document exists and reports `task_status=completed`, `current_run_id=run_20260804114855_99fd0a28`, `completed_at=2026-08-04T11:57:16.408575+00:00`, and `is_active=true`. There is no matching `session_events_counter` document, `team_runs` document, or `shared_sessions` document for this session.
- A global duplicate preflight found 312 duplicate `trace_id` groups in the `traces` collection; the target group is one of them. The current `traces` indexes do not include a unique `trace_id` index.

### Immutable `trace_events` evidence

- The configured `trace_events` collection is absent from `agent_state`; querying it yields an estimated count of zero and no indexes.
- Consequently, there are no immutable trace/run counts, `event_id` ranges, `seq` ranges, duplicate/missing immutable IDs, or immutable timestamps to compare for this session.
- Legacy-vs-immutable comparison for the completed trace is therefore 6,215 retained legacy events versus 0 immutable events; overlap is unobservable/zero because no immutable IDs exist. The running duplicate is 1 legacy event versus 0 immutable events.
- The completed legacy array is internally contiguous by `seq`, but every event lacks stable `event_id`, so immutable-id deduplication/reconciliation cannot be performed from current data.

### Redis stream/key evidence

- `SCAN MATCH session:c1a46a28-931d-4d9e-8a7c-1b458413505d:*` returned zero keys. Therefore no stream length, first/last Redis stream ID, Redis `event_id`/`seq` range, duplicate pattern, or TTL is currently observable; TTL is not available for a missing key.
- The expected stream naming pattern is `session:<session_id>:run:<run_id>:events` (or `session:<session_id>:events` without a run). The writer refreshes streams using `SSE_CACHE_TTL`; with an effective 3,600-second TTL and the last persisted activity on 2026-08-04, the missing stream is consistent with normal cache expiry, but the exact deletion/eviction cause cannot be proven from the current absence.

### Can the current data reconstruct UI history?

- Under the effective legacy read mode, the completed legacy trace contains 6,215 retained events and a complete-looking contiguous sequence, so a normal `completed_only` history read can likely reconstruct those 6,215 retained events from MongoDB.
- It cannot establish authoritative completeness: there is no immutable event source, all legacy events lack `event_id`, and the duplicate `running` metadata document remains. Redis cannot supply a replay fallback because its stream key is gone.
- The legacy cursor response deliberately returns `history_complete=false` even when its retained array ends; callers should treat this history as “retained events available, completeness unproven,” not as an authenticated complete history. A UI path that relies on stable event identities may also be unable to deduplicate/merge this legacy data safely.

### Deletion vs temporary invisibility

- The 6,215-event completed legacy array is still physically present in MongoDB, so the target history was not deleted from the retained legacy store.
- Redis visibility is currently absent/expired rather than evidence that MongoDB data was deleted. The immutable store has no collection/documents, so there is no evidence of an immutable copy being deleted; it was not persisted under the effective legacy mode (or is unavailable in this database).
- The contradictory running duplicate and global duplicate population are integrity issues requiring explicit migration/recovery; no read path or research query modified them.

## Code patterns

- Effective defaults and collection/mode names are defined in `src/kernel/config/base.py:68-71,154,167,180-186`.
- Redis stream keys are built as `session:<session_id>:run:<run_id>:events` or `session:<session_id>:events` in `src/infra/session/dual_writer.py:235-238`; stream TTL is checked/refreshed through `SSE_CACHE_TTL` in `src/infra/session/dual_writer.py:632-641`.
- The immutable collection is selected lazily as `MONGODB_TRACE_EVENTS_COLLECTION` in `src/infra/session/trace_storage.py:192-198`.
- `DualEventWriter.read_session_events` selects event-store, merge, or legacy reads according to `TRACE_EVENT_READ_MODE` in `src/infra/session/dual_writer.py:844-899`; the current effective mode therefore reads the legacy array only.
- Legacy cursor pages probe one extra item but return `history_complete=False` because retained-array exhaustion does not prove durability in `src/infra/session/trace_storage.py:1492-1552`.
- Immutable reads join event documents to terminal trace metadata and sort by sequence/event identity in `src/infra/session/trace_storage.py:244-293`; that path has no data for this session.
- The project dependency versions include `redis>=5.2.0` and `pymongo>=4.10.0` in `pyproject.toml:29-32`.

## Related specs

- `.trellis/spec/backend/trace-event-storage.md` — immutable one-event-per-document storage, stable event identity, legacy/merge/event-store modes, backfill coverage, and rollback safety.
- `.trellis/spec/backend/session-history-pagination.md` — cursor ordering, incomplete-history semantics, and read-only history GET behavior.
- `.trellis/spec/backend/trace-uniqueness-readiness.md` — duplicate preflight, fail-closed unique-index readiness, and prohibition on read-time duplicate deletion.
- `.trellis/spec/backend/trace-stale-recovery.md` — conservative handling of abandoned `running` traces outside read paths.

## Caveats / Not Found

- This was a point-in-time read-only observation on 2026-08-07. No repair, deduplication, backfill, index creation, Redis key restoration, or status update was attempted.
- Payload contents were intentionally not inspected or emitted. Event-type counts and metadata key names were treated as non-payload identity/metadata; no user text/tool input/result data is included here.
- Absence of a Redis key cannot distinguish TTL expiry from eviction/manual deletion. Absence of the `trace_events` collection cannot distinguish “never written” from a database restore/configuration mismatch, although the effective legacy write mode makes no immutable writes the expected behavior.
- The legacy event array has a contiguous `seq` range and matching `event_count`, but this alone cannot prove no events were lost before persistence or that every UI-visible source is represented.
