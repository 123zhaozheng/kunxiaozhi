# Research: trace persistence schema and direct event-store cutover

- Query: 核对 legacy `traces` 数组、immutable `trace_events`、读写模式和直接切换到 `event_store` 的安全边界。
- Scope: mixed（内部代码/规范；依赖版本仅作实现背景）
- Date: 2026-08-07

## Findings

### 1. Legacy `traces` 集合

- 集合由 `settings.MONGODB_DB`（默认 `agent_state`）和 `settings.MONGODB_TRACES_COLLECTION`（默认 `traces`）决定：`src/kernel/config/base.py:179-186`、`src/infra/session/trace_storage.py:171-178`。
- 设计注释给出的文档形状是：`trace_id`、`session_id`、`run_id`、`agent_id`、`user_id`、`events[]`、`event_count`、`started_at`、`updated_at`、`completed_at`、`status`、`metadata`；数组元素通常有 `event_type`、`data`、`timestamp`，新写入还带 `event_id` 和 session 全局 `seq`：`src/infra/session/trace_storage.py:7-24`、`553-564`、`src/infra/session/dual_writer.py:120-137`。
- `create_trace` 使用 `update_one({trace_id}, {$setOnInsert: doc}, upsert=True)`，初始 `events=[]`、`event_count=0`、`status="running"`；已有文档只补缺失 metadata，并校验 session/run identity，不覆盖已有值：`src/infra/session/trace_storage.py:525-565`、`567-635`、`655-685`。
- 正常 Presenter 路径是 `DualEventWriter.write_event`：事件先写 Redis Stream，再放入 `_mongo_buffer`；flush 时按 trace 分组，用 `UpdateOne({trace_id}, {$push: {events: {$each: batch, $slice: -max_events}}}, $inc event_count, $set updated_at, upsert=False)` 写 legacy 数组：`src/infra/session/dual_writer.py:266-335`、`391-453`、`108-162`。`max_events` 来自 `SESSION_MAX_EVENTS_PER_TRACE`，默认 10000：`src/infra/session/dual_writer.py:57-60`。
- 因而 `$slice` 只保留尾部，而 `event_count` 继续累计全部写入数；当 `event_count > len(events)` 时可以确认前缀已被丢弃，但旧数组即使没有这个差异也不能证明历史完整。`backfill_legacy_events` 明确把此情况报成 `legacy_events_truncated`：`src/infra/session/trace_storage.py:331-383`。
- 还有直接 legacy `TraceStorage.append_event` 路径，使用 `$push` + `$inc`，本身不加 `$slice`：`src/infra/session/trace_storage.py:691-735`；它构造的事件只有 `event_type/data/timestamp`，若传 `session_id` 才附 `seq`（没有稳定 `event_id`）。正常 Presenter 当前使用 `DualEventWriter`，不是这个方法。
- 完成时更新 `status`、`completed_at`、`updated_at` 和可选 metadata：`src/infra/session/trace_storage.py:805-848`。为补 token usage，legacy `complete_trace` 还会通过 aggregation pipeline 用 `$indexOfArray`、`$concatArrays`、`$slice`、`$size` 把合成的 `token:usage` 插到 `done` 前并递增 `event_count`：`src/infra/session/trace_storage.py:740-803`。
- 现有 legacy 索引：`session_status_started_at_idx(session_id,status,started_at)`、`session_run_status_idx(session_id,run_id,status)`、`started_at_idx(started_at desc)`、`session_started_at_desc_idx(session_id,started_at desc)`、`status_merged_idx(status,metadata.merged)`；先做 `trace_id` 重复预检，零重复后才创建唯一 `trace_id_unique_idx(trace_id)`：`src/infra/session/trace_storage.py:428-473`。
- session 级 seq 计数器在固定 `session_events_counter` 集合，文档 `_id=session_id`，`next_event_seq` 原子 `$inc: {seq:1}` + upsert：`src/infra/session/trace_storage.py:181-188`、`385-399`。批量 flush 会按 session 一次 `$inc` 预分配连续区间：`src/infra/session/dual_writer.py:455-489`。
- legacy history 的 `completed_only=True` 条件是 `status != "running"`，会接受任何非 running/异常未知状态；排序按兼容复合键（无 seq 在 legacy bucket，之后 seq、timestamp、trace_id、event_id、ordinal）：`src/infra/session/trace_storage.py:1334-1455`、`src/infra/session/history_cursor.py:89-108`。legacy page 永远返回 `history_complete=False`，因为数组尾端不是耐久完整性的证明：`src/infra/session/trace_storage.py:1491-1552`。
- `ENABLE_EVENT_MERGER=True` 时，索引 ready 后会启动后台合并器；它对已完成 trace 的 legacy `events` 做可合并事件压缩并可能重写 `events`、`event_count`、`metadata.merged`：`src/infra/session/trace_storage.py:401-426`、`509-523`、`src/infra/session/event_merger.py:243-257`、`328-365`。这不会补回已经被 `$slice` 或进程崩溃丢掉的源事件。

### 2. 新 `trace_events` 集合

- 集合名可配置为 `MONGODB_TRACE_EVENTS_COLLECTION`，默认 `trace_events`；配置只拒绝空名、`$` 开头、NUL 和 `system.` 前缀：`src/kernel/config/base.py:65-71`、`100-111`、`src/infra/session/trace_storage.py:190-198`。
- 一事件一文档。`DualEventWriter._build_trace_event_documents` 产生字段：`event_id`、`session_id`、`trace_id`、`run_id`、`seq`、`timestamp`、`event_type`、`data`；`write_trace_events` 额外默认加入 `created_at`：`src/infra/session/dual_writer.py:165-194`、`src/infra/session/trace_storage.py:217-241`。`run_id`/`seq` 可为 `None`（例如没有 session seq 的兼容数据）。
- 唯一幂等键严格是 `(session_id, trace_id, event_id)`，索引名 `session_trace_event_unique`；同一身份 retry 使用 `$setOnInsert`，不会改写已有 seq/payload：`src/infra/session/trace_storage.py:200-215`、`217-241`。同一 `event_id` 在不同 trace/session 并不冲突。
- 其他索引：`session_seq_event_idx(session_id,seq,event_id)`、`session_run_seq_event_idx(session_id,run_id,seq,event_id)`、`trace_type_timestamp_idx(trace_id,event_type,timestamp)`：`src/infra/session/trace_storage.py:203-208`。
- `event_id` 在进入 Redis/缓冲前生成（`event_id or uuid.uuid4().hex`）；dual/event_store 写模式下 seq 也在缓冲前通过 `next_event_seq(session_id)` 分配，随后同一个 `(event_id,seq)` 随 Redis、legacy dual 数组和 immutable 文档传播：`src/infra/session/dual_writer.py:282-313`。旧 legacy 数组无稳定 ID 时，backfill/merge 用 `legacy_event_id(trace_id, trace-array-ordinal,event_type,timestamp,data)` 的 SHA-256 结果（前缀 `legacy-`）：`src/infra/session/trace_storage.py:322-329`、`331-372`、`src/infra/session/dual_writer.py:868-885`。
- `traces` 仍是 metadata/lifecycle 来源，immutable 文档不复制 status。`completed_only=True` 时先从 `traces` 找同 session 且 `status in {completed,error}` 的 trace_id，再用 `$in` 筛 `trace_events`；未知、缺失或 running metadata 一律不可见：`src/infra/session/trace_storage.py:243-293`。这比 legacy 的 `$ne: running` 更 fail-closed。
- dual/event_store flush 先写 immutable 文档，再按实际 `upserted_ids` 增加 `traces.event_count`；metadata 更新失败只告警，不删除已落盘事件。event_store 不生成 legacy `$slice` 操作：`src/infra/session/dual_writer.py:491-545`、`516-531`。因此 `event_count` 是可修复投影，不是 immutable 事件的唯一计数。

### 3. WRITE/READ 行为矩阵

| 配置 | 写入行为 | 读取行为/完整性 |
|---|---|---|
| `WRITE=legacy` | Redis + legacy `traces.events`；flush 使用 `$slice=-SESSION_MAX_EVENTS_PER_TRACE`。没有 immutable 写入。seq 分配失败时 legacy 仍可能继续但会缺 seq：`dual_writer.py:455-489`。 | `READ=legacy` 只读 legacy；`completed_only` 排除 `running`，但接受其他状态；page `history_complete=False`。 |
| `WRITE=dual` | Redis + `trace_events` 幂等写；同时写 legacy 数组（仍受 `$slice`），同一 event_id/seq 共享。 | `READ=merge` 读两边，按 stable ID 去重；immutable 覆盖同 ID 的 legacy；旧数组事件使用与 backfill 一致的 trace-array ordinal ID，再按兼容复合键排序。若只读 `legacy`，仍只能看到 retained array。`dual_writer.py:853-899`。 |
| `WRITE=event_store` | Redis + immutable `trace_events`；不 append/rewrite legacy 数组，避免 `$slice`。仍在 `traces` 建/校验 metadata、完成 status，并修复 `event_count` 投影：`dual_writer.py:491-545`、`584-609`。 | `READ=event_store` 只读 immutable；`completed_only` 必须 join 到明确 completed/error 的 `traces` metadata，旧 legacy-only 历史不可见。event-store page 的 retained immutable 末端可返回 `history_complete=True`（`not has_more`）：`dual_writer.py:901-958`、`trace_storage.py:295-320`。 |
| 任一 WRITE + `READ=merge` | merge reader 会尝试读 immutable + legacy；如果当前 writer 仍 legacy，immutable 侧可能为空，仍能显示 legacy retained 数据，但完整性标记仍为 false。 | merge 不是 backfill；它不能恢复已从 legacy 数组丢弃的 prefix。 |
| 任一 WRITE + `READ=event_store` | 只显示已有 immutable 文档；运行中/未知 metadata 的事件在默认 `completed_only=True` 下隐藏。 | 适合切换后的 authoritative path，不适合未 backfill 的旧库。 |

所有模式的 `completed_only=False` 都允许按 event 文档读取，但 event-store 仍应用 session/run/event-type filters；只是在不做 terminal metadata join 的情况下读取。`READ=event_store` 和 `READ=merge` 的分支分别在 `src/infra/session/dual_writer.py:844-899`；API/Presenter 默认调用 `completed_only=True`（如 `src/api/routes/session.py:320-328`）。

### 4. 是否能直接把两个环境变量设为 `event_store`

语法上可以：`TRACE_EVENT_WRITE_MODE` 允许 `legacy|dual|event_store`，`TRACE_EVENT_READ_MODE` 允许 `legacy|merge|event_store`，启动校验不会拒绝 `event_store`：`src/kernel/config/base.py:78-98`。但“能启动”不等于“已有库安全切换”。

直接 step-4 cutover 仅在以下情形可视为安全：

1. **全新空库**：没有需要保留的 legacy traces，或业务明确接受零历史；部署支持 immutable 写入的代码后先确保 `traces` 和 `trace_events` 索引 ready，再启用两个 `event_store`。第一批写入会通过 `DualEventWriter` 的 readiness gate 创建 metadata，immutable event indexes 也会在 flush 前检查：`dual_writer.py:240-264`、`391-431`、`491-499`。
2. **既有库但明确不需要历史**：同上，必须接受旧 `traces.events` 将对 event-store 读取不可见；保留数据库不代表新 reader 会 fallback。
3. **既有库且完成、核验了 backfill**：对每个 trace 运行 `backfill_legacy_events(dry_run=True)` 统计，再 apply；必须没有 errors/truncated coverage，immutable 数量/ID/排序与 retained source 核对，`trace_events` unique/index readiness 成功，且 metadata 的 session/trace/run identity 可 join。backfill 是 source-preserving、幂等 `$setOnInsert`，不会恢复已被旧 writer 丢弃的事件：`trace_storage.py:331-383`。

切换前还必须满足：

- **无 in-flight writer，或先完成排空/冻结**：旧进程可能在切换后继续只写 legacy；event_store reader 看不到它们。发布顺序应先部署支持 dual/event_store 的代码，再在所有 writer 实例一致后切 read mode；不能出现 `READ=event_store` 与仍 `WRITE=legacy` 的混合窗口。
- **Redis/内存 buffer 已 flush**：`write_event` 先写 Redis 后进 Mongo buffer，Redis TTL 只是实时/重放缓存，不是 `trace_events` backfill 源；旧进程退出/崩溃前要调用 `flush_mongo_buffer`（运行时 shutdown hook：`src/infra/runtime_services.py:44-47`），并确认没有 scheduled flush、重试 batch 或失败 batch。当前失败会把 batch 按原顺序放回并 re-arm flush，而进程崩溃时内存 batch 无法恢复：`dual_writer.py:391-453`、`527-569`。
- **索引/ready 已验证**：legacy 全局 `trace_id` 重复预检为空，`traces` 唯一索引 ready；`trace_events` 四个索引（尤其 compound unique）ready。任意 trace readiness 失败都会 fail-closed，不应把“应用已启动”当成可写。
- **重复 trace 已治理**：legacy `trace_id_unique_idx` 是全局唯一；历史重复会阻止 readiness/新写入。重复迁移必须使用显式 CLI dry-run/apply/confirm、备份、校验和回滚，不能靠 history GET 或 readiness 自动清理：规范 `.trellis/spec/backend/trace-duplicate-migration.md`，实现 CLI `src/infra/session/trace_migration.py:730-757`。
- **active run 已处理**：不要在运行中的 trace 尚未 flush/complete 时 backfill 或切换 reader；否则 `completed_only` 会暂时隐藏 immutable events，旧 writer 的尾批也可能只在 legacy。可以先 drain、等待 terminal status，再核对事件覆盖；若必须不停机，应先 dual-write 并继续 `READ=merge`，待覆盖验证后再 read cutover。

### 5. 直接切换现有 DB 会隐藏/不可恢复什么

- 任何只存在于旧 `traces.events`、尚未 backfill 的事件，在 `READ=event_store` 下完全不可见；`traces` metadata 仍可能显示 trace/status/event_count，但 reader 不会从 metadata 的 legacy 数组补读。
- legacy `$slice` 已丢弃的 prefix、buffer 满时旧版本曾静默淘汰的事件、Mongo 16 MB 失败前未持久化的尾部、进程崩溃时尚未 flush 的内存 batch，均不能由当前 backfill 恢复。backfill 只能复制当前 retained array，并在 `event_count > len(events)` 时明确 `complete=False`：`trace_storage.py:334-383`。
- 已被 `EventMerger` 合并/重写的旧数组只保留合并后的事件形状；即使 `event_count` 与数组长度相等，也不等于最初每条流式事件都仍可重建。immutable store 若之前未 dual-write，则没有第二份事实来源。
- 没有 terminal `traces` metadata 的 immutable 事件（unknown trace、session/run identity 不匹配、仍 `running`）在默认 `completed_only=True` 下故意不可见；这通常是安全过滤而非删除，完成 metadata 后才可能可见。若旧 metadata 文档被错误重复/选错 session/run，event-store join 也可能把历史隐藏或归属错误。
- `TRACE_EVENT_BACKFILL_ENABLED` 虽在 settings 中声明为默认 `False`，代码搜索中没有任何读取/调度它的 wiring；目前真正可调用的是 `TraceStorage.backfill_legacy_events(...)`，没有对应 trace-event backfill CLI/API。不能仅设置该 flag 就认为完成 backfill。现有 `python -m src.infra.session.trace_migration` 是 duplicate-trace 维护 CLI，不是 event backfill CLI：`src/kernel/config/base.py:65-71`、`src/infra/session/trace_storage.py:331-383`、`src/infra/session/trace_migration.py:730-757`。

### 6. External references / implementation versions

- 代码依赖声明 `pymongo>=4.10.0`、`motor>=3.7.1`：`pyproject.toml:31-46`；`uv.lock` 当前解析到 Motor 3.7.1、PyMongo 4.16.0（锁文件对应条目 `uv.lock:2271-2279`、`3366-3374`）。Mongo 操作均通过 Motor/PyMongo async collection wrapper。
- 项目内部 rollout contract：`.trellis/spec/backend/trace-event-storage.md`（模式、幂等、backfill、rollback）；`.trellis/spec/backend/trace-uniqueness-readiness.md`（全局 trace 唯一性/ready）；`.trellis/spec/backend/trace-duplicate-migration.md`（重复 trace 显式迁移）；`.trellis/spec/backend/session-history-pagination.md`（legacy 截断不等于完整）。

## Caveats / Not Found

- 未在代码中找到 `TRACE_EVENT_BACKFILL_ENABLED` 的消费者、自动 backfill startup job 或 trace-event backfill CLI；需要运维显式调用 storage method/外部脚本并保存覆盖审计。
- `TraceStorage.write_trace_events` 本身不调用 `ensure_event_indexes`；当前生产 flush 通过 `DualEventWriter._do_flush` 调用 readiness，但直接调用 storage method 的维护脚本必须自行先确保 indexes。
- `get_event_store_session_events_page` 当前先把匹配事件全部读入内存再分页（`trace_storage.py:295-320`），它没有 Mongo cursor-level `limit+1`；这不改变可见性结论，但超大 session 的资源风险仍需另行关注。
- `DualEventWriter.write_event` 的 `event_id` 参数是可选稳定输入；Presenter 默认不传，因此每次调用生成 UUID。只有上游重试显式复用同一 ID 才能触发 immutable 幂等；重新生成 ID 的“重试”会被视为新事件。
- legacy `TraceStorage.append_event` 和 `_ensure_token_usage_event` 仍可能产生无 `event_id` 事件；merge/backfill 依赖 ordinal/hash fallback，不能把这些 fallback ID 当作原始生产端 ID。
