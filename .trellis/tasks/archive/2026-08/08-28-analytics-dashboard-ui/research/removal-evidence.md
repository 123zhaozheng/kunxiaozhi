# Removal Evidence — analytics dashboard S5 清理

日期：2026-08-28 · 范围：`frontend/`（Windows + Git Bash，`cd frontend` 后执行）。
所有命令均为删除前取证；命令 + 原始输出保留如下。

---

## E1 `getUsageByPersona` 客户端方法 —— 已删除

前端无任何调用方，仅服务文件自身定义：

```
$ grep -rn "getUsageByPersona" src --include="*.ts" --include="*.tsx"
src/services/api/analytics.ts:342:  async getUsageByPersona(
```

处置：删除 `analyticsApi.getUsageByPersona`（前端客户端方法）。
后端 `/usage/by-persona` 端点不动（任务范围外）；`UsageByPersonaItem/Response`
类型保留在 `types/analytics.ts` 作为后端 schema 镜像（无 lint 影响）。

## E2 旧版 Users / Tokens 段数据请求 —— 已删除

三个方法仅被 `AnalyticsPanel` 中即将删除的旧段落调用：

```
$ grep -rn "getUsersHeatmap\|getTokensByPreset\|getTokensTrend" src --include="*.ts" --include="*.tsx"
src/components/panels/analytics/AnalyticsPanel.tsx:163:        analyticsApi.getUsersHeatmap(start, end),
src/components/panels/analytics/AnalyticsPanel.tsx:167:        analyticsApi.getTokensByPreset(start, end, 10),
src/components/panels/analytics/AnalyticsPanel.tsx:168:        analyticsApi.getTokensTrend(start, end),
src/services/api/analytics.ts:114:  async getUsersHeatmap(
src/services/api/analytics.ts:142:  async getTokensByPreset(
src/services/api/analytics.ts:152:  async getTokensTrend(start: string, end: string): Promise<TrendResponse> {
```

处置：删除三个客户端方法 + 面板内对应 state（`heatmap`/`tokensByPreset`/`tokensTrend`）
与旧版 Users 段（活跃趋势 + 热力图）、Tokens 段（by-preset 饼图 + token 趋势）JSX。

## E3 `getActiveUserTrend` —— 保留（复用中）

```
$ grep -rn "getActiveUserTrend" src --include="*.ts" --include="*.tsx"
src/components/panels/analytics/AnalyticsPanel.tsx:162:        analyticsApi.getActiveUserTrend(start, end, usageFilters),
src/services/api/analytics.ts:104:  async getActiveUserTrend(
```

面板仍用它驱动第一张 KPI 卡 sparkline 与核心趋势图的「活跃用户」序列（`activeTrend`），不删。

## E4 `HeatmapGrid` / `PieBlock` / `HeatmapCell` —— 已删除

```
$ grep -rn "HeatmapGrid\|PieBlock\|HeatmapCell\|HeatmapResponse" src --include="*.ts" --include="*.tsx"
src/components/panels/analytics/AnalyticsPanel.tsx:21:  HeatmapCell,
src/components/panels/analytics/AnalyticsPanel.tsx:46:import { ChartCard, HeatmapGrid, LineTrend, PieBlock } from "./analyticsPrimitives";
src/components/panels/analytics/AnalyticsPanel.tsx:78:  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
src/components/panels/analytics/AnalyticsPanel.tsx:388:                <HeatmapGrid cells={heatmap} />
src/components/panels/analytics/AnalyticsPanel.tsx:440:              <PieBlock
src/components/panels/analytics/analyticsPrimitives.tsx:144:interface HeatmapGridProps {
src/components/panels/analytics/analyticsPrimitives.tsx:145:  cells: HeatmapCell[];
src/components/panels/analytics/analyticsPrimitives.tsx:148:export function HeatmapGrid({ cells }: HeatmapGridProps) {
src/components/panels/analytics/analyticsPrimitives.tsx:205:interface PieBlockProps {
src/components/panels/analytics/analyticsPrimitives.tsx:212:export function PieBlock({ title, data, unitFormatter, onSliceClick }: PieBlockProps) {
src/components/panels/analytics/__tests__/analyticsConsistency.test.ts:91:  assert.doesNotMatch(topRowSource, /PieBlock/);
src/services/api/analytics.ts:17:  HeatmapResponse,
src/services/api/analytics.ts:117:  ): Promise<HeatmapResponse> {
src/services/api/analytics.ts:118:    return authFetch<HeatmapResponse>(
src/types/analytics.ts:24:export interface HeatmapCell {
src/types/analytics.ts:30:export interface HeatmapResponse {
src/types/analytics.ts:31:  cells: HeatmapCell[];
src/types/index.ts:292:  HeatmapCell,
src/types/index.ts:293:  HeatmapResponse,
```

处置：
- 删除 `HeatmapGrid` 原语（唯一消费方是旧 Users 段）。
- 顺带删除 `PieBlock` 原语：唯一消费方是旧 Tokens 段 by-preset 饼图；
  `analyticsConsistency.test.ts:91` 仅断言 TopRow 不含 PieBlock，删除后仍通过。
- 删除 `HeatmapCell`/`HeatmapResponse` 类型及 `types/index.ts` 再导出。
- 删除后复查（仅存守护断言，无真实引用）：

```
$ grep -rn "getUsageByPersona\|getUsersHeatmap\|getTokensByPreset\|getTokensTrend\|HeatmapGrid\|HeatmapCell\|PieBlock" src --include="*.ts" --include="*.tsx"
src/components/panels/analytics/__tests__/analyticsConsistency.test.ts:91:  assert.doesNotMatch(topRowSource, /PieBlock/);
```

## E5 孤儿 i18n key —— 已从五个 locale 删除

旧段落引用的 key（见 E-面板行号）在删除段落/替换组件后无任何引用：

```
$ grep -rn '"analytics\.users\.' src --include="*.tsx" --include="*.ts"
src/components/panels/analytics/AnalyticsPanel.tsx   （仅旧 Users 段，S5 已删）

$ grep -rn '"analytics\.sessions\.(title|messages|trend)"|"analytics\.sessions\.trendHint"' src   # 0 条
$ grep -rn '"analytics\.overview\.totalSessions"' src                                             # 0 条
$ grep -rn '"analytics\.dimensions\.(byAgent|byPersona)"' src                                     # 0 条
```

`analytics.tokens.*` 逐条核对（保留项仍有真实引用）：

```
$ grep -rn '"analytics\.tokens\.' src --include="*.ts" --include="*.tsx"
AnalyticsPanel.tsx（旧 Tokens 段，S5 已删）: title / byPreset / byPresetHint / trend / trendHint / total / unit
src/components/panels/analytics/AnalyticsTopRow.tsx:113:    "analytics.tokens.byModelHint",     ← 模型环图副标题（保留）
src/components/panels/analytics/AnalyticsTopRow.tsx:123:    centerCaption={t("analytics.tokens.total", …)}  ← 保留
src/components/panels/analytics/AnalyticsTrendChart.tsx:28/72: unit / total                     ← 保留
```

删除清单（五个 locale 同步）：`analytics.users.*`（整段）、
`analytics.sessions.{title,messages,trend,trendHint}`、`analytics.overview.totalSessions`、
`analytics.tokens.{byModel,byPreset,byPresetHint,title,trend,trendHint}`、
`analytics.dimensions.{byAgent,byPersona}`。
`usageReportKeys.test.ts` 新增 `removedKeys` 断言，防止回潮。

## E6 重复日期格式化工具 —— 合并为一处

```
$ grep -rn "function formatDateTime" src --include="*.ts" --include="*.tsx"
src/components/panels/analytics/analyticsFormat.ts:30:export function formatDateTime(iso: string | null | undefined): string {
src/components/panels/AnalyticsDrilldownList.tsx:63:function formatDateTime(iso: string | null | undefined): string {
src/utils/datetime.ts:22:export function formatDateTime(input: TimeInput): string {
src/utils/datetime.ts:44:export function formatDateTimeShort(input: TimeInput): string {
```

处置：`AnalyticsDrilldownList.tsx` 内的本地实现与 `analyticsFormat.ts` 逐字相同，
改为 `import { formatDateTime } from "./analytics/analyticsFormat"`。
`utils/datetime.ts` 的共享版语义不同（`+Z` 解析启发式、不处理 null），
被 chat/memory/feedback 等面板使用，保持不动。
区间计算类重复（`setHours`+`toISOString`）已在 S2 删除，
`analyticsFilterContract.test.ts:203-209` 守护，无残留。
钻取列表本地 `formatNumber` 与 `analyticsFormat.formatNumber` 有意不同
（前者不做 K/M 缩写），保留。

## E7 观察记录（未删除，超出本次范围）

以下两个客户端方法同样无调用方，但不在 S5 清单内，留待协调方决定：

```
$ grep -rn "getSessionsTrend\|getOverview" src --include="*.ts" --include="*.tsx"
src/services/api/analytics.ts:100:  async getOverview(start: string, end: string): Promise<OverviewResponse> {
src/services/api/analytics.ts:123:  async getSessionsTrend(
（其余匹配均为服务文件自身；无任何组件/测试调用）
```

另：`analyticsFormat.WEEKDAY_LABELS` 曾同时服务热力图与洞察栏；热力图删除后
仍被 `AnalyticsInsightPanel`（`analytics.weekdays.N` 的兜底文案）使用，保留。
