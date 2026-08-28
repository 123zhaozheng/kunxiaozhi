# Implement：看板版面重写

依赖：子2 已完成（纯日期参数、`usage/insights`、`summary.previous` 可用）。
串行执行 S1 → S5。

前端测试用 `node --test` 经 tsx 运行（项目无 vitest）：
`npx tsx --test src/.../xxx.test.ts`。构建即类型检查（`pnpm run build` = `tsc -b && vite build`）。

---

## S1 类型与 API 客户端

- [x] `frontend/src/types/analytics.ts`：`start`/`end` 类型改为日期字符串；
      `UsageSummary` 增 `usingUsers` 与 `previous`；新增 `UsageInsights` 及子结构。
- [x] `frontend/src/services/api/analytics.ts`：新增 `getUsageInsights`；
      所有筛选序列化统一走一个 helper，确保八个请求参数一致。
- [x] 移除 `presetId` 遗留分支，统一 `personaPresetId`。

验证：`cd frontend; pnpm run build`

## S2 组件骨架与筛选栏

- [x] 新建目录 `frontend/src/components/panels/analytics/`，按 prd R1 拆七个文件。
- [x] `AnalyticsFilterBar.tsx`：日期区间显示 + [今天][7天][30天][自定义] + Persona/智能体下拉。
      预设按钮组抽成独立小组件，`PresetAnalyticsModal` 改用它。
- [x] 日期计算改为纯日期字符串运算，删除 `setHours` + `toISOString` 那套
      （旧 `AnalyticsPanel.tsx:98-127`）。
- [x] `AnalyticsPanel.tsx` 只保留筛选 state、数据编排、区块布局。
- [x] `__tests__/analyticsFilterContract.test.ts`

验证：`cd frontend; npx tsx --test src/components/panels/analytics/__tests__/analyticsFilterContract.test.ts`

## S3 KPI 行、趋势图、洞察栏

- [x] `AnalyticsKpiRow.tsx`：六张卡（含 sparkline + 较上一区间）。
      人均消息分母为 `usingUsers`；除零显示 `—`；`previous` 缺失时隐藏 ±x%。
      有 persona/智能体筛选时第一张卡标题切换为「使用用户」。
- [x] `AnalyticsTrendChart.tsx`：多指标同图对比（会话数、活跃用户、用户消息、Token 右轴），
      粒度切换沿用现有「按天」控件。
- [x] `AnalyticsInsightPanel.tsx`：四条硬数据 + 点击行为（见 prd 表格）。
- [x] sparkline 用 recharts `<LineChart>` 去掉轴与网格实现，不引新库。
- [x] `__tests__/analyticsKpiDerivation.test.ts`

验证：`cd frontend; npx tsx --test src/components/panels/analytics/__tests__/analyticsKpiDerivation.test.ts`

> S3/S4 决策：环图与「会话数」KPI 卡统一取 `summary.new_sessions`（区间内新建会话）。
> 原因：`/sessions/by-agent`、`/sessions/by-persona` 统计的就是区间内创建的会话，
> 与 `active_sessions`（有活动的会话）不同源；后者两个环图端点给不出。
> 共享取值函数在 `analyticsKpi.ts`（`kpiSessionsValue` / `kpiTotalTokensValue`），
> 环图中心与 KPI 卡调用同一函数，`analyticsConsistency.test.ts` 守护。

## S4 环图行、明细表、钻取

- [x] `AnalyticsTopRow.tsx`：智能体 Top5 / Persona Top5 / 模型 Token Top5 三个环图
      + 反馈概览四个数字。环图中心数字与对应 KPI 卡**取自同一字段**。
- [x] `AnalyticsUsageTable.tsx`：搜索（工号/姓名/Persona）+ 导出 CSV + 分页。
      表头右上角显示"N 人使用 / 共 M 人登录"。
      注意：后端 `/usage/by-user` 无搜索参数且 limit≤100，搜索为当前页客户端过滤
      （界面上有提示文案 `analytics.usage.searchHint`）。
- [x] `AnalyticsDrilldownList.tsx`：persona 手填框改为受控下拉；
      `AGENT_OPTIONS` 硬编码改为父组件传入。（已在前置提交 b7e7fd1b 完成，
      `analyticsUsageSection.test.ts` 持续守护。）
- [x] `__tests__/analyticsConsistency.test.ts`

验证：`cd frontend; npx tsx --test src/components/panels/analytics/__tests__/analyticsConsistency.test.ts`

## S5 i18n、清理、收口

- [x] 五个 locale（zh/en/ja/ko/ru）补齐新增 key。文案用「较上一区间」。
- [x] 扩展 `frontend/src/i18n/__tests__/usageReportKeys.test.ts` 覆盖新增 key。
- [x] 清理（每项先 grep 取证，写入 `research/removal-evidence.md`）：
      - 未使用的 `UsageByPersona` 客户端方法（若本次版面用上则保留）
      - 重复的日期格式化工具
      - 孤儿 i18n key
      - 旧版面遗留的未使用组件与常量（含热力图渲染组件，若确认不再使用）
- [x] 空态检查：无数据时六张卡 0/`—`、图表空态文案、明细表空提示，均不报错。
- [x] 桌面与移动视口布局各自检查（响应式类名，不改现有断点约定）。

验证：
```powershell
cd frontend; pnpm run lint
cd frontend; pnpm run build
cd frontend; npx tsx --test src/components/panels/analytics/__tests__/*.test.ts src/i18n/__tests__/usageReportKeys.test.ts
```

---

## 注意

- 遵循 `.trellis/spec/frontend/component-guidelines.md`、`quality-guidelines.md`、
  `type-safety.md`、`state-management.md`。
- 配色只用项目 theme token，参考图配色不采用。
- 只有 `AnalyticsPanel` 发请求；子组件不得自己 fetch。

## 回滚

新代码集中在新目录 `frontend/src/components/panels/analytics/`。
回滚方式：恢复旧 `AnalyticsPanel.tsx` 并撤掉路由引用。
