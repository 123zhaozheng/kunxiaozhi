# PR2: PersonaPreset 分析 Modal + 钻取明细

## Goal

在 PR1 基础上，添加角色智能体（PersonaPreset）维度的分析 Modal 和全局看板中的反馈板块 + 图表钻取到明细列表的能力。

> 详细需求参考父任务 PRD：`.trellis/tasks/06-11-analytics-dashboard/prd.md`

## Scope

### 后端
- 实现 `GET /api/analytics/presets/{preset_id}?start=...&end=...` — 单角色智能体完整指标（含基础指标 + 反馈指标 + 点踩原因分布统计）
- 实现 `GET /api/analytics/feedback/summary?start=...&end=...` — 反馈汇总
- 实现 `GET /api/analytics/feedback/by-preset?start=...&end=...` — 按角色智能体分反馈
- 实现钻取明细端点（视实际需要 2-3 个）：
  - `GET /api/analytics/sessions/list?...` — 会话明细列表（支持筛选）
  - `GET /api/analytics/feedback/list?...` — 反馈明细列表
  - `GET /api/analytics/runs/list?...` — 运行明细列表（含 token 用量）

### 前端
- 添加 `analytics.feedback.*`，`analytics.byPreset` 等 chart 类型到 AnalyticsPanel
- 新建 `PresetAnalyticsModal` 组件
- 在 `PersonaPresetCard.tsx` 加入分析按钮（条件渲染：global preset + channel:manage）
- 实现柱状图组件（点踩原因分布）
- 实现明细列表视图组件（点击饼图元素跳转）
- 饼图绑定 onClick → 跳转钻取列表

## Pages Build Up

### Modal（角色分析）
- 概览卡片（4 个）：总消息数 / 总会话数 / 活跃用户数 / token 消耗总计
- 反馈指标：点赞率、点踩原因分布（横向柱状图）

### 全局看板补全
- 反馈板块：汇总统计 + 按角色智能体分反馈柱状图

### 钻取明细
- 点击饼图元素 → 跳转到对应明细列表
- 列表带分页 + 时间筛选器一致性

## Acceptance Criteria

- [ ] 单角色智能体端点返回完整指标
- [ ] 反馈汇总 + 按角色智能体反馈端点可用
- [ ] PersonaPresetCard 上的分析按钮在 global + channel:manage 权限下可见
- [ ] 点击按钮弹出 PresetAnalyticsModal
- [ ] Modal 内图表和指标正确渲染
- [ ] 全局看板补齐反馈板块
- [ ] 饼图点击跳转到对应明细列表
- [ ] 明细列表可分页、可读
- [ ] i18n 五语言完整
- [ ] Lint / typecheck / 测试全部通过

## Out of Scope (for PR2)

- CSV/Excel 导出
- 实时推送
- 用户消息内容查看
- 通知/告警阈值
