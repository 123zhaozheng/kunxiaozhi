# Design

## Data sources

| Range part | Source | Writer |
|---|---|---|
| Historical day | `analytics_daily_snapshot` (core fields immutable via `$setOnInsert`) | backfill / freeze (request-path freeze kept only as last-resort safety net) |
| Today | `analytics_today_snapshot` (same row schema, overwritten each refresh) | `DailyFreezeWorker` tick every 60s |

Row key: `(date, user_id, persona_preset_id, agent_id)`. Row fields: core
(`new_sessions`, `new_session_ids`, `active_sessions`, `active_session_ids`,
`user_messages`, `tokens`, `last_active_at`) + extras (`hour_messages[24]`,
`hour_tokens[24]`, `model_tokens[{model, tokens}]`).

Markers (`user_id = "__snapshot_complete__"`): daily marker gains
`extras_version`; today marker carries `refreshed_at`.

## Shared day computation

`compute_day_rows(storage, date)` in `snapshot.py` — two trace aggregations
(grouped by key × hour; token events grouped by key × model) + one sessions
aggregation. Used by `_freeze_dates` (write: `$setOnInsert` core + `$set`
extras + marker `extras_version`) and `refresh_today_snapshot` (write: `$set`
everything + `refreshed_at`, then delete stale rows / other dates).

## Worker tick (`DailyFreezeWorker.run_once`, 60s, Redis lock)

1. Freeze yesterday if its marker is missing (immediate after midnight).
2. Refresh today's snapshot.
3. Enrich up to 5 most recent frozen dates whose marker lacks `extras_version`.

## Read path (`_merge_results`)

Snapshot-style aggregations run over each source (historical dates in daily
snapshot; today in today snapshot when its marker exists) and their grouped
docs are concatenated — the existing `_merge_metric_items` union/sum logic
already merges duplicate keys. Realtime today block only runs when the today
marker is missing. The `not historical_items` realtime fallback is removed
(markers make empty days authoritative).

New result keys: `by_hour` (single-day only), `peak`, `by_model`,
`active_users_by_day`, `extras_complete`, `refreshed_at`.

## Cache

`AnalyticsStorage._read_usage(filters)`: key = filters tuple; in-flight
future map (single flight) + TTL cache (30s if range includes today, else
600s), bounded to 256 entries. Calls module-level `read_or_freeze` so existing
monkeypatches keep working.

## API changes (additive)

- `UsageSummaryResponse.data_updated_at: datetime | None`
- `UsageTrendResponse.hourly: list[UsageHourPoint]` (hour, user_messages, total_tokens), single-day only
- `/tokens/by-model` items: `label` = model display name, `id` = raw model id; `partial` when any date lacks extras.

## Frontend

Tabs (dashboard / usage detail), drilldown replaces content, RankedBarList
replaces DonutBlock, trend metric switcher (single series, single axis,
hourly for 1d), palette from dataviz reference (light/dark steps).

## Rollback

All changes additive at the data layer: dropping `analytics_today_snapshot`
and reverting code restores the old realtime path; extras fields are ignored
by old code.
