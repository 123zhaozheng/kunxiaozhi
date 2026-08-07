# Technical Design

## Architecture

历史链路分为五个边界：游标契约、不可变事件存储、trace 元数据唯一性、存量数据治理、生命周期恢复。`traces` 只承载 trace 元数据；`trace_events` 成为新事件事实来源。读取在迁移期合并 legacy arrays 与新集合，并通过统一排序键分页。

## Data Contract

- 请求新增可选 `after`，保留现有过滤参数。
- 响应新增 `has_more`、`next_cursor`、`history_complete`、`ordering_version`；`events_limited` 保留为兼容别名。
- cursor 编码并校验版本、session/filter fingerprint 和 exclusive ordering key。
- 排序键为 legacy bucket、session `seq`、timestamp、trace_id、event_id；legacy 事件使用确定性桥接 ID。

## Storage And Rollout

1. 先发布游标契约、纯读路径和明确 incomplete 状态。
2. 发布 `trace_events` dual-write/dual-read，监控失败与覆盖率。
3. backfill legacy arrays，按 session 校验数量与 checksum 后切换读取。
4. 处理重复 trace 后建立/确认唯一索引。
5. 保留 legacy 数据一个回滚窗口，不自动删除。

## Safety

- 迁移工具默认 dry-run，按 `(session_id, trace_id)` 获取租约并备份源文档。
- 活跃 writer/heartbeat 存在时拒绝迁移或 stale 修正。
- 索引初始化失败保持 readiness 失败，不把应用标记为安全可写。
- 所有状态改变使用精确条件和 CAS，并记录原因与操作 ID。

## Trade-offs

- 一事件一文档增加文档与索引数量，但消除单文档 16MB 和 `$slice` 丢失边界，优先保证正确性。
- dual-read 增加迁移期复杂度，但提供渐进 rollout 和可靠 rollback。
- composite cursor 比 seq-only 更复杂，但能处理 legacy、相同 seq/timestamp 与并发追加。
