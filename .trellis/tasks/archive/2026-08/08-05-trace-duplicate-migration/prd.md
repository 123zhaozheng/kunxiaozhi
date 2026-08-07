# 重复 trace 安全迁移工具

## Goal

以默认 dry-run、可审计和可回滚的显式工具合并存量重复 trace，不丢失独有事件。

## Dependencies

依赖 B 的事件身份/存储和 C 的唯一索引 gate；完成迁移并验证零冲突后才启用唯一索引。

## Requirements

- 按 `(session_id, trace_id)` 严格分组并获取维护租约。
- 活跃 trace/heartbeat 默认跳过；apply 需要显式确认。
- 备份全部源文档，确定性合并 metadata 与事件并校验 checksum/count。
- 精确删除源 `_id`，记录 operation ID，支持幂等重跑和 rollback。

## Acceptance Criteria

- [x] dry-run 无写入并输出准确计划。
- [x] 合并保留所有独有事件且顺序确定。
- [x] 活跃写入不会与迁移竞态。
- [x] 中途失败可重跑，已应用操作可从备份回滚。
