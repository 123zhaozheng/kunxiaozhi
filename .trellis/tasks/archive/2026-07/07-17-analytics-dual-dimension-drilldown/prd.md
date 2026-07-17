# 统计双维度 + 钻取筛选 + 卡片可点

## Goal

统计支持 **by-agent** 与 **by-persona** 双维度；活跃用户/总会话卡片可点钻取；列表支持时间、智能体、Persona、RBAC 用户角色筛选与频次/最近排序。

## Requirements

- by-agent 汇总（会话数等）与现有 by-preset 同级可用。
- 活跃用户列表 API + UI 钻取。
- 会话列表增强 filter：`agent_id`、`persona_preset_id`、用户角色；sort：频次、最近。
- StatsCard：活跃用户、总会话可点。
- 文案：用户角色 / 智能体 / Persona 分离。
- 依赖新会话双字段写入（binding）；本任务负责读路径与 UI。

## Acceptance Criteria

- [ ] overview 或独立接口可返回按 agent_id 聚合。
- [ ] 点活跃用户 → 列表；可按 agent/persona/角色筛；可按频次排序。
- [ ] 点总会话 → 列表；同上筛选。
- [ ] 无筛选时行为与现网兼容（仅时间范围）。

## Dependencies

- 读字段依赖 binding 的写入口径；可先实现 filter，binding 后集成验。
- CSV 子任务依赖本任务 list 查询参数。

## Notes

- 父任务：`07-17-persona-template-and-analytics`
- 历史测试数据可不完美。
