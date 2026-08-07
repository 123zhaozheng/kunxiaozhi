# Implementation Plan

1. 提取候选扫描和 task/heartbeat 判定。
2. 接入 startup/scheduled cleanup 与配置。
3. 实现 CAS 状态更新和审计/指标。
4. 删除旧 read-path helper 或限制为无副作用诊断。
5. 添加 grace、活跃 heartbeat、终止状态、无终止事件和并发竞态测试。

## Review 补充（review 之后确认）

- **读路径前置已满足**：E1（从读路径移除 dedup/reconcile 副作用）已被 Child A 达成（读路径不再调用它们），本任务无前置阻塞，可直接启动。
- **死代码/陷阱**：旧的 `reconcile_stale_running_traces`（`trace_storage.py`）仍在，仅按"含 done/error 事件"翻转 `running→completed`，不查 heartbeat/task_status、无 CAS、无 grace period。实现 E 后删除该实现，替换为 heartbeat/task-aware 版本；同步更新 `tests/infra/session/test_trace_stale_reconcile.py` 中断言旧行为的测试。
