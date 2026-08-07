# Implementation Plan

1. 定义 plan/audit/backup schema 和 CLI 参数。
2. 实现扫描、租约、活跃检查与 dry-run 输出。
3. 实现确定性 merge、校验和两阶段 apply。
4. 实现 rollback 与幂等重跑。
5. 添加跨 session、相同计数、独有事件、竞态和失败注入测试。

## Review 补充（review 之后确认）

- **死代码/陷阱**：工作区仍存在破坏性的 `dedup_duplicate_traces`（`trace_storage.py`，无调用方），删除时**不带 `session_id` 作用域**、只保留 event_count 最大的文档。当前无调用方，但 `tests/infra/session/test_trace_stale_reconcile.py` 仍把它当期望行为断言——实现 D 时必须删除该函数及其破坏性测试断言，替换为本次规划的安全迁移能力。
- **D 是 C 的硬依赖**：C 的唯一索引 preflight 在库里存在重复 trace 时永远 fail-closed。D 落地并清理重复后，唯一索引才能创建。本任务的验收应包含"迁移后重复归零、可安全建唯一索引"。
