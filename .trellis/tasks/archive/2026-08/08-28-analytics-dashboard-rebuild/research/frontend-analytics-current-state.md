# Frontend Analytics Dashboard — Current State Research

**Task:** 08-24-usage-report-metrics  
**Date:** 2024-08-24  
**Scope:** Map current state of React+TS frontend analytics dashboard, including uncommitted changes, for clean rebuild planning.

---

## 1. AnalyticsPanel.tsx — Full Structure

**File:** `frontend/src/components/panels/AnalyticsPanel.tsx`  
**Lines:** ~1510 (after uncommitted changes added ~277 lines)

### State Management

```typescript
// Core data state (lines 713-739)
const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
const [activeTrend, setActiveTrend] = useState<TrendDataPoint[]>([]);
const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
const [usageTrend, setUsageTrend] = useState<UsageTrendPoint[]>([]);
const [usageRows, setUsageRows] = useState<UsageByUserItem[]>([]);
const [usageTotal, setUsageTotal] = useState(0);
const [usagePage, setUsagePage] = useState(1);
const [usageLoading, setUsageLoading] = useState(true);
const [usageError, setUsageError] = useState<string | null>(null);
const [isUsageExporting, setIsUsageExporting] = useState(false);
const [usageExportError, setUsageExportError] = useState<string | null>(null);
const [personaOptions, setPersonaOptions] = useState<Array<{ id: string; name: string }>>([]);
const [personaPresetId, setPersonaPresetId] = useState("");
const [agentId, setAgentId] = useState("");
const [tokensByModel, setTokensByModel] = useState<ByLabelItem[]>([]);
const [tokensByPreset, setTokensByPreset] = useState<ByLabelItem[]>([]);
const [tokensTrend, setTokensTrend] = useState<TrendDataPoint[]>([]);
const [feedbackSummary, setFeedbackSummary] = useState<FeedbackSummaryResponse | null>(null);
const [feedbackByPreset, setFeedbackByPreset] = useState<ByPresetFeedbackItem[]>([]);
const [sessionsByAgent, setSessionsByAgent] = useState<ByLabelItem[]>([]);
const [sessionsByPersona, setSessionsByPersona] = useState<ByLabelItem[]>([]);
const [drilldown, setDrilldown] = useState<{ kind: DrilldownKind; presetId?: string; rating?: "up" | "down"; initialFilters?: AnalyticsDrilldownFilters; } | null>(null);
const [isLoading, setIsLoading] = useState(true);
const [error, setError] = useState<string | null>(null);
```

### Requests Fired & When

**Main fetch (line 800-856):** Single `Promise.all` fetching 11 endpoints:
1. `analyticsApi.getUsageSummary(start, end, usageFilters)` → summary (KPI cards)
2. `analyticsApi.getActiveUserTrend(start, end, usageFilters)` → activeTrend (line chart)
3. `analyticsApi.getUsersHeatmap(start, end)` → heatmap (heatmap)
4. `analyticsApi.getUsageTrend(start, end, usageFilters)` → usageTrend (multi-series line)
5. `analyticsApi.getTokensByModel(start, end)` → tokensByModel (donut)
6. `analyticsApi.getTokensByPreset(start, end, 10)` → tokensByPreset (donut)
7. `analyticsApi.getTokensTrend(start, end)` → tokensTrend (line)
8. `analyticsApi.getFeedbackSummary(start, end)` → feedbackSummary (stats + reason bar)
9. `analyticsApi.getFeedbackByPreset(start, end)` → feedbackByPreset (stacked bar)
10. `analyticsApi.getSessionsByAgent(start, end, 100)` → sessionsByAgent (donut)
11. `analyticsApi.getSessionsByPersona(start, end, 10)` → sessionsByPersona (donut)

**Usage table fetch (line 854-888):** Independent `listUsageByUser` with pagination, fires on page/range/filter change.

**Persona options load (line 764-788):** `personaPresetApi.list({ scope: "global", limit: 100 })` on mount.

### Visual Block Order

1. **PanelHeader** (line 966-974) — title + subtitle + icon
2. **Time range filter block** (line 978-1033):
   - Preset buttons: "1 day", "7 days", "30 days" (using `t("analytics.timeRange.*")`)
   - Custom range dropdown with date inputs
   - Range anchor text: `${start} → ${end}（UTC+8）`
3. **Persona & Agent filters** (line 1029-1062): Two native `<select>` elements
4. **Error alert** (line 1068-1072) — if main fetch failed
5. **Overview KPI cards** (line 1076-1107): 4-card grid (Active Users, Sessions, User Messages, Total Tokens) — first 3 clickable drilldowns
6. **Users section** (line 1109-1152): Active users trend (line chart, lg:col-span-2), Activity heatmap (heatmap grid)
7. **Sessions section** (line 1156-1238): Sessions & messages trend (multi-series line, full width), Sessions by agent (donut), Sessions by persona (donut)
8. **Tokens section** (line 1242-1303): Tokens by model (donut), Tokens by preset (donut), Tokens trend over time (line)
9. **Usage detail section** (line 1307-1416): Table header + export button, table rows with pagination (new from uncommitted attempt)
10. **Feedback section** (line 1420-1486): Feedback stats cards (total/up/down/upRate), By preset feedback (stacked bar), Reason distribution (bar)
11. **Drilldown drawer** (line 1492-1500): Conditional render of `<AnalyticsDrilldownList />`

### Loading/Empty/Error States

- **Main isLoading** (line 213-248): `ChartCard` wrapper shows `PanelLoadingState` when true, or empty center message when data is empty array
- **Individual charts**: Each has `isLoading={isLoading}` and `isEmpty={!isLoading && data.length === 0}`
- **Usage table loading** (line 1345-1349): Uses `PanelLoadingState` directly inside glass card
- **Usage table empty** (line 1350): Center text "暂无使用数据"
- **Global error** (line 1068): Red alert box above content
- **Usage export error** (line 1343-1347): Red alert inside usage section

### Sub-components Rendered

| Component | Lines | Purpose |
|-----------|-------|---------|
| `StatsCard` | 143-203 | Reusable KPI card with optional hint & click handler |
| `ChartCard` | 207-253 | Frame for charts: title/subtitle/icon + loading/empty/content |
| `PresetButton` | 257-277 | Time range tab style button |
| `CustomRangePicker` | 284-343 | Date range dropdown with start/end inputs |
| `HeatmapGrid` | 347-403 | Weekday×hour CSS grid heatmap |
| `PieBlock` | 407-477 | Donut chart using recharts with slice click handling |
| `FeedbackByPresetBar` | 479-541 | Horizontal stacked bar (up/down per persona) |
| `ReasonBar` | 545-595 | Horizontal bar for feedback reasons |
| `LineTrend` | 599-697 | Multi-series line chart adapter |
| `AnalyticsDrilldownList` | external | Separate component for drilldown tables |

**Oversized render blocks:**
- `PieBlock`: 71 lines (407-477) — donut logic with tooltip + legend
- `LineTrend`: 99 lines (599-697) — series merging + single-point label rendering
- Main `return` block: 536 lines (966-1502) — entire UI structure; candidate for splitting into sections

---

## 2. Supporting Components & Contracts

### AnalyticsDrilldownList.tsx (`frontend/src/components/panels/AnalyticsDrilldownList.tsx`)

**Role:** Drilldown tables for sessions/users/feedback/runs with shared filters.

**Filter contract** (line 12-23):
```typescript
export interface AnalyticsDrilldownFilters {
  agentId?: string;
  personaPresetId?: string;
  roleId?: string;
  sort?: "recent" | "frequency";
}
```

**Props** (line 33-54):
- `kind`: `"sessions" | "users" | "feedback" | "runs"`
- `presetId`: For single-persona views
- `rating`: `"up" | "down"` for feedback drilldown
- `initialFilters`: Pre-populate filter state
- `lockPersonaPresetId`: Hides persona selector (read-only persona context)
- `personaOptions`, `agentOptions`: Dropdown source arrays
- `onBack`: Close drilldown

**API calls** (line 124-166):
- Sessions: `analyticsApi.listSessions(start, end, { agentId, personaPresetId, roleId, sort, skip, limit })`
- Users: `analyticsApi.listActiveUsers(start, end, same schema)`
- Feedback: `analyticsApi.listFeedback(start, end, { presetId, rating, skip, limit })` — note uses `presetId` (deprecated) internally
- Runs: `analyticsApi.listRuns(start, end, { presetId, skip, limit })`

**Pagination:** PAGE_SIZE = 20, independent state.

### PresetAnalyticsModal.tsx (`frontend/src/components/panels/PresetAnalyticsModal.tsx`)

**Role:** Single Persona analysis modal triggered from persona plaza "分析" button.

**Key behaviors:**
- Locked persona (line 143-393): No global filters, only time range + preset analytics metrics
- Fetches `analyticsApi.getPresetAnalytics(preset.id, start, end)` (line 188-208)
- Shows active_users, total_sessions, total_tokens, up_vote_rate, total_messages, down_reasons bar
- Clickable KPI cards open locked drilldowns (line 397-420)
- Uses `EditorSidebar` layout instead of panel shell

---

## 3. API & Types

### analytics.ts (`frontend/src/services/api/analytics.ts`)

**New from uncommitted attempt** (added lines after ~320):

```typescript
// appendUsageFilters (line 39-53) — NEW utility for usage report filtering
async getUsageSummary(start, end, filters?: UsageFilters)      // /api/analytics/usage/summary
async getUsageTrend(start, end, filters?: UsageFilters)         // /api/analytics/usage/trend
async getUsageByPersona(start, end, filters?: UsageFilters)     // /api/analytics/usage/by-persona
async listUsageByUser(start, end, filters?, pagination?)        // /api/analytics/usage/by-user?skip&limit
async exportUsageCsv(start, end, filters?)                      // /api/analytics/usage/export.csv
```

**Existing functions (unchanged but now support filters):**
- `getActiveUserTrend` (line 122-130): Now appends usage filters
- `getSessionsTrend` (line 138-146): Unchanged signature, not used in new dashboard
- `getPresetAnalytics` (line 195-202): Single persona metrics (deprecated approach)
- `listSessions`, `listActiveUsers`, `listFeedback`, `listRuns`: List APIs with pagination, reuse filter logic via `appendListFilters`

**Endpoint pattern:** All usage endpoints hit `/api/analytics/usage/*` with query params: `start`, `end`, `persona_preset_id`, `agent_id`, `role_id`, `skip`, `limit`.

### types/analytics.ts (`frontend/src/types/analytics.ts`)

**New interfaces** (added after line 161):

```typescript
export interface UsageFilters {
  personaPresetId?: string;
  agentId?: string;
  roleId?: string;  // RBAC user role id
}

export interface UsageSummaryResponse {
  active_users: number;
  new_sessions: number;
  active_sessions: number;
  user_messages: number;
  total_tokens: number;
}

export interface UsageTrendPoint {
  date: string;  // YYYY-MM-DD in Asia/Shanghai
  new_sessions: number;
  active_sessions: number;
  user_messages: number;
  total_tokens: number;
}

export interface UsageByUserItem {
  user_id: string;
  username: string;
  display_name: string | null;
  roles: string[];
  persona_preset_id: string | null;
  persona_preset_name: string;
  new_sessions: number;
  active_sessions: number;
  user_messages: number;
  total_tokens: number;
  last_active_at: string | null;
}
```

**Deprecated params still lingering** (removed from code but present in older branches):
- `presetId` → should be `personaPresetId` everywhere (used in `listFeedback` / `listRuns` at line 289-313)
- `presetId` in analytics.listFilters type was removed in favor of `personaPresetId`

---

## 4. Charting Library & Available Primitives

**Library:** `recharts` (imported line 20-39)

**Currently used primitives:**
- `LineChart` + `Line` — multi-series line charts (active users, sessions+messages, tokens trend)
- `PieChart` + `Pie` — donut charts (tokens by model/preset, sessions by agent/persona)
- `BarChart` + `Bar` — horizontal stacked bars (feedback by preset)
- **NOT USED:** `Heatmap`, `Sparkline`, `AreaChart` (custom heatmap implemented manually in `HeatmapGrid` as HTML/CSS grid)

**Available in recharts that could be leveraged:**
- AreaChart, ScatterChart, RadarChart, Treemap, Sankey — none currently used
- Heatmap would require custom cell renderer similar to current implementation

**Gap analysis:** Project does NOT currently use any dedicated sparkline primitive — if sparklines are needed in KPI cards, they'd need manual SVG path generation or simple line-without-axis component.

---

## 5. Design System Inventory

### Card/Panel Components

| Component | File | Notes |
|-----------|------|-------|
| `GlassShell` | `frontend/src/styles/glass.css` | Root container class `.glass-shell` |
| `glass-card` | `frontend/src/styles/glass.css` | Reusable styled div `.glass-card` with subtle bg/border |
| `glass-input` | `frontend/src/styles/glass.css` | Input/select/button styling |
| `PanelHeader` | `frontend/src/components/common/PanelHeader.tsx` | Title/subtitle/icon/actions slot |
| `PanelLoadingState` | `frontend/src/components/common/PanelLoadingState.tsx` | Skeleton loader |
| `Pagination` | `frontend/src/components/common/Pagination.tsx` | Page nav with ellipsis, mobile compact mode |
| `EditorSidebar` | `frontend/src/components/common/EditorSidebar.tsx` | Slide-out sidebar for modals (used by PresetAnalyticsModal) |

### Select/Dropdown

| Component | File | Status |
|-----------|------|--------|
| Native `<select>` | Used inline in AnalyticsPanel (line 1033-1060) | ✅ Exists |
| `GlassSelect` | `frontend/src/components/common/GlassSelect.tsx` | Portal-based custom dropdown |
| Custom date picker | Inline in `CustomRangePicker` (line 284-343) | ✅ Custom implementation exists |
| Segmented control / ButtonGroup for tabs | ❌ **NOT FOUND** — no Tabs or SegmentedButton component |

**Critical gap:** The time range filter ("今天 / 昨天 / 近 7 天 / 近 30 天 / 自定义") is currently implemented as individual `PresetButton`s with inline styles (line 264-277). There is NO reusable Segmented/ButtonGroup/Tabs component in the design system.

### Table

| Component | File | Notes |
|-----------|------|-------|
| Custom HTML table | AnalyticsPanel (line 1355-1402) | Hand-rolled with `min-w-[900px]`, `text-xs`, divide-y borders |
| Pagination | `Pagination` component (line 1-163) | ✓ Already exists |
| ✗ Data grid library | Not used | Gap if complex editable tables needed later |

### Skeleton/Spinner

| Component | File | Notes |
|-----------|------|-------|
| `PanelLoadingState` | `frontend/src/components/common/PanelLoadingState.tsx` | Skeleton-based loader for panels |
| `LoadingSpinner` | `frontend/src/components/common/LoadingSpinner.tsx` | Circular spinner (used in PresetAnalyticsModal) |

### Badge

| Component | File | Notes |
|-----------|------|-------|
| None explicitly found | N/A | Badge pattern not identified in design system |

### Button

| Component | File | Notes |
|-----------|------|-------|
| Inline `<button>` | Everywhere | Styled with utility classes `.rounded-lg`, `.bg-[var(--theme-primary)]`, etc. |
| ✗ Button component | Not abstracted | No reusable Button component (gap?) |

### Tooltip

| Component | File | Notes |
|-----------|------|-------|
| `Tooltip` | `frontend/src/components/common/Tooltip.tsx` | ✅ Exists, used elsewhere in chat UI |
| recharts built-in Tooltip | Inline in charts | Standard recharts Tooltip |

### Theme / Color Tokens

**File:** `frontend/src/styles/tokens.css` (lines 1-110)

**CSS variables available:**
```css
/* Primary */
--theme-primary: #78716c;
--theme-primary-hover: #57534e;
--theme-primary-light: #f5f5f4;
--theme-ring: #78716c;

/* Backgrounds */
--theme-bg: #f5f5f4;
--theme-bg-card: #ffffff;
--theme-bg-sidebar: #f5f5f4;
--theme-bg-elevated: #ffffff;
--theme-bg-subtle: #f5f5f4;

/* Borders */
--theme-border: #e7e5e4;
--theme-border-hover: #d6d3d1;

/* Text */
--theme-text: #1c1917;
--theme-text-secondary: #78716c;
--theme-text-tertiary: #a8a29e;
```

Dark mode overrides at line 77-106.

---

## 6. i18n

**Locale files:** `frontend/src/i18n/locales/{zh,en,ja,ko,ru}.json`

**Analytics namespace shape** (lines 264-400 of zh.json):
```json
{
  "analytics": {
    "dimensions": {
      "byAgent": "...",
      "byAgentHint": "...",
      "byPersona": "...",
      "byPersonaHint": "..."
    },
    "drilldown": {
      "agent": "...", "back": "...", "comment": "...", ..., "exportCsv": "...", "exportFailed": "..."
    },
    "feedback": {
      "byPreset": "...", "down": "...", "downCount": "...", "noReasons": "...", "reasonDistribution": "...", "title": "...", "total": "...", "up": "...", "upCount": "...", "upRate": "..."
    },
    "filters": {
      "agent": "...", "all": "...", "persona": "...", "personaLocked": "...", "sort": "...", "sortFrequency": "...", "sortRecent": "...", "userRole": "..."
    },
    "overview": {
      "activeUsers": "...", "sessions": "...", "activeSessions": "...", "userMessages": "...", "totalSessions": "...", "totalTokens": "..."
    },
    "preset": { ... },
    "sessions": {
      "messages": "...", "userMessages": "...", "sessions": "...", "title": "...", "trend": "...", "trendHint": "..."
    },
    "usage": {
      "title": "...", "subtitle": "...", "exportCsv": "...", "exporting": "...", "exportFailed": "...", "empty": "...",
      "columns": {
        "userId": "...", "name": "...", "role": "...", "persona": "...", "newSessions": "...", "activeSessions": "...", "userMessages": "...", "tokens": "...", "lastActive": "..."
      }
    },
    "subtitle": "...",
    "timeRange": {
      "1d": "...", "30d": "...", "7d": "...", "apply": "...", "custom": "...", "end": "...", "label": "...", "start": "..."
    },
    "title": "...",
    "tokens": {
      "byModel": "...", "byModelHint": "...", "byPreset": "...", "byPresetHint": "...", "title": "...", "total": "...", "trend": "...", "trendHint": "...", "unit": "..."
    },
    "users": {
      "active": "...", "activeTrend": "...", "activeTrendHint": "...", "heatmap": "...", "heatmapHint": "...", "title": "..."
    }
  }
}
```

**Keys added by uncommitted attempt** (new vs pre-existing):
- `analytics.overview.sessions` (renamed from `analytics.overview.totalSessions`)
- `analytics.overview.activeSessions` (NEW — shown as hint on session card)
- `analytics.overview.userMessages` (NEW — replaces upVoteRate card)
- `analytics.filters.persona` / `analytics.filters.agent` (NEW — table filter labels)
- `analytics.usage.title`, `analytics.usage.subtitle` (NEW — new section header)
- `analytics.usage.exportCsv`, `analytics.usage.exporting`, `analytics.usage.exportFailed`, `analytics.usage.empty` (NEW)
- `analytics.usage.columns.*` (NEW — 9 column headers)
- `analytics.sessions.userMessages` (renamed from `analytics.sessions.messages`)
- Some keys exist in zh.json but may be missing in en/ja/ko/ru (check completeness)

**Test coverage:**
- `frontend/src/i18n/__tests__/usageReportKeys.test.ts` — asserts required keys exist in all 5 locales (line 1-57)
- `frontend/src/components/panels/__tests__/analyticsUsageSection.test.ts` — asserts AnalyticsPanel imports and uses new usage APIs (line 1-32)

Tests run via `node --test` (Node builtin test runner), called from package.json scripts.

---

## 7. Test Scripts & Build Commands

**File:** `frontend/package.json` (lines 3-19)

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "i18n:extract": "tsx scripts/extract-i18n.ts",
    "migrate-tests": "tsx scripts/migrate-tests.ts"
  }
}
```

**Running tests:** Node's builtin test runner runs `.test.ts` files via:
```bash
node --test 'src/**/__tests__/*.test.ts'
# or just node test <file>
```

No Jest/Vitest configured; pure ES modules with Node 20+.

**Lint rules:** ESLint config at `frontend/eslint.config.js` (not detailed here).

**Trellis frontend specs relevant:**
- `.trellis/spec/frontend/component-guidelines.md` — naming, props, styling patterns
- `.trellis/spec/frontend/type-safety.md` — TypeScript conventions
- `.trellis/spec/frontend/state-management.md` — when to use Context vs local state

---

## 8. Git Diff Summary — What Was Changed

**Command run:** `git status frontend/src/components/panels/AnalyticsPanel.tsx frontend/src/services/api/analytics.ts frontend/src/types/analytics.ts`  
**Result:** 3 modified files (uncommitted work)

### frontend/src/types/analytics.ts
- **+62 lines, -2 lines** (net +60)
- Added `UsageFilters` interface (3 new fields)
- Added `UsageSummaryResponse`, `UsageTrendPoint`, `UsageTrendResponse`
- Added `UsageByPersonaItem`, `UsageByPersonaResponse`
- Added `UsageByUserItem`, alias `UsageByUserResponse`
- Removed deprecated `presetId` from `AnalyticsListFilters` (2 lines deleted)

### frontend/src/services/api/analytics.ts
- **+81 lines, -4 lines** (net +77)
- Added import of new types (line 39-43)
- Added `appendUsageFilters()` utility function (line 39-53)
- Modified `getActiveUserTrend()` to accept and apply filters (line 122-130)
- Added 5 new usage report methods: `getUsageSummary`, `getUsageTrend`, `getUsageByPersona`, `listUsageByUser`, `exportUsageCsv` (line 317-376)
- Removed deprecated `presetId` fallback in `appendListFilters` (line 62)

### frontend/src/components/panels/AnalyticsPanel.tsx
- **+311 lines, -34 lines** (net +277)
- Added imports: `Download`, `Pagination`, `personaPresetApi`, new types (line 14-54)
- Added constants: `USAGE_PAGE_SIZE = 20`, `USAGE_COLUMN_KEYS` array (line 75-92)
- Added helper: `formatDateTime()` (line 139-145)
- Extended `StatsCard` with optional `hint` prop (line 143-203)
- New state variables: `summary`, `usageTrend`, `usageRows`, `usageTotal`, `usagePage`, `usageLoading`, `usageError`, `isUsageExporting`, `usageExportError`, `personaOptions`, `personaPresetId`, `agentId` (line 713-740)
- Added `usageFilters` useMemo, `agentOptions` useMemo, `personaPresetApi.list` useEffect (line 758-788)
- Refactored `fetchData()` to call new usage APIs instead of old overview/sessions trend (line 798-856)
- Added separate `listUsageByUser` fetch effect with pagination (line 854-888)
- Added `handleUsageExport` callback (line 890-912)
- Added time range listener for dropdown close (line 914-927)
- Added `applyCustomRange` and `handlePresetClick` helpers (line 929-957)
- Added `presetLabel` useMemo (line 959-967)
- **Major UI overhaul:** 
  - Persona/agent select filters inserted before main content (line 1029-1062)
  - Overview section updated to show user messages token cards instead of upVoteRate (line 1075-1107)
  - Sessions section simplified to use merged usageTrend data (line 1159-1197)
  - Entirely new **Usage detail section** with table, export button, pagination (line 1307-1416)
  - Drilldown call updated to pass `personaOptions` and `agentOptions` (line 1492-1495)

**Unfinished/dead items identified:**
- No `Tabs` or `SegmentedButton` component created despite multiple inline button groups in time filter
- No sparkline implementation despite demand in KPI cards
- `CustomRangePicker` is hardcoded with Chinese labels (`t("analytics.timeRange.start")`) but lacks visual polish compared to other panels
- `UsageByPersona` API method defined but NOT USED anywhere in current dashboard — legacy artifact
- Comments in Chinese with question marks encoding issue (`// ── PR2: 单角色智能体 + 反馈 + 钻取明细 ─────────────────────────────`) — cleanup needed
- No skeleton loader for usage table during initial load (only PanelLoadingState shown)

---

## Reusable Primitives Table

| Need | Existing Component / Path | Gap / Notes |
|------|--------------------------|-------------|
| Glass card frame | `glass-card` class in glass.css | ✅ Ready |
| Panel header | `PanelHeader` component | ✅ Ready |
| Skeleton loader | `PanelLoadingState` | ✅ Ready |
| Circular spinner | `LoadingSpinner` | ✅ Ready |
| Pagination | `Pagination` component | ✅ Ready (mobile-responsive) |
| Native select dropdown | Native `<select>` + `glass-input` class | ✅ Ready |
| Custom portal dropdown | `GlassSelect` | ✅ Ready (can replace native selects if desired) |
| Date picker (inline) | `CustomRangePicker` (inline) | ⚠️ Fragmented, no reusable component |
| Tabs / Segmented control | ❌ NONE | Gap: Need reusable TabGroup/ButtonGroup for time range and dimension switches |
| Sparkline chart | ❌ NONE | Gap: Need lightweight sparkline primitive or SVG generator |
| Heatmap | `HeatmapGrid` (inline) | ⚠️ Works but needs extraction to component + storybook example |
| Donut chart | `PieBlock` | ✅ Ready |
| Stacked bar chart | `FeedbackByPresetBar` | ✅ Ready |
| Multi-series line chart | `LineTrend` | ✅ Ready |
| Table with pagination | Hand-rolled table + Pagination | ✅ Functional but no generic DataTable abstraction |
| Tooltip | `Tooltip` component | ✅ Ready |
| Toast/alert banner | Hand-rolled red alert box | ⚠️ No toast system observed |
| Export button action | Inline `<button>` with Download icon | ⚠️ Could extract to IconButton + ExportAction variant |
| Theme tokens | `tokens.css` variables | ✅ Ready |
| Dark mode | Tailwind `dark:` prefix | ✅ Ready |

---

## Junk / Redundancy Candidates

1. **`UsageByPersona` API method unused** (analytics.ts line 338-346): Defined but never consumed in dashboard. Backend endpoint exists but frontend has dropped it.

2. **`presetId` vs `personaPresetId` inconsistency**:
   - `AnalyticsDrilldownList.listFeedback` (line 149) passes `presetId` param
   - `AnalyticsDrilldownList.listRuns` (line 154) also uses `presetId`
   - Both should probably consolidate to `personaPresetId` for consistency, though backend may still expect `presetId`. Verify backend contract.

3. **Duplicate button group patterns**: Time range filter buttons duplicated across:
   - AnalyticsPanel (line 986-1002)
   - PresetAnalyticsModal (line 293-313)
   - No shared component; candidates for extraction

4. **Hardcoded date formatting logic**: `rangeForPreset()`, `toIso()`, `formatCSTDate()` appear in both AnalyticsPanel and PresetAnalyticsModal — potential utils extraction point.

5. **Inline heatmap CSS grid**: `HeatmapGrid` could be extracted to standalone reusable component if heatmap pattern expected in other dashboards.

6. **Red alert boxes scattered throughout**: Multiple places use hand-rolled `bg-red-50 p-3` alert boxes. Consider extracting to reusable Alert component.

---

## Summary (≤400 words)

### Current Dashboard Block Order

1. PanelHeader
2. Time range filter (preset buttons + custom dropdown)
3. Global filters: Persona select, Agent select
4. KPI cards: Active Users, Sessions, User Messages, Total Tokens
5. Users section: Active users trend (line chart), Activity heatmap (grid)
6. Sessions section: Sessions + messages trend (multi-line), Sessions by agent (donut), Sessions by persona (donut)
7. Tokens section: Tokens by model (donut), Tokens by preset (donut), Tokens trend (line)
8. **New**: Usage detail section: User × Persona table with export & pagination
9. Feedback section: Stats cards, By preset feedback (stacked bar), Reason distribution (bar)
10. Drilldown drawer (conditional)

### Chart Library & Capabilities

Library: **recharts**  
Used: LineChart, PieChart (donut), BarChart (horizontal stacked)  
Missing: Sparkline (needed for KPI mini-charts), Heatmap primitive (current implementation is CSS grid)

### Design System Primitives

✅ Exists: Glass card frame, PanelHeader, PanelLoadingState (skeleton), Pagination, native select + GlassSelect (portal dropdown), Tooltip, dark mode theme tokens  
❌ Missing: **Tabs / SegmentedButton / ButtonGroup** (critical gap — time range filter uses raw button duplication), DataTable abstraction (table+pagination works but not reusable), Toast/alert banner system  
⚠️ Fragmented: CustomRangePicker, HeatmapGrid, StatsCard components could be extracted

### Top Redundancy Findings

1. **Time range buttons duplicated** in 2 places without shared component
2. **UsageByPersona API unused** — dead code
3. **presetId / personaPresetId confusion** persists in listFeedback/listRuns calls
4. **Date/time utilities duplicated** between AnalyticsPanel and PresetAnalyticsModal
5. **Scattered alert boxes** instead of unified Banner/Alert component

The dashboard is functional but architecturally fragmented. A clean rebuild should focus on: extracting reusable Tabs/Segmented control, consolidating filter patterns, removing dead code, and standardizing error/loading states.
