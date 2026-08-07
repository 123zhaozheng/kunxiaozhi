# 完善会话历史持久化与 trace 一致性

## Goal

彻底解决长 TeamAgent/SOP 会话刷新后历史回复缺失、重复 trace 文档和读路径危险自愈问题，使历史在超大事件量、并发 trace 创建和异常终止场景下完整、可观测且不会误删数据。

## Confirmed Facts

- 旧的 1000 事件读取默认值是会话 `994c3a6b-04e6-484d-951d-2f72877d7869` 刷新后回复缺失的直接原因。
- 将前后端上限改为 10000 只移动阈值；最大值 probe、前端单次读取和写入 `$slice` 仍会静默丢失或隐瞒截断。
- 单 trace 数组受 MongoDB 16MB 文档上限约束，不能承载无损长历史。
- 当前读路径去重会永久删除文档且不合并独有事件；stale-running 修正也不应由普通 GET 触发。
- 多个 Presenter 会为同一 `trace_id` 调用 `create_trace`，而唯一索引初始化未被启动流程真正等待。

## Requirements

- R1：session 与 public share 使用统一的游标分页契约，准确返回 `has_more`、`next_cursor`、`history_complete` 和排序版本。
- R2：前端循环拉取全部页面；中断或失败时保留已加载内容并在内部元数据中明确标记历史不完整，不向用户常驻展示泛化警告。
- R3：普通历史读取严格只读，不删除 trace、不修改 trace 状态。
- R4：新事件写入不可因固定 `$slice`、Mongo 单文档容量或缓冲区溢出而丢失。
- R5：事件具备稳定 `event_id` 和确定性全局排序键，分页边界不重不漏。
- R6：trace 索引初始化必须可等待、可重试、可观测；`create_trace` 并发幂等并验证 session/run 一致性。
- R7：存量重复 trace 通过 dry-run 默认的显式管理工具处理，包含租约、备份、确定性合并、校验、审计与回滚。
- R8：stale-running 恢复移出读路径，结合 grace period、任务状态、run heartbeat 和 CAS 更新保守执行。
- R9：兼容现有调用方和 legacy array 数据，采用可回滚的分阶段 dual-read/backfill/cutover。
- R10：覆盖超 10000 事件、游标边界、并发创建、索引失败、迁移回滚和活跃 run 竞态。

## Acceptance Criteria

- [x] 超过 10000 个事件的会话刷新后完整重建，事件无缺失、无重复、顺序稳定。
- [x] session/share API 和两个前端消费路径使用同一分页与完整性语义。
- [x] 新事件以不可变事件文档持久化，不再依赖 trace 内嵌数组作为唯一事实来源。
- [x] `get_session_events` 无数据库写副作用。
- [x] 并发创建相同 trace 最终只有一个权威文档，索引未就绪时不会静默接收不安全写入。
- [x] 存量重复数据可 dry-run、备份、合并、校验和回滚，不丢失任一独有事件。
- [x] 活跃 trace 不会被 stale 恢复误标为终态。
- [x] 每个子任务通过独立 `trellis-check`，父任务完成跨层集成检查。

## Task Map

- A `08-05-session-history-cursor`：共享游标契约、纯读取与前端分页。
- C `08-05-trace-uniqueness-readiness`：索引就绪和幂等创建；可在 A 后独立完成。
- B `08-05-trace-event-storage`：不可变事件集合；依赖 A 的事件身份与排序契约。
- D `08-05-trace-duplicate-migration`：存量重复迁移；依赖 B 的事件身份和 C 的索引 gate。
- E `08-05-trace-stale-recovery`：生命周期恢复；依赖 A 已移除读路径修正。

## Out of Scope

- 不重构 TeamAgent/SOP 的业务编排或减少其事件数量。
- 不在自动化测试或部署过程中直接修改生产数据库。
- 不立即删除 legacy trace 数组；在验证和保留期内作为回滚来源。

## Key Decisions

- 使用 opaque composite cursor，不使用 offset；新事件以 `event_id` 作为最终稳定 tie-breaker。
- 新建一事件一文档的 `trace_events` 集合，`traces` 保留元数据和 legacy 兼容。
- 重复 trace 清理是显式运维动作，不是读取副作用。
- stale 恢复复用现有 task status/heartbeat 事实来源，不仅凭终止事件判断。

## Constraints

- 保留当前大量用户未提交改动，实施代理不得回退或覆盖无关文件。
- rollout 必须向后兼容、可 feature flag 回滚，并在切换前验证 backfill 覆盖率。
- 生产迁移必须由运维显式执行；默认 dry-run。
