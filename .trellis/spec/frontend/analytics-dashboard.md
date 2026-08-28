# Analytics Dashboard (`/analytics`)

## Scenario: One-screen analytics board — filters on top, KPI row, trend + insights, dimension donuts, usage table

### 1. Structure

All files live in `frontend/src/components/panels/analytics/`:

```
AnalyticsPanel.tsx          filter state + data orchestration + layout — the ONLY file that fetches
AnalyticsFilterBar.tsx      date range + [Today][7d][30d][Custom] presets + Persona/agent dropdowns
AnalyticsKpiRow.tsx         6 KPI cards (sparkline + vs-previous-period badge)
AnalyticsTrendChart.tsx     multi-metric trend (sessions/active users/user messages + Token right axis)
AnalyticsInsightPanel.tsx   right-column hard-data insights (peak hour, top token users, fastest persona, new users)
AnalyticsTopRow.tsx         three Top5 donuts (agent / persona / model token) + feedback overview
AnalyticsUsageTable.tsx     usage detail: search (current page) + CSV export + pagination
analyticsKpi.ts             pure KPI derivation (accessors, per-user/per-session ratios, deltaPct)
analyticsDates.ts           pure YYYY-MM-DD string arithmetic (Date.UTC, no setHours/toISOString)
analyticsFormat.ts          number/date formatters, pie palette, weekday labels
analyticsPrimitives.tsx     StatsCard / ChartCard / DonutBlock / LineTrend atoms
```

Subcomponents receive computed data and callbacks only — they never fetch. When filters
change, every section sees the same parameter batch (`usageFilters` memo in the panel).

### 2. Contracts

- **Pure date strings**: all analytics requests send `start`/`end` as `YYYY-MM-DD`
  (backend rejects ISO timestamps). Never compute day boundaries with local-time
  `setHours`/`toISOString` — use `analyticsDates.ts`.
- **One serializer**: every usage endpoint (summary, trend, insights, by-agent,
  by-persona, by-model, by-user, export) goes through `buildUsageQuery`
  (`services/api/analyticsQuery.ts`). Guarded by `analyticsFilterContract.test.ts`.
- **KPI ↔ donut same-source**: donut center numbers and KPI cards call the same
  accessor in `analyticsKpi.ts` (`kpiSessionsValue` → `summary.new_sessions`,
  `kpiTotalTokensValue` → `summary.total_tokens`). Accepted tradeoff: the session
  card shows `new_sessions` (by-agent/by-persona endpoints count created sessions,
  so `active_sessions` cannot be made consistent). Never derive the center by
  summing slices.
- **Ratios**: 人均消息 = `user_messages / using_users`; 会话均 Token =
  `total_tokens / active_sessions`. Divide-by-zero renders `—`, never NaN/Infinity.
- **vs-previous badge** comes from `summary.previous`; when `previous` is missing
  or base is 0, hide the badge entirely (never show 0%).
- **Card 1 switches identity**: without persona/agent filter it is 活跃用户 with a
  "其中使用 N 人" sub-row; with a filter it becomes 使用用户 (`using_users`).
- **CSV export cap**: show `usage.exportHint` (10000-row cap) near the export button.
- `PresetAnalyticsModal` shares `AnalyticsRangePresetPicker` with the dashboard —
  do not fork a second preset picker or date formatter.

### 3. Tests (node --test via tsx, no vitest)

| Test | Guards |
|------|--------|
| `analytics/__tests__/analyticsFilterContract.test.ts` | 8 endpoints receive identical params on filter change; date strings contain no `T`/`Z`; no legacy date math in source |
| `analytics/__tests__/analyticsKpiDerivation.test.ts` | ratio denominators, `—` on zero, hidden badge when previous missing |
| `analytics/__tests__/analyticsConsistency.test.ts` | donut center and KPI use the same accessor (runtime + source scan) |
| `i18n/__tests__/usageReportKeys.test.ts` | 122+ required keys × 5 locales, weekdays array, removed orphan keys stay absent, "vs previous period" wording (never "last week") |

### 4. Gotchas

> **Warning**: Only `AnalyticsPanel` fetches. A subcomponent that calls the API
> directly breaks the "one parameter batch" invariant and the contract test.

> **Warning**: The usage table search filters the current page client-side — the
> backend `/usage/by-user` has no search param. The UI explains this via
> `analytics.usage.searchHint`; do not silently imply full-dataset search.

> **Warning**: `getOverview` and `getSessionsTrend` client methods are orphaned
> since the rewrite (backend endpoints still exist for other consumers to verify).
> Check before reusing or deleting.
