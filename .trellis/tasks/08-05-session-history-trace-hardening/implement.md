# Parent Execution Plan

1. 完成并检查 A：共享 cursor、只读历史查询、session/share 前端分页。
2. 完成并检查 C：索引 readiness、重复 preflight、幂等 `create_trace`。
3. 完成并检查 B：`trace_events`、event ID、dual-write/read、backfill 基础能力。
4. 完成并检查 D：dry-run 默认的重复 trace 合并、备份、审计与回滚。
5. 完成并检查 E：heartbeat/task-aware stale-running 恢复。
6. 父任务集成检查：后端/前端全量相关测试、ruff、mypy、TypeScript、跨层契约和 rollout/rollback 演练。

每个子任务必须按 `trellis-implement` → 新 `trellis-check` 顺序串行执行；不得让两个写代理同时修改相同文件。
