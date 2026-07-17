# Persona 分析复用全局统计 + 用户名展示

## Goal

1. Persona 广场的「分析」入口复用已构建的全局统计双维度/钻取能力，不再单独维护一套 `PresetAnalyticsModal` 指标页。
2. 统计钻取界面显示用户 **username（工号）**，而非难以阅读的长 `user_id`。

## Requirements

### R1 Persona 分析复用

- 广场点击某 Persona「分析」时，进入/打开全局 Analytics 的 **persona 维度视图**（时间范围 + 该 persona 的会话/用户/反馈钻取），筛选预填 `persona_preset_id`。
- 优先复用：`AnalyticsPanel` / `AnalyticsDrilldownList` / 已有 by-persona、list、export 契约。
- 删除或降级独立 `PresetAnalyticsModal` 中与全局重复的指标实现（若仍需弹层容器，也只做壳，数据与钻取走统一路径）。
- 不引入第二套 overview 计算逻辑。

### R2 用户名（工号）展示

- 活跃用户列表：主列显示 `username`（工号），可次要显示 display_name；`user_id` 不作为主展示。
- 会话列表：用户列显示 username（需后端 list 补 username 或前端可接受的 join；优先后端 list 带 username，避免 N+1）。
- 反馈列表若已有 username 则保持；无则对齐。
- CSV 导出同步：用户相关列优先 username/工号。

## Acceptance Criteria

- [ ] 广场「分析」不再依赖独立一套与全局无关的 preset metrics 页面作为主路径。
- [ ] 分析入口打开后带当前 persona 筛选，可钻取会话/用户并导出。
- [ ] 会话/用户钻取列表主显示为 username（工号），不是长 ObjectId 主列。
- [ ] 相关单测或关键路径手测说明。

## Dependencies

- 依赖已完成的 dual-dimension + csv 子任务 API。

## Notes

- 父任务：`07-17-persona-template-and-analytics`
