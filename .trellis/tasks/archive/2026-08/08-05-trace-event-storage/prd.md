# 无损 trace 事件存储

## Goal

以不可变 `trace_events` 集合消除 `$slice`、缓冲丢弃和 Mongo 单文档 16MB 导致的历史事件丢失。

## Dependencies

依赖 `08-05-session-history-cursor` 已定义 event identity、排序和 cursor 契约。

## Requirements

- 每个新事件持久化为独立文档，带稳定 `event_id`、session/trace/run、seq、timestamp 和 payload。
- 写入幂等且可重试；事件成功后元数据更新失败可被修复，不能反向丢事件。
- 提供必要唯一/查询索引、dual-write/dual-read feature flag 和 legacy backfill 能力。
- 缓冲压力不得静默丢弃事件；失败必须重试、持久化或显著报警。

## Acceptance Criteria

- [x] 超过 10000 事件和大 payload 测试无数据丢失或 16MB 单文档失败。
- [x] 重试相同 event ID 不产生重复。
- [x] dual-read 在迁移期不重不漏，校验后可切换新集合。
- [x] rollback 可恢复 legacy 读取且不删除新事件。
