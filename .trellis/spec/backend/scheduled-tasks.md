# Scheduled Tasks (User-Defined Timed Agent Runs)

> Package: backend. Code: `src/infra/scheduler/*`, `src/api/routes/scheduled_task.py`,
> `src/kernel/schemas/scheduled_task.py`. Underlying logic adapted from LambChat.

## Contract

- Users never send cron. The API accepts `ScheduleSpec{kind: daily|weekdays|weekly|monthly|once|interval, time, weekdays(1..7), month_day(1..31|-1), run_at, every_hours}`.
- `schedule_spec.py` is the single authority for: validation (`ScheduleValidationError`, Chinese message shown verbatim by the UI), trigger building, `describe()` (zh text), `next_fire_times()` preview. Do not duplicate schedule maths elsewhere.
- Default timezone `Asia/Shanghai`; naive `run_at` is interpreted in the task timezone.
- API `/api/scheduled-tasks` (requires `chat:write`, always owner-scoped): list, create, get, patch, pause, resume, run, delete, runs, preview, templates.

## Multi-replica rules (must keep)

1. Every replica registers every active task in its local `RuntimeScheduler` (job id `user_task:<task_id>`).
2. Before executing a scheduled fire the runner claims a Redis slot `scheduler:task_slot:{task}:{slot}` with `SET NX EX` and **never releases it**. Slot id is derived from the fire minute, so peers compute the same id.
3. The execution lock `scheduler:task_lock:{task}` uses a random token and Lua compare-and-delete. Manual "run now" skips the slot but still takes the lock (409 when busy).
4. Interval triggers are anchored to `last_run_at or created_at` so replicas agree on fire times.
5. `scheduled_task.resync` (60s) reloads when `get_active_marker()` (active count + latest `updated_at`) changes.

## Execution rules

- APScheduler handler must return fast: the runner creates the run record and spawns a detached monitor (`drain_monitors()` on shutdown).
- Agent runs go through `BackgroundTaskManager.submit` / `submit_arq` with executor `agent_stream`; model access is validated with the owner's real roles (`validate_agent_model_access`), persona via `resolve_persona_request`.
- Session id `sch_<task12>_<run8>[_r<attempt>]`; retries use agent run id `<run_id>-r<attempt>`.
- Session metadata sets `hidden_from_conversation_list=True`; `SessionStorage.list_sessions` excludes those sessions. Keep that filter if list queries change.
- After each run: `record_task_result` (atomic `$inc` for `total_runs` / `consecutive_failures`), one-shot -> `completed`, `consecutive_failures >= 3` -> auto-pause with `pause_reason`, WebSocket `scheduled_task:run_finished` to the owner. Notification failures must never fail a run.
- Startup: `ensure_indexes`, close runs stuck RUNNING > 3h, `load_all(force=True)`. Startup failures are logged, never raised.

## Settings

`SCHEDULED_TASK_ENABLED`, `SCHEDULED_TASK_MAX_PER_USER` (20), `SCHEDULED_TASK_MIN_INTERVAL_HOURS` (1) in `SETTING_DEFINITIONS` (category agent / subcategory scheduled_task).

## Common mistakes

- Registering jobs with `interval_seconds` for user tasks: use `ScheduledJob(trigger=...)`; `interval_seconds` stays for legacy internal jobs.
- Releasing the slot key after a run lets delayed peers re-run the same slot.
- Raising English/internal errors to the UI: map to Chinese (`_friendly_error`).

## Tests

`tests/infra/scheduler/` (fakes in `conftest.py`): spec maths, validation, cross-replica slot dedupe, retry, auto-pause, one-shot completion, resync idempotency, owner isolation.
