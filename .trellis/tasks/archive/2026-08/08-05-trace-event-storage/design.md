# Design

- 新建 event storage 边界和 `trace_events` 集合；`traces` 继续保存元数据。
- 推荐唯一键 `(session_id, trace_id, event_id)`，读取索引覆盖 `(session_id, seq, event_id)` 与 run 过滤。
- 新写入以新集合为事实来源；legacy array dual-write 仅为过渡，不得决定 `history_complete`。
- backfill 为可恢复批次，使用确定性 legacy event ID、覆盖率和 checksum 记录。
- EventMerger、analytics 与 legacy array 消费方必须在切换前审计。
