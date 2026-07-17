# 单 Persona 分析视图（非全局看板）

## Goal

广场「分析」进入 **单 Persona 专用分析**，只展示该人设相关指标与明细；禁止打开全局 Analytics 全貌（by-agent / by-persona 对比等）。

## Product rule

| 入口 | 应看到 |
|---|---|
| 全局 `/analytics` | 双维度总览（保留，运营用） |
| 广场某 Persona「分析」 | **仅该 persona**：谁用过、会话数、token、点赞点踩、明细钻取（用户显示工号） |

## Requirements

### R1 入口
- `handleAnalyze` **不得** `navigate("/analytics")` 打开全局全貌。
- 打开单 Persona 分析 UI（侧栏/Modal/专用页均可），`persona_preset_id` 固定为当前人设。

### R2 内容（仅这些，不要全站对比）
- 时间范围筛选
- 卡片：活跃用户、会话数、token、点赞率（及点踩原因若已有）
- 可点钻取：用过的用户（username 工号）、会话明细、反馈明细
- 筛选锁死 persona；列表可复用 `AnalyticsDrilldownList` 并预填 `personaPresetId`
- **禁止**在此 UI 展示：按智能体分、按 Persona 分反馈全站对比、全站 by-agent 饼图等

### R3 数据复用
- 指标优先 `getPresetAnalytics` / `GET /presets/{id}`（已有）
- 明细复用 list/export + persona 筛选
- 不重写第二套聚合算法；只修正 UI 边界

### R4 清理
- 删除/改掉错误的「分析 → 全局 analytics route state 跳转」路径
- `analyticsRouteState` 若仅服务错误路径，可删或收窄，避免再误用

## Acceptance Criteria

- [ ] 广场分析不进入全局 Analytics 全貌页
- [ ] 单 Persona 视图只有该人设指标：用户/会话/token/赞踩（+原因）
- [ ] 无 by-agent / 全站 by-persona 对比模块
- [ ] 用户明细主显示 username（工号）
- [ ] 相关测试通过

## Notes

- 父：`07-17-persona-template-and-analytics`
- 用户纠正：上次把分析做成全局看板是错的
