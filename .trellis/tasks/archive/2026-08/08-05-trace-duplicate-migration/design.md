# Design

- 采用现有 CLI/admin 模式实现，默认 dry-run；apply 必须显式参数。
- 维护 lease 覆盖整个 re-read、backup、merge、verify、delete 流程。
- canonical metadata 按 updated/completed 时间与 `_id` 确定；事件按 event ID/legacy hash 求并集。
- 无事务环境使用两阶段 operation marker；备份和审计记录先于任何删除。
