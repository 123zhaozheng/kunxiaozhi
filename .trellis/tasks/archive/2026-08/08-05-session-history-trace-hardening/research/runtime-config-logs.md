# Research: Runtime trace modes, deployment evidence, and session c1a46a28

- Query: Inspect effective TRACE_EVENT_WRITE_MODE/READ_MODE and related local runtime/deployment settings; inspect process/container/application evidence for session `c1a46a28-931d-4d9e-8a7c-1b458413505d`, Mongo flush/index/duplicate/history errors, lifecycle recovery, and restart timing.
- Scope: mixed (local source/config, live host process, local Mongo/Redis/Docker runtime)
- Date: 2026-08-07

## Findings

### Effective mode evidence

- A fresh settings load from the repository environment resolved to:
  - `TRACE_EVENT_WRITE_MODE=legacy`
  - `TRACE_EVENT_READ_MODE=legacy`
  - `TRACE_EVENT_BACKFILL_ENABLED=false`
  - `MONGODB_TRACE_EVENTS_COLLECTION=trace_events`
  - `TRACE_STALE_RECOVERY_ENABLED=true`, grace `120`, batch `100`.
  This matches the defaults declared in `src/kernel/config/base.py:67-76`.
- No trace-mode variables are present in the repository `.env`, `.env.example`, `deploy/.env.example`, or `deploy/intranet/.env.example`; the only related local env setting is `MONGODB_TRACES_COLLECTION=traces` (`.env:68`, `.env.example:78`). The current shell environment also has no `TRACE_EVENT_*`, `MONGODB_TRACE_EVENTS_COLLECTION`, or `TRACE_STALE_*` variables.
- A read-only query of Mongo `system_settings` for all seven trace-mode/recovery keys returned no documents. The event-mode keys are also not in `SETTING_DEFINITIONS` (`src/kernel/config/definitions.py:274-294` only defines the stale-recovery keys), while `initialize_settings()` loads database values by iterating `SETTING_DEFINITIONS` (`src/kernel/config/service.py:257-301`). Therefore there is no observed database override for event write/read mode.
- The host process serving port 8000 is `python .../.venv/Scripts/python.exe main.py` (PID 22724; parent PID 4208), started at 2026-08-07 11:49:37 local time. `/api/version` reports app version `2.5.0`, commit `965c66fd`. The process environment cannot be introspected directly without invasive debugging, but command line, env files, settings DB, and source defaults all converge on legacy/legacy.
- Conclusion: the observed deployment is not truly running dual; the effective observed write mode is legacy and read mode is legacy. Read mode does not differ from write mode. This means the immutable event store is not an active authoritative or dual-read path in this runtime.

### Live readiness, Mongo collections, and target session

- `GET http://127.0.0.1:8000/ready` returned HTTP 503 with `ready=false`, `attempted=true`, `attempts=3`, and error `trace_id_unique_idx: duplicate trace_id values: 312`. The response contained 312 duplicate IDs; IDs were not copied into this artifact.
- The Mongo database used by the running local app contains `traces`, but does not contain `trace_events`; `trace_events` therefore has no indexes and no documents. The `traces` collection has no unique `trace_id` index. This is consistent with legacy-only writes and explains why readiness remains fail-closed.
- Read-only Mongo inspection of session `c1a46a28-931d-4d9e-8a7c-1b458413505d` found exactly two `traces` documents and zero `trace_events` documents. Both documents share the same `trace_id` and `run_id` identity (IDs redacted here):
  - one `status=completed`, `event_count=6215`, legacy array length 6215, `completed_at`/`updated_at` 2026-08-04 11:57:16 UTC;
  - one `status=running`, `event_count=1`, legacy array length 1, same `updated_at`, no `completed_at`.
  Neither document has `reconciled_at` or `reconciled_reason`.
- The target session document has `is_active=true`, `created_at=2026-08-04 11:48:55.633 UTC`, and `updated_at=2026-08-04 11:57:16.408 UTC`; it has no top-level task-status, current-run, or heartbeat fields. This is evidence of a persisted active session and an unreconciled running duplicate, not proof that a worker is currently alive.
- A read-only Redis scan found zero keys matching `session:c1a46a28-931d-4d9e-8a7c-1b458413505d*`; no live/replay stream evidence remains for this session.
- Global read-only aggregation found 312 duplicate `trace_id` groups; the target session contributes one duplicate group. No migration, deletion, or repair was run.

### Logs, flushes, history, and recovery

- No repository application log files (`*.log`, `*.out`, `*.err`, `logs/`, stdout/stderr captures) were found. The host app logs to its terminal/stdout, which was not persisted in an inspectable file.
- `docker logs` for local Mongo (`mongo:8.2.5`), Redis (`redis:alpine`), and Phoenix (`arizephoenix/phoenix:latest`) showed no lines matching the target session, Mongo flush failure, trace-event index failure, duplicate trace, pagination, or history error. This does not establish that no host-app flush error occurred; it establishes only that no matching persisted container log was available.
- The target data is legacy-array-only, so there is no observed immutable write, dual-write failure, event-store pagination response, or backfill result to correlate. Unauthenticated `GET /api/sessions/<target>/events?limit=3` returned HTTP 401, so the history API could not be exercised without credentials.
- The completed duplicate and unreconciled running duplicate are direct evidence of a prior duplicate trace creation/completion race. The absence of `reconciled_*` fields and zero event-store rows show no observed stale-recovery repair or immutable backfill for this session. There is no local evidence proving the cause of the original Mongo flush behavior or a later history pagination failure.

### Restart/deployment timing

- Docker `lambchat-mongodb`, `lambchat-redis`, and `phoenix` all started at 2026-08-07 03:49:17 UTC (11:49:17 local), with zero restarts reported after that start. The app host process started about 20 seconds later at 11:49:37 local. No backend application container is running in `docker ps`; the backend is the host `main.py` process.
- The Mongo/Redis data volume is persistent (`deploy_mongodb-data` mounted at Mongo `/data/db`), so restarting containers would preserve the target's 2026-08-04 duplicate documents. The duplicate documents predate the 2026-08-07 restart by roughly three days.
- `k8s` has no current kubectl context and API discovery returned NotFound, so no live Kubernetes pod/deployment state was available. Static manifests contain no trace-mode env assignments. `k8s/kunxiaozhi.yaml:200-203` probes `/ready`, while the intranet template `deploy/intranet/k8s/kunxiaozhi.template.yaml:188-191` still probes `/health`; this manifest divergence could change whether duplicate-readiness failures block a deployment, but it does not prove that Kubernetes deployed this local process.
- Restart timing is therefore a plausible mechanism for reloading legacy defaults and re-running duplicate preflight, but it is only temporal correlation. The durable duplicate data and legacy-only mode existed before the restart; no evidence shows that the restart created or deleted the target documents.

## Files found

- `src/kernel/config/base.py:67-110` — trace event/recovery settings, defaults, and validators.
- `src/kernel/config/definitions.py:274-294` — stale recovery settings exposed to database settings; event mode keys absent.
- `src/kernel/config/service.py:257-301` — database settings initialization is limited to `SETTING_DEFINITIONS`.
- `.env:68`, `.env.example:78` — legacy trace collection setting; no event mode assignments.
- `src/infra/session/trace_storage.py:177-218` — `traces`/`trace_events` collection wiring and index readiness.
- `src/infra/session/dual_writer.py:86-90`, `:279-380`, `:470-570` — mode selection and buffered Mongo write behavior.
- `k8s/kunxiaozhi.yaml:159-203` — static Mongo env and `/ready` readiness probe.
- `deploy/intranet/k8s/kunxiaozhi.template.yaml:152-191` — static Mongo env and older `/health` readiness probe.
- `.trellis/spec/backend/trace-event-storage.md` — mode and immutable storage contract.
- `.trellis/spec/backend/trace-uniqueness-readiness.md` — fail-closed duplicate/index readiness contract.
- `.trellis/spec/backend/trace-stale-recovery.md` — conservative recovery contract.

## Caveats / Not Found

- Exact in-memory `settings` values of PID 22724 were not extracted from the process; the legacy/legacy conclusion is based on all observable configuration sources, source defaults, database settings absence, and the absence of `trace_events` writes.
- Host application stdout/log history was not available as a file; container logs do not cover the host backend. No definitive Mongo flush exception, pagination exception, or task recovery log can be attributed to the target session.
- No credentials were used or exposed; no destructive database operation, migration, backfill, index creation, or restart was performed.
