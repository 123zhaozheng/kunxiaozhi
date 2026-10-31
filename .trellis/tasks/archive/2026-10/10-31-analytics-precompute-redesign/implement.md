# Implement

1. snapshot.py: `compute_day_rows`, `_freeze_dates` writes extras + marker version, `refresh_today_snapshot`, `freeze_missing_dates`/`enrich` helpers; `_merge_results` multi-source + extras outputs; lazy Redis in `read_or_freeze`.
2. daily_freeze.py: tick = freeze yesterday + refresh today + enrich; 60s interval.
3. storage.py: `today_snapshot`/`model_configs` collections + index; `_read_usage` cache; switch all `read_or_freeze` callers; active users by day, insights peak/top users, tokens by model from snapshot + model label mapping; remove hourly read path.
4. schemas + routes: `data_updated_at`, `hourly`.
5. Tests: update insights/by-model tests; add cache, today snapshot, compute rows, worker tick, model label tests. → `uv run pytest tests/infra/test_analytics*.py tests/infra/analytics tests/api/routes/test_analytics*.py`
6. Frontend: default 1d, tabs, RankedBarList, trend switcher/hourly, updated-at, model drilldown by id; i18n keys (5 locales). → `npx tsc --noEmit`, eslint on changed files, analytics node tests, i18n key test.
7. Spec update: frontend/analytics-dashboard.md, backend analytics doc.
