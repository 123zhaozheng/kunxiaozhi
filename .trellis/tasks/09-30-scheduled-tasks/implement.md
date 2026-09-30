# Implement Plan

1. [x] Extend `RuntimeScheduler` (trigger support, unregister/has_job) + keep tests green.
2. [x] `schedule_spec.py` + schemas + unit tests (compile, describe, preview, validation).
3. [x] `locks.py`, `storage.py`, `templates.py`.
4. [x] `runner.py` (slot/lock/record/monitor/retry/auto-pause/notify) + tests with fakes.
5. [x] `service.py` (CRUD/quota/register/load/resync) + tests.
6. [x] API route + main.py registration + indexes + runtime startup + settings definitions.
7. [x] Hide scheduled sessions from `list_sessions`.
8. [x] Frontend: types, api service, panel (list, wizard, preview, runs), tab/route/nav, i18n zh/en.
9. [x] Validation: `uv run pytest tests/infra/scheduler tests/api/test_scheduled_task_routes.py`,
       existing scheduler tests, `uv run ruff check`, `pnpm exec tsc -b` in frontend.
10. [x] Spec update: `.trellis/spec/backend/scheduled-tasks.md`, frontend spec entry, index rows.
11. [x] Commit, journal via add_session.py.

Rollback points: after step 1 (pure refactor), after step 7 (backend complete), after step 8.
