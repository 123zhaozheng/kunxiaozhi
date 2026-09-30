# Design — Scheduled Tasks

## Module layout

```
src/kernel/schemas/scheduled_task.py   Pydantic models (ScheduleSpec, Task, Run, API DTOs)
src/infra/scheduler/runtime.py         RuntimeScheduler: +register_job/unregister_job/has_job, generic triggers (backward compatible)
src/infra/scheduler/schedule_spec.py   Friendly spec -> APScheduler trigger, zh description, next-N preview, validation
src/infra/scheduler/locks.py           Redis slot claim + execution lock (Lua CAS release)
src/infra/scheduler/storage.py         Mongo collections scheduled_tasks / scheduled_task_runs + indexes
src/infra/scheduler/runner.py          Execution engine (slot -> lock -> run record -> detached monitor -> retry -> finalize -> notify)
src/infra/scheduler/service.py         CRUD, quota, (un)registration, startup load, periodic resync
src/infra/scheduler/templates.py       Built-in bank scenario templates
src/api/routes/scheduled_task.py       REST API under /api/scheduled-tasks
frontend/src/components/panels/ScheduledTaskPanel/*   UI
```

## Data model

`scheduled_tasks` document:
`_id, owner_id, name, prompt, schedule{kind,time,weekdays,month_day,run_at,every_hours},
timezone, agent_id, persona_preset_id, model_id, enabled, status(active|paused|completed),
pause_reason, max_retries, timeout_seconds, consecutive_failures, last_run_at, last_run_status,
last_run_id, next_run_at, total_runs, template_id, created_at, updated_at`.

`scheduled_task_runs` document:
`_id(run_id), task_id, owner_id, trigger(schedule|manual), status, session_id, attempt,
started_at, finished_at, duration_ms, result_preview, error_message`.

Indexes: tasks `(owner_id, updated_at)`, `(enabled, status)`; runs `(task_id, started_at desc)`,
`(owner_id, started_at desc)`, TTL-free (bounded by list pagination; retention can be added later).

## Friendly schedule -> trigger

| kind | trigger |
|---|---|
| daily | CronTrigger(hour, minute, tz) |
| weekdays | CronTrigger(day_of_week="mon-fri") |
| weekly | CronTrigger(day_of_week=",".join(days)) |
| monthly | CronTrigger(day=N or "last") |
| once | DateTrigger(run_at in tz) — must be future |
| interval | IntervalTrigger(hours=N, start anchored to created_at/last_run_at) |

Anchoring interval triggers to persisted timestamps (LambChat idea) makes every replica compute the
same fire times, so the Redis slot key `interval:<due_ts>` dedupes across replicas.

## Multi-replica coordination (from LambChat)

1. Every replica registers every active task in its local APScheduler.
2. On fire, runner computes a slot id (`cron:<scheduled_fire_ts floored to minute>`,
   `date:<ts>`, `interval:<due_ts>`), `SET NX EX` on `scheduler:task_slot:{task}:{slot}`.
   Loser replicas skip. Slot keys are never released (TTL 1 day) so delayed peers don't re-run.
3. Execution lock `scheduler:task_lock:{task}` with random token, TTL = timeout*(retries+1);
   released via Lua compare-and-delete. Prevents overlap between a long run and the next fire and
   between manual "run now" and scheduled fire.
4. Periodic `scheduled_task_resync` interval job (60s) reloads tasks when the marker
   `(count, max(updated_at))` changes, so edits on replica A reach replica B.

## Execution

Runner submits through `get_task_manager().submit(...)` (or `submit_arq` when `TASK_BACKEND=arq`)
with executor `agent_stream`, persona resolved via `resolve_persona_request`, model access via
`validate_agent_model_access` using the owner's real roles. The APScheduler job returns immediately;
a detached monitor polls `get_run_status` until terminal/timeout (cancel on timeout), retries,
extracts a result preview from trace `message:chunk` events, writes run + task stats, applies
consecutive-failure auto-pause, completes one-shot tasks, releases the lock and pushes a WebSocket
`scheduled_task:run_finished` message.

Session metadata: `source=scheduled_task, scheduled_task_id, scheduled_task_run_id,
hidden_from_conversation_list=True`. `SessionStorage.list_sessions` excludes hidden sessions.

## Settings

`SCHEDULED_TASK_ENABLED` (bool, True), `SCHEDULED_TASK_MAX_PER_USER` (int, 20),
`SCHEDULED_TASK_MIN_INTERVAL_HOURS` (int, 1). Category: existing general/agent category.

## Compatibility / rollback

- `RuntimeScheduler.register_interval_job` keeps its signature; `ScheduledJob.interval_seconds`
  stays supported, new optional `trigger` field.
- Feature is additive; disabling `SCHEDULED_TASK_ENABLED` skips loading/firing without data loss.
- Rollback = revert commit; new collections are harmless if left behind.
