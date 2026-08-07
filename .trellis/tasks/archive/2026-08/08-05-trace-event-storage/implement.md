# Implementation Plan

1. 增加事件 schema/storage/index 与 feature flags。
2. 改造 DualEventWriter 为幂等新集合写入，移除静默 buffer drop。
3. 实现 dual-read 合并和事件身份去重。
4. 实现 dry-run/backfill 基础能力与覆盖校验。
5. 审计 EventMerger/analytics 消费方。
6. 添加大历史、重试、失败恢复、dual-read 和 rollback 测试。

## Review 补充（review 之后确认）

- **状态与代码不符**：本任务标记 `in_progress` 但工作区**没有任何实现代码**。四条丢事件路径全部原封不动：
  - `dual_writer.py` 的 `$push` + `$slice: -max_events`（buffer 截断）
  - buffer 溢出时丢弃最老一半（静默 buffer drop）
  - `bulk_write` 异常被吞（整批 200 事件丢失）
  - `_ensure_token_usage_event` 用 `$concatArrays` 重建整个 events 数组
- **16MB 硬边界**：cap 10000 时平均每条仅约 1.7KB 预算；平均 5KB 时 ~3350 条即超限。design 已否决"安全的中间截断值"，因此唯一正道是新的逐事件存储（`trace_events`），不是调大 `$slice` 上限。
- **死代码清理**（用户要求）：新集合就绪后，旧的 `$push`/`$slice`/`$concatArrays` 路径及其测试要移除，不留双写死代码。
- 验收目标：`history_complete: true` 只有在 B 落地后才能对健康会话为真——本任务是"完整历史"承诺成立的前提。
