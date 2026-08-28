# 子3：看板版面重写

父任务：`.trellis/tasks/08-28-analytics-dashboard-rebuild`
设计依据：父任务 `design.md` §5
前置：子2（`08-28-analytics-api-consolidation`）已完成

## Goal

按用户选定的参考排版（图 3 骨架）重写 `/analytics` 版面：一屏看完、结论在右、明细在下，
且页面上任意两个相关数字互相对得上。配色沿用项目现有 theme token，不采用参考图配色。

## 目标版面

```
┌ 标题 + 日期区间 + [今天][7天][30天][自定义] + Persona/智能体筛选 ┐
│ 数据更新时间                                                    │
├ KPI ×6（每张带 sparkline + 较上一区间 ±x%）                      ┤
├──────────────────────────────┬────────────────┤
│ 核心趋势（多指标同图对比，大折线）              │ 硬数据洞察栏     │
├───────────┬───────────┬───────────┬──────────┤
│ 按智能体Top5 │ 按PersonaTop5 │ 模型TokenTop5 │ 反馈概览   │
├──────────────────────────────────────────┤
│ 使用明细（搜索 + 导出CSV + 分页）                            │
└──────────────────────────────────────────┘
```

### KPI 六张

| # | 卡片 | 主值 | 副行 |
|---|------|------|------|
| 1 | 活跃用户 / 使用用户 | 未筛选：登录去重人数；有 persona/智能体筛选：改标题为「使用用户」并取发过消息人数 | 未筛选时"其中使用 N 人" |
| 2 | 会话数 | 活跃会话 | "新建 M" |
| 3 | 用户消息 | 用户消息数 | — |
| 4 | 总 Token | token 合计 | — |
| 5 | 人均消息 | 用户消息 ÷ 使用用户数 | 悬停说明"分母为发过消息的人数" |
| 6 | 会话均 Token | Token ÷ 活跃会话 | — |

每张卡带 sparkline（趋势数据来自 `usage/trend`）与「较上一区间 ±x%」
（来自 `summary.previous`）。

### 洞察栏四条

| 条目 | 点击行为 |
|------|----------|
| 活跃高峰（周X HH:00，N 条用户消息） | 无 |
| Token 大户 Top3（姓名 + token） | 钻到使用明细并按该用户过滤 |
| 增长最快 Persona（+x% 较上一区间） | 把顶部 persona 筛选设为该 persona |
| 本期新增使用者（N 人） | 钻到用户列表 |

## Requirements

### R1 组件拆分

`AnalyticsPanel.tsx` 现为单文件巨型组件，拆到 `frontend/src/components/panels/analytics/`：

```
AnalyticsPanel.tsx          筛选 state + 数据编排 + 区块布局（唯一发请求的地方）
AnalyticsFilterBar.tsx      日期区间 + 预设按钮组 + Persona/智能体下拉
AnalyticsKpiRow.tsx         6 张 KPI 卡（含 sparkline）
AnalyticsTrendChart.tsx     核心趋势多指标对比
AnalyticsInsightPanel.tsx   右侧硬数据洞察栏
AnalyticsTopRow.tsx         三个环图 + 反馈概览
AnalyticsUsageTable.tsx     使用明细（搜索 + 导出 + 分页）
```

子组件只接收算好的数据与回调，自己不发请求。这样筛选变化时所有区块看到同一批参数。

### R2 筛选契约

- 时间预设：今天 / 7 天 / 30 天 / 自定义。自定义可选任意起止日期。
- 传参为纯日期字符串（子2 已改后端），前端不再用 `setHours` + `toISOString`
  算浏览器本地日边界（现 `AnalyticsPanel.tsx:98-107`、`:125`）。
- Persona 与智能体两个下拉，选中后 KPI、趋势、洞察栏、三个环图、明细表、导出**全部**跟随。
- 时间范围按钮组抽成共用组件，`PresetAnalyticsModal` 也改用它（消除两处重复实现）。

### R3 数字一致性（硬约束）

1. 三个环图中心数字 == 对应 KPI 卡数字。
2. 使用明细表 total（后端返回，不是当前页求和）== 对应 KPI 卡数字。
3. 导出 CSV 行集 == 当前筛选下的明细全集；超 10000 行上限时给出提示。
4. 钻取抽屉的 total == 触发它的卡片数字。
5. 明细表头右上角显示"N 人使用 / 共 M 人登录"，解释为何行数不等于活跃用户卡。

### R4 钻取抽屉

保留现有钻取抽屉，把里面手填 preset id 的输入框改为与顶部同源的下拉
（`AnalyticsDrilldownList.tsx`），`AGENT_OPTIONS` 硬编码改为父组件传入。

### R5 i18n

- 中英日韩俄五个语言包补齐本次全部新增 key，界面不得出现混合语言。
- 「较上一区间」而非「较上周」（选 30 天时"上周"是错的）。
- 保留现有 `frontend/src/i18n/__tests__/usageReportKeys.test.ts` 的守护思路，
  扩展为覆盖本次新增 key。

### R6 样式

- 卡片容器、圆角、阴影、间距沿用现有 `glass-card` 与 panel 约定。
- 图表系列色读项目 theme token，不硬编码参考图配色。
- 桌面与移动视口都要可用（现有响应式断点约定不变）。
- sparkline 用 recharts 去掉坐标轴与网格实现，不引入新图表库。

### R7 清理

- 删除前端 `UsageByPersona` 客户端方法（定义了从未调用）——若本次版面用上了它，则保留。
- 删除重复的日期格式化工具（`AnalyticsPanel` 与 `PresetAnalyticsModal` 各一份）。
- 删除孤儿 i18n key。
- 删除旧版面遗留的未使用组件与常量（含热力图渲染组件，若确认不再使用）。

## Out of Scope

- 后端任何改动（子1、子2）
- 反馈原因分布图的公式与形态（只做位置调整）
- 新增图表库

## Acceptance Criteria

- [ ] `frontend/src/components/panels/analytics/__tests__/analyticsFilterContract.test.ts`：
      切换时间预设 / Persona / 智能体后，所有请求（summary、trend、insights、
      by-agent、by-persona、by-model、by-user、export）收到的参数完全一致。
- [ ] `frontend/src/components/panels/analytics/__tests__/analyticsKpiDerivation.test.ts`：
      人均消息分母为 `using_users`；使用用户为 0 时显示 `—` 而非 `NaN`/`Infinity`；
      `previous` 缺失时不显示 ±x% 而非显示 0%。
- [ ] `frontend/src/components/panels/analytics/__tests__/analyticsConsistency.test.ts`：
      环图中心数字与 KPI 卡数字取自同一字段（同源断言）。
- [ ] `frontend/src/i18n/__tests__/usageReportKeys.test.ts` 扩展后覆盖新增 key，
      五个 locale 全部齐备。
- [ ] 传参为纯日期：测试断言请求 URL 中 `start`/`end` 形如 `2026-08-22`，
      且不含 `T`/`Z`。
- [ ] 空态：无数据时六张卡显示 0/`—`、图表显示空态文案、明细表显示空行提示，不报错。
- [ ] `cd frontend; pnpm run lint` 通过。
- [ ] `cd frontend; pnpm run build` 通过（含 tsc 类型检查）。
- [ ] 新增前端测试全部通过。
- [ ] R7 每项删除有 grep 无引用证据。
