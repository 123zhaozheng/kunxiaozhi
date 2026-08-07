# stale-running 生命周期恢复

## Goal

把 stale-running 修正移出历史读取，结合任务状态和 heartbeat 保守恢复真正失联的 trace。

## Dependencies

依赖 A 已移除读路径状态修改；复用现有 startup cleanup/task heartbeat 事实来源。

## Requirements

- 仅扫描超过 grace period 的 running trace。
- 检查 session task status、current run、Redis heartbeat 和终止事件。
- 活跃 heartbeat 存在时绝不翻转；更新使用仍为 running 的 CAS 条件。
- 记录 reconciled 时间、原因和观察状态，并暴露计数/失败日志。

## Acceptance Criteria

- [x] 历史 GET 不触发状态修正。
- [x] 活跃 run 即使存在 error/done 子事件也不会被误标终态。
- [x] 真正 stale 且满足保守终止条件的 trace 可在 startup/scheduled recovery 中恢复。
- [x] 并发 writer/reconciler 竞态由 CAS 和 heartbeat 测试覆盖。
