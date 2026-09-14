# Analytics 缺陷修复审核：非破坏性契约与向后兼容

**审核角色**：`review-contract`  
**分支**：`fix/analytics-correctness`（HEAD `816dcaec`）  
**基线**：`main`（`075e9041`）  
**范围**：Mongo 写入语义、旧文档字段回退、完成哨兵、API 参数兼容、前端类型兼容、索引与启动时机、旧版本回滚安全。已避开 `review-api.md` 与 `review-frontend.md` 已报告的峰值过滤、模型钻取、usage-by-user 降级和活跃用户钻取口径问题。

## 结论

**NEEDS_CHANGES**。

本审核范围发现两项 MAJOR 和一项 MINOR。它们不属于测试是否通过的问题，而是会使回填结果反复被视为未完成、使历史库上的唯一性约束可能根本未建立，或使大库首次启动在 readiness 前长时间等待。

## 验证记录

- 定向运行：`uv run pytest tests/infra/test_analytics_snapshot_immutable.py tests/infra/test_analytics_backfill.py tests/infra/test_analytics_daily_freeze.py -q`，结果 **21 passed**（4 个既有 Pydantic deprecation warnings）。
- 全仓生产代码搜索 `analytics_daily_snapshot`：读取/聚合集中在 `src/infra/analytics/snapshot.py`；`backfill.py` 只写入该集合，`storage.py` 负责 collection 入口和索引，未发现本次未改动的第二套业务读取路径。
- 全仓及 analytics 目录搜索未发现本次改动新增的 `update_many`、`delete_many`、`drop`、`drop_index`、`ReplaceOne`、`UpdateMany`、`DeleteMany` 或动态拼接 Mongo 写方法。

## 发现

### MAJOR-1：回填成功后没有写入完成哨兵，回填数据仍会被每次历史查询当成未冻结

**位置**：`src/infra/analytics/backfill.py:454-456`、`src/infra/analytics/backfill.py:553-559`；对照 `src/infra/analytics/snapshot.py:255-268`、`490-505`。

`_backfill_snapshot_for_day()` 在 `not docs and not session_docs` 时直接返回 `True`（`454-456`）；有数据时，`snapshot_col.bulk_write(ops)` 成功后也直接返回 `True`（`553-559`）。两条成功路径都没有插入 `{date: target_date, user_id: "__snapshot_complete__"}`。但 `read_or_freeze()` 的完整性判断只查询该哨兵（`255-268`），真正写哨兵的代码只存在于 `_freeze_dates()`（`490-505`）。

**触发条件/影响**：启动回填成功处理历史日期后，第一次 API 查询仍会把这些日期放入 `truly_missing`，再次执行 traces/sessions 全量聚合和逐日锁流程；在 Redis 不可用时，回填虽然已经写入行，却始终没有标记，后续每次历史请求都可能走实时回退。空日期也无法被标记为已冻结。数值通常不会被覆盖（写入仍是 `$setOnInsert`），但回填的完成契约不成立，历史大库会重复承受昂贵扫描，并且 rollout 所说“每个已完整冻结日期都有标记”不成立。

**建议修法**：回填快照 bulk write 成功后，用精确的 `{date, user_id: "__snapshot_complete__"}` filter 和 `$setOnInsert` + `upsert=True` 写入哨兵；空日期也必须写哨兵。哨兵写失败时返回 `False`，让该日期进入 `failed_dates`，不能把它当成已完成。

### MAJOR-2：新增唯一快照索引没有历史重复键预检，旧库存在重复行时索引会失败且被静默吞掉

**位置**：`src/infra/analytics/storage.py:230-233`，异常吞掉于 `src/infra/analytics/storage.py:187-189`、`253-254`；启动调用位于 `src/api/main.py:370-372`、`406-415`。

本次首次为 `analytics_daily_snapshot` 创建复合唯一索引 `date + user_id + persona_preset_id + agent_id`。该集合在基线代码中没有对应唯一索引，而旧版曾经存在按日期/筛选写入和锁粒度不足的路径；因此不能假设线上历史文档没有相同复合键。Mongo 在已有重复键时会拒绝创建唯一索引。`ensure_indexes()` 用一个总 `try/except` 包住全部索引调用，失败后只记录 warning 并正常返回；启动器随后仍记录 `AnalyticsStorage indexes initialized`，不会将该索引失败暴露为 readiness 或健康状态。

**触发条件/影响**：历史库只要有一个重复复合键，唯一索引就建不起来；后续 `$setOnInsert`/upsert 缺少数据库唯一约束兜底，重复快照仍可能被写入，聚合读端会把重复行累加，重新引入历史数字膨胀。该问题不能靠应用启动时删除或合并历史文档解决，因为用户明确禁止破坏性迁移。

**建议修法**：在创建唯一索引前做只读重复键预检，并把发现重复或建索引失败作为显式、可观测的部署/readiness 条件，而不是记录 warning 后伪装成功；在不改写历史文档的约束下提供人工处置/阻断流程后再启用唯一索引，或改用不依赖未经清理历史数据的安全索引与应用锁方案。索引应显式命名，避免既有同键不同选项的 IndexOptionsConflict 难以诊断。

### MINOR-1：analytics 新索引仍在 API readiness 前同步等待，大集合首次建索引可能阻塞启动

**位置**：`src/api/main.py:406-415`、`src/api/main.py:370-372`；索引调用为 `src/infra/analytics/storage.py:187-251`。

`lifespan()` 在 `await _run_startup_indexes(app)` 后才进入 `yield`；`_run_startup_indexes()` 又直接等待 `_initialize_startup_indexes()` 完成。analytics initializer 会依次 await traces/events、sessions、snapshot 和 activity 的多次 `create_index()`。Mongo 的 `background=True` 只降低建索引对读写的影响，不会让 Motor 的 `create_index` 调用立即返回，因此在生产大集合上，新增事件、persona、agent 和 snapshot 索引的首次创建会延迟应用进入 readiness。

**触发条件/影响**：新环境或已有大 traces/sessions 集合第一次部署时，API 可能在索引构建期间无法提供服务，滚动发布会出现长时间不可用；这与 rollout 声明“backfill/daily-freeze 不阻塞启动”不同，不能推导出索引路径也不阻塞启动。并且 `ensure_indexes()` 的总异常吞掉会让部分索引失败后仍继续启动，形成“启动成功但查询性能/唯一性不满足”的隐性状态。

**建议修法**：将 analytics 索引初始化改为启动后的独立、可观测任务并支持重试，或把建索引纳入部署前置步骤；readiness/健康状态应区分“服务可用”和“analytics 索引完整”，至少不能在索引失败后输出初始化成功的假状态。

## 已核对且未发现问题的声明

- **快照业务行不覆盖历史文档**：`snapshot.py:483` 的业务行使用 `bulk_write` 中的 `$setOnInsert` + `upsert`；`snapshot.py:490-505` 的哨兵也只用 `$setOnInsert`；`backfill.py:509-550` 的两类快照 `UpdateOne` 同样只用 `$setOnInsert` + `upsert`。backfill 状态文档使用 `$set` 是进度/失败状态的可变控制记录，不是历史快照。
- **新增 ID 数组缺失时回退旧整数语义**：`snapshot.py:79-89` 记录字段是否存在，`127-134` 的 `_visible_metric()` 在旧行缺少数组时回退 `active_sessions`/`new_sessions` 并打 debug；`137-205` 的 `_merge_metric_items()` 对混合版本和 user×persona 复合键保留旧整数，不会把旧行清零。
- **新增字段读取兼容**：`analytics_backfill_state.failed_dates` 在 `backfill.py:147-149` 缺失时回退为空列表；旧快照缺少 `active_session_ids`、`new_session_ids`、`last_active_at` 时当前读取端均有默认/整数回退，Pydantic analytics 响应字段也有默认值或 nullable 定义。
- **完成哨兵的当前版本排除**：`snapshot.py:593-603` 的 `base_match` 在所有当前维度聚合前排除 `user_id == "__snapshot_complete__"`；全仓生产读取搜索没有发现其他快照集合读路径。旧版本虽然没有该排除，但其旧 snapshot merge 只会把哨兵视为零值分组；旧版 `/usage/by-user` 仍走 traces 实时聚合，不会把哨兵返回成真实用户明细，也不会改变合计数字。
- **新增 API 参数的省略行为**：三个环图端点的 `persona_preset_id`、`agent_id`、`role_id` 都是 `Optional[str] = Query(None)`；`/runs/list` 的 `model` 是 `Optional[str] = Query(None)`；旧调用方不传时仍走原有无筛选调用。`/users/list` 和用户 CSV 的 `first_use` 默认 `False`，省略时保持原有全部活跃用户行为；manager/storage 新增参数均追加在原有可选参数之后。
- **前端灰度响应兼容**：本次没有新增必须存在的 analytics 响应字段；`AnalyticsListFilters.firstUse`、`listRuns` 的 `model` 选项均为可选。`UsageByUserItem.last_active_at` 已是 nullable 消费；被删除的 `UsageByPersonaItem/Response` 仅为仓内无引用的死类型，运行时 `usage/by-persona` API 和后端 schema 未删除。
- **回滚安全**：旧版本只读取快照中的整数 `active_sessions`/`new_sessions`，不会解包新增 ID 数组；新增数组是追加字段，未知字段不会导致旧代码反序列化失败。哨兵在旧版本聚合中只形成零值分组，不覆盖旧行，也不使旧版 `/usage/by-user` 读到假用户。
- **索引重复/冲突扫描**：未发现 traces/sessions 上与本次新增命名索引相同键的既有生产索引；activity 的两个索引在 `AnalyticsStorage` 与 `ActivityStorage` 中同名同定义，重复调用是幂等的，不是定义冲突。所有本次新增 `create_index` 调用都带 `background=True`。
- **角色筛选与空列表语义**：`storage.py:784-797` 直接按 `users.roles` 查询角色成员；空角色成员列表下游使用 `$in: []`，不会退化成不筛选。
- **无破坏性 Mongo 操作**：本次 analytics 改动没有新增 `update_many`、`delete_many`、drop 或 replace 类操作；activity 的 `$set`/`$addToSet` 是既有日活 upsert 语义，不是对 analytics 快照历史行的改写。

## 复核结论

修复 MAJOR-1、MAJOR-2 后再考虑合并；MINOR-1 至少应补齐索引失败可观测性并决定是否把大集合建索引移出 readiness 路径。并行报告中的 `_insights_peak`、模型 token 钻取、`/usage/by-user` 降级和首卡钻取口径问题不在本报告重复列出。
