# Implementation Plan

1. 重构索引 readiness 状态、锁和逐索引错误处理。
2. 接入应用 startup/readiness。
3. 增加 duplicate preflight 和安全降级。
4. 将 `create_trace` 改为原子幂等 upsert。
5. 添加并发、失败重试、历史冲突和 metadata 一致性测试。

## Review 修复范围（review 之后补充，须一并完成）

- **HIGH · 写入门禁被绕过**：`create_trace` 的 readiness 门禁（`trace_storage.py` 抛 `TraceWriteUnavailableError`）只挡住 `_ensure_trace` 一条路。`dual_writer._build_mongo_bulk_operations`（`dual_writer.py` 的 `$setOnInsert` upsert 建 trace 文档路径）**不过门禁、不验身份**，缓冲 flush 时照样能绕过唯一性/就绪保证创建 trace。需要让 bulk upsert 走与 `create_trace` 相同的就绪检查与身份校验（幂等语义保持一致）。
- **HIGH · 异常被吞**：`presenter_storage.py` 的 `_ensure_trace` 用 `except Exception` 吞掉 `TraceWriteUnavailableError`，`_trace_created` 未置位、run 继续。需要让写不可用成为可见失败（re-raise 或上游显式处理），而不是静默丢 trace。
- **k8s 探测未接入 `/ready`**：`k8s/kunxiaozhi.yaml` 的 livenessProbe/readinessProbe 都指向 `/health`，fail-closed 的 `/ready` 没接进部署。将 readinessProbe 指向 `/ready`。
- **Minor · `/ready` 无退避重试**：`health.py` 每次探测都无退避重跑整套索引初始化，Mongo 故障时会形成重试风暴。增加退避/合并。
- **Minor · 唯一索引作用域**：unique index 建在裸 `trace_id` 上，若 trace_id 为全局唯一则无碍，但需确认设计意图是否应为 `(session_id, trace_id)`；按 design.md 决定并保持一致性。
- **Minor · metadata 合并 N+1 非原子**：`_merge_trace_metadata_if_missing` 逐 key `update_one`。若工作量小可原子化（如合并到一个 `$set`+`$setOnInsert`），否则在文档里说明。
- **依赖说明**：C 的 duplicate preflight 是**永久阻塞**——只要库里存在重复 trace，唯一索引永远不会创建，readiness 保持 fail-closed。这是设计意图（fail-closed），真正的清重复依赖 Child D，C 自身只保证"有重复时拒绝写/降级"，不负责清除。
