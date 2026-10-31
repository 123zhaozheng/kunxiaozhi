# Analytics Dashboard (`/analytics`)

## Scenario: Analytics board — one-row filters, [数据看板 | 使用明细] tabs, KPI row, trend + insights, ranked bars, feedback

### 1. Structure

All files live in `frontend/src/components/panels/analytics/`:

```
AnalyticsPanel.tsx          filter state + data orchestration + tabs/layout — the ONLY file that fetches
AnalyticsFilterBar.tsx      one card: [Today][7d][30d][Custom] presets + range label + Persona/agent selects
AnalyticsKpiRow.tsx         6 KPI cards (sparkline + vs-previous-period badge; hourly sparklines for 1d)
AnalyticsTrendChart.tsx     one metric at a time (segmented switcher), one axis; hourly for 1d, daily zero-filled
AnalyticsInsightPanel.tsx   right-column hard-data insights (peak hour, top token users, fastest persona, new users)
AnalyticsTopRow.tsx         agent / persona / model ranked-bar cards + feedback overview row
AnalyticsUsageTable.tsx     usage detail tab: search (current page) + CSV export + pagination
useSeriesColor.ts           theme-aware single series hue
analyticsKpi.ts             pure KPI derivation (accessors, per-user/per-session ratios, deltaPct)
analyticsDates.ts           pure YYYY-MM-DD string arithmetic (Date.UTC, no setHours/toISOString)
analyticsFormat.ts          formatters, dataviz palette (series + status), pure rankRows()
analyticsPrimitives.tsx     ChartCard / RankedBarList / FeedbackPresetBars / TrendArea atoms
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
- **KPI ↔ ranked-list same-source**: list share denominators and KPI cards call
  the same accessor in `analyticsKpi.ts` (`kpiSessionsValue` → `active_sessions`,
  `kpiTotalTokensValue` → `total_tokens`). Never derive a denominator by summing
  visible rows (a truncated list would inflate shares).
- **Default range is 1d (today)**; nothing is computed on open — the backend
  serves pre-aggregated snapshots and `summary.data_updated_at` is shown as
  "数据更新于 HH:mm".
- **Charts (dataviz)**: no donuts, no dual y-axis. Breakdowns are ranked
  horizontal bars in one hue; trends are a single series with a crosshair
  tooltip, daily series zero-filled via `daysInRange`. Status colors only for
  feedback up/down, always with icon + label.
- **Usage detail lives in its own tab** and is fetched only while that tab is
  open; drilldowns replace the content area (with back) instead of appending
  below the board.
- **Model slices** show `label` (display name) but drill down with `id`.
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
| `analytics/__tests__/analyticsConsistency.test.ts` | ranked-list denominators and KPI use the same accessor; no donut / right axis (source scan) |
| `analytics/__tests__/analyticsRankedBars.test.ts` | ranking order, tail folding, KPI-denominated shares |
| `i18n/__tests__/usageReportKeys.test.ts` | 122+ required keys × 5 locales, weekdays array, removed orphan keys stay absent, "vs previous period" wording (never "last week") |

### 4. Gotchas

> **Warning**: Only `AnalyticsPanel` fetches. A subcomponent that calls the API
> directly breaks the "one parameter batch" invariant and the contract test.

> **Warning**: The usage table search filters the current page client-side — the
> backend `/usage/by-user` has no search param. The UI explains this via
> `analytics.usage.searchHint`; do not silently imply full-dataset search.

> **Warning**: The orphan `getOverview`/`getSessionsTrend` client methods were
> deleted on 2026-08-28 (no component caller). Backend `/overview` and
> `/sessions/trend` endpoints still exist for out-of-repo consumers — do not
> re-add frontend client wrappers without an actual call site.
