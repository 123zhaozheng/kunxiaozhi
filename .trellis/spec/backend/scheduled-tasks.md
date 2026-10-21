# Scheduled Tasks (User-Defined Timed Agent Runs)

> Package: backend. Code: `src/infra/scheduler/*` (service / storage / runner / drafts / persona / locks),
> `src/agents/core/scheduled_run.py`, `src/api/routes/scheduled_task.py`, `src/api/routes/scheduled_task_admin.py`,
> `src/infra/tool/scheduled_task_tool.py`, `src/kernel/schemas/scheduled_task.py`.

## Contract

- Users never send cron. The API accepts `ScheduleSpec{kind: daily|weekdays|weekly|monthly|once|interval, time, weekdays(1..7), month_day(1..31|-1), run_at, every_hours}`.
- `schedule_spec.py` is the single authority for: validation (`ScheduleValidationError`, Chinese message shown verbatim by the UI), trigger building, `describe()` (zh text), `next_fire_times()` preview. Do not duplicate schedule maths elsewhere.
- Default timezone `Asia/Shanghai`; naive `run_at` is interpreted in the task timezone.
- User API `/api/scheduled-tasks` requires `scheduled_task:use`, always owner-scoped: list, create, get, patch, pause, resume, run, delete, runs, preview, templates, drafts (GET / confirm / cancel).
- Admin API `/api/admin/scheduled-tasks` requires `scheduled_task:admin`: cross-user list (filters `q` name regex-escaped case-insensitive, `owner` username→ids, `status`, `last_run_status`; `skip`/`limit` ≤ 100), `POST /{id}/pause` (body `reason` 1..200 chars), `POST /{id}/resume`, `DELETE /{id}`, `GET /{id}/runs`. Every intervention writes one structured `[ScheduledTaskAdmin]` log line (actor / action / task / owner / reason) — the minimal audit trail.
- Data model (二期 additions): `persona_preset_id`, `paused_by: "user"|"admin"|"system"|None`, `pause_reason`, `created_via: "web"|"chat"`. `agent_id` is only `fast` / `search`; legacy documents with other values fall back to `fast` at execution time.

## Expert execution (定时任务选专家)

- Save-time validation `ensure_persona_usable()` (`scheduler/persona.py`) goes through `PersonaPresetManager.get_preset` — visibility rules identical to chat. Use `get_preset`, never `use_preset` (that bumps usage counters). Failure → `ScheduledTaskPersonaError` → 400 Chinese.
- `runner._execute` follows the exact `chat_stream` order: resolve persona → validate model access (`validate_agent_model_access`, owner's real roles) → inject persona knowledge bases (`apply_dify_kb_dataset_ids_to_agent_options`) → `agent_id` (persona preferred agent wins) → `enabled_skills` (persona `skill_names`) → sandbox admission → metadata. Session metadata carries persona name/avatar for display.
- Persona deleted / delisted / owner no longer authorized → `NonRetryableRunError(PERSONA_UNAVAILABLE_RUN_MESSAGE)`: no retry, immediate pause with `paused_by="system"`. Owner-recheck failures behave the same.

## Unattended runs (定时运行标记)

- ContextVar `TraceContext.unattended` is per-run, set explicitly by `TaskExecutor.run_task`; plumbed `TaskManager.submit` / `submit_arq` → arq payload → `arq_worker.run_agent_task`. Default False; chat / wecom / startup recovery never set it. `set_request_context(unattended=None)` means "don't modify".
- `src/agents/core/scheduled_run.py`: `ScheduledRunMiddleware` removes `ask_human`, `update_sop`, `list_scheduled_tasks`, `draft_scheduled_task` from model requests (main agent **and** subagents; fast / search / team) and intercepts stray calls with a Chinese ToolMessage — no approval is created, nothing ever waits. `SCHEDULED_RUN_PROMPT` is appended to the system prompt only for unattended runs and never enters session messages / checkpoint.
- Session metadata `unattended_run_id` marks the agent run; `startup_cleanup` marks such runs failed instead of resuming — the scheduler's own retry policy owns them.
- Unattended runs never emit `task:complete`; only `scheduled_task:run_finished`.

## Chat drafts (D8: card confirmation)

- The agent tools `list_scheduled_tasks` / `draft_scheduled_task` (`src/infra/tool/scheduled_task_tool.py`) only read state or store drafts. Nothing is created or modified until the user confirms the card → `POST /api/scheduled-tasks/drafts/{id}/confirm`.
- Mongo collection `scheduled_task_drafts`; status machine `pending -> confirming -> confirmed | cancelled`. `expired` is **derived** from `expires_at` (30 min) and never persisted. Confirm claims via a single atomic `find_one_and_update` whose query includes `expires_at > now`, so double-click / two tabs / a retried request can apply a draft at most once.
- Update drafts carry `base_signature` (sha1 of user-editable fields; `updated_at` excluded — every run refreshes it, which would cause false conflicts). Signature mismatch on confirm → 409 `DraftConflict`.
- TTL index on `purge_at` (30 days) physically deletes stale docs; the app never sweeps.

## Governance & permissions

- Permissions `scheduled_task:use` / `scheduled_task:admin` (`kernel/types.py`). The default `user` role gets `use`; admin is a system role with everything. Deployed roles are **not** auto-granted — ops must assign to pilot roles.
- Feature toggle `SCHEDULED_TASK_ENABLED` is end-to-end: mutating routes return 400 (`ScheduledTaskDisabledError`), scheduling stops, and the `load_all` close-branch resets the resync marker (open→close→open re-registers correctly). Pausing and deleting stay allowed while off.
- Runtime owner recheck on every fire: owner missing / inactive / lacking `scheduled_task:use` + `chat:write` → non-retryable, immediate pause `paused_by="system"`.
- Admin hold: `paused_by == "admin"` blocks owner resume / run-now / enable with 403 `ScheduledTaskAdminPausedError`; the owner pausing again never downgrades an admin hold. Only `admin_resume` clears it.

## Global concurrency (run slots)

- Redis ZSET `scheduler:running` (member = agent run id, score = expiry). `acquire_run_slot` Lua: purge expired → refresh if the member exists → else claim while `ZCARD < limit`. The clock comes from the client so every replica scores with the same time source.
- `SCHEDULED_TASK_MAX_CONCURRENT_RUNS` (10) and `SCHEDULED_TASK_QUEUE_TIMEOUT_MINUTES` (20) are re-read every poll, so hot-updated settings apply immediately. Slot TTL = task timeout + 120 s.
- Queue wait keeps the run record PENDING. Timeout on attempt 0 → `SKIPPED` (系统繁忙); a later attempt timing out keeps the previous FAILED result.
- `record_task_result`: SUCCESS resets `consecutive_failures`, FAILED / TIMEOUT increment it, **SKIPPED leaves it untouched** (busy ≠ broken). A skipped one-shot auto-pauses instead of completing — never lose a one-shot silently.
- Redis failure fails open (run without a slot; release is then a no-op): chat-path availability wins over strict limiting.

## Multi-replica rules (must keep)

1. Every replica registers every active task in its local `RuntimeScheduler` (job id `user_task:<task_id>`).
2. Before executing a scheduled fire the runner claims a Redis slot `scheduler:task_slot:{task}:{slot}` with `SET NX EX` and **never releases it**. Slot id is derived from the fire minute, so peers compute the same id.
3. The execution lock `scheduler:task_lock:{task}` uses a random token and Lua compare-and-delete. Manual "run now" skips the slot but still takes the lock (409 when busy).
4. Interval triggers are anchored to `last_run_at or created_at` so replicas agree on fire times.
5. `scheduled_task.resync` (60s) reloads when `get_active_marker()` (active count + latest `updated_at`) changes.

## Execution rules

- APScheduler handler must return fast: the runner creates the run record and spawns a detached monitor (`drain_monitors()` on shutdown).
- Agent runs go through `BackgroundTaskManager.submit` / `submit_arq` with executor `agent_stream`, `unattended=True`.
- Session id `sch_<task12>_<run8>[_r<attempt>]`; retries use agent run id `<run_id>-r<attempt>`.
- Session metadata sets `hidden_from_conversation_list=True`; `SessionStorage.list_sessions` excludes those sessions. First user follow-up in such a session un-hides it (via `chat_stream` `extra`). Keep that filter if list queries change.
- After each run: `record_task_result` (atomic `$inc` for `total_runs` / `consecutive_failures`), one-shot -> `completed`, `consecutive_failures >= 3` -> auto-pause with `pause_reason`, WebSocket `scheduled_task:run_finished` to the owner. Notification failures must never fail a run. Non-retryable pauses take precedence over one-shot completion and the failure counter.
- Startup: `ensure_indexes`, close runs stuck RUNNING > 3h, `load_all(force=True)`. Startup failures are logged, never raised.

## Settings

`SCHEDULED_TASK_ENABLED`, `SCHEDULED_TASK_MAX_PER_USER` (20, min/max validated), `SCHEDULED_TASK_MIN_INTERVAL_HOURS` (1), plus 二期: `SCHEDULED_TASK_CHAT_TOOL_ENABLED`, `SCHEDULED_TASK_MAX_CONCURRENT_RUNS` (10), `SCHEDULED_TASK_QUEUE_TIMEOUT_MINUTES` (20) — all in `SETTING_DEFINITIONS` (category agent / subcategory scheduled_task).

## Statistics

Scheduled sessions are normal user sessions for analytics (R6 口径): every metric counts them, queries must not filter `sch_*` / hidden sessions, and each retry attempt (`_r<N>`) counts its own sessions and messages. See `analytics-persona-and-lists.md` for the metric builder.

## Common mistakes

- Registering jobs with `interval_seconds` for user tasks: use `ScheduledJob(trigger=...)`; `interval_seconds` stays for legacy internal jobs.
- Releasing the slot key after a run lets delayed peers re-run the same slot.
- Raising English/internal errors to the UI: map to Chinese (`_friendly_error`).
- Persisting `expired` on drafts, or checking expiry outside the atomic claim query — check-then-confirm is a race; put `expires_at > now` inside the `find_one_and_update`.
- Validating personas with `use_preset` instead of `get_preset` (bumps usage counters).
- Letting SKIPPED increment `consecutive_failures`, or marking a skipped one-shot completed — both silently lose or over-punish runs.
- Putting `updated_at` in the draft signature: every run refreshes it, so update drafts would flag false conflicts.
- Adding ask_human back to unattended runs "because the tool list is shared": mount `ScheduledRunMiddleware` (model-request filter + call interception) instead of not-adding the tool.

## Tests

`tests/infra/scheduler/` (fakes in `conftest.py` / `fakes.py`): spec maths, validation, cross-replica slot dedupe, retry, auto-pause, one-shot completion, resync idempotency, owner isolation, persona execution (`test_persona_execution.py`, real `resolve_persona_request`), admin actions (`test_admin.py`), storage filters (`test_storage_admin.py`), run slots (`test_run_slots.py`, fake redis). API: `test_scheduled_task_routes.py`, `test_scheduled_task_draft_routes.py`, `test_scheduled_task_admin_routes.py`. Agents: `test_scheduled_run_middleware.py` (mount order vs prompt caching, all three agent types). Task plumbing: `test_unattended_flag.py` (local + arq, ContextVar reset semantics).
