# Analytics API / 存储层独立审核报告

审核对象：`fix/analytics-correctness`（HEAD `816dcaec`），基线 `main`（`075e9041`）  
审核范围：API 路由、analytics storage，以及指定的任务/消息写入改动。  
审核结论：**NEEDS_CHANGES**

## 验证记录

- 运行限定回归测试：`uv run pytest tests/api/routes/test_analytics_api_layer_fixes.py tests/api/routes/test_analytics_date_params.py tests/infra/test_analytics_storage_list_filters.py -q`，结果为 **26 passed**。
- 测试通过只能证明现有 mock 场景，不覆盖下述跨边界事件、混合模型 token、快照内部降级等场景。

## 发现

### MAJOR-1：峰值统计没有把消息事件限制在请求日期区间内

**位置：** `src/infra/analytics/storage.py:1712-1745`（重点 `1718-1722`）。

`_insights_peak()` 使用 `usage_facts_stages(filters)[:-1]`，该阶段只按 `traces.started_at` 过滤，并在 `$unwind` 后只匹配 `events.event_type == "user:message"`；没有再匹配 `events.timestamp >= filters.start` 且 `< filters.end`。

因此，trace 在区间内开始但跨到区间外的用户消息会被错误计入本期峰值；反向地，trace 在区间外开始、但消息事件发生在区间内的消息会被漏掉。时区参数 `Asia/Shanghai` 本身已用于 `$dayOfWeek/$hour`，但不能弥补日期边界过滤缺失。

**建议修法：** `$unwind` 后增加对 `events.timestamp` 的半开区间 `$match`（同时继续保留 `events.event_type` 匹配），用请求的 `filters.start/end` 做边界；为跨日 trace 补充“trace 起始日在区间外、消息事件在区间内”的回归测试。

### MAJOR-2：模型钻取的运行 token 数与模型切片不一致，且 `unknown` 切片无法钻取

**位置：** `src/infra/analytics/storage.py:1187-1201`、`1213-1222`。

`list_runs(model=...)` 的查询只要求 trace 存在一个匹配所选模型的 `token:usage` 事件（`$elemMatch`），但返回每个 run 时在 `1213-1222` 遍历该 trace 的**全部** token 事件并累加。因此一个 trace 同时产生模型 A、模型 B 的 token 事件时，点击 A 后该 run 会列出 A+B 的总 token，不能与模型环图的 A 数字对上。

另外，`get_tokens_by_model()` 将缺失模型字段的 token 归入标签 `"unknown"`（`storage.py:397-410`），但 `list_runs(model="unknown")` 的 `$elemMatch` 只匹配字段值等于字符串 `unknown`，不会匹配字段缺失/null 的事件；点击 unknown 环图切片会得到空列表。

**建议修法：** 把“模型归属”抽成同一套表达式：`model_id` 非空时优先，否则使用 `model`，两者都为空时使用 `unknown`；运行明细的 token 求和只累加归属当前 `model` 的事件，并让 `unknown` 查询匹配缺失/null/空值字段。最好用同一条聚合管道同时完成匹配和 token 求和，避免查询与响应再次分叉。

### MAJOR-3：快照层降级时 `/usage/by-user` 可能返回空明细；实时回退还把新建会话固定为 0

**位置：** `src/infra/analytics/storage.py:1539-1579`、`1591-1597`；相关降级路径为 `src/infra/analytics/snapshot.py:712-739`。

`list_usage_by_user()` 只有在 `read_or_freeze()` 抛异常时才调用 `_usage_by_user_persona_realtime()`（`1591-1597`）。但快照层会把 Redis 不可用、快照缺失等情况内部降级为实时计算而不抛出；该实时降级在 `snapshot.py:712-739` 主要补齐趋势/汇总，未构造 `by_user_persona`，于是 `list_usage_by_user()` 把缺失结果当作合法空集合返回。历史日期首次查询而 Redis/冻结不可用时，summary 可能有数字，用户明细和 CSV 却为空。

即使真正进入 `_usage_by_user_persona_realtime()`，其 `1576` 明确写入 `"new_sessions": 0`。快照/summary 不可用时，summary 的实时回退仍会从 `sessions` 统计新建会话；因此用户明细/CSV 的新建会话列与 KPI 不一致。

**建议修法：** 让快照读取结果明确区分“真实空集合”和“快照/聚合不可用”，不可用时在用户明细路径执行完整实时聚合；实时用户×persona 回退必须并行按 `user_id + persona_preset_id` 聚合 `sessions.created_at`，填充 `new_sessions`，不能写死 0。补 Redis 不可用、历史快照缺失、sessions 有新建会话但 trace 无消息三种场景的明细/CSV测试。

## 逐项核对结果

- `/sessions/by-agent`：`src/api/routes/analytics.py:166-182` 已声明 `persona_preset_id/agent_id/role_id`，有筛选时构造 `UsageFilters` 并传入 manager；storage `src/infra/analytics/storage.py:439-500` 读取快照 `by_agent` 的 `active_sessions`，参数确实下沉。
- `/sessions/by-persona`：`src/api/routes/analytics.py:193-210` 已声明并下沉；storage `src/infra/analytics/storage.py:505-520` 读取快照 `by_persona` 的 `active_sessions`，参数确实下沉。
- `/tokens/by-model`：`src/api/routes/analytics.py:219-233` 已声明并下沉；storage `src/infra/analytics/storage.py:379-427` 将共享 usage facts 筛选接入 token 聚合。参数下沉成立，但模型钻取明细存在 MAJOR-2 的归属/求和不一致。
- 两个会话环图：storage `src/infra/analytics/storage.py:448-500` 统一消费 `read_or_freeze()` 返回的 `by_agent/by_persona.active_sessions`，没有保留旧的 sessions.created_at 第二通路；正常快照路径与 summary 的 `active_sessions` 同源。
- `_session_user_ids_for_role()`：`src/infra/analytics/storage.py:784-797` 直接查询 `users` 的 `roles` 字段，不再依赖区间内新建会话；返回空列表时 `UsageFilters.role_user_ids=[]`，后续 `$in: []` 表示无人而不是不筛选，未发现该处越权放大。
- `/usage/by-user` 正常快照路径：`src/infra/analytics/storage.py:1591-1614` 读取 `by_user_persona`，先过滤 `user_messages <= 0`（完成标记行不会进入），再按 `user_messages desc → tokens/total_tokens desc → user_id → persona_preset_id` 排序后分页，`total=len(raw_items)` 与排序集合同源。该正常路径成立，但快照内部降级/实时回退不成立，见 MAJOR-3。
- `first_use`：`src/api/routes/analytics.py:595-626`、`634-663` 均为可选 `False`，并传入 manager/storage；`storage.py:952-974` 以 activity 的首次消息日收窄候选，参数有效。默认不传仍为旧的全部活跃用户列表。
- `model`：`src/api/routes/analytics.py:709-719` 为可选 `None` 并传入 `manager.list_runs()`，参数确实到达查询；但查询与 token 输出口径不一致，见 MAJOR-2。
- 日期与输入边界：`src/api/routes/analytics.py:48`、`66-88` 捕获 `9999-12-31` 溢出并限制最多 366 天；分页/Top-N 仍有原有 `ge/le` 约束。未发现本批新增参数导致明显越权或非法分页输入问题。
- RBAC：上述新增 analytics 端点仍使用 `Depends(require_permissions("settings:manage"))`；没有因新增筛选参数移除权限依赖。
- 消息活跃度写入：正常启动/排队路径中，`chat.py`、`task/manager.py`、`task/executor.py` 使用幂等的日活跃 upsert helper，未发现同一正常路径重复写入会导致计数膨胀的问题；`record()` 的 `$addToSet` 也保持日粒度去重。

## 最终结论

当前实现不能批准合并：至少应修复 MAJOR-1（峰值边界）、MAJOR-2（模型钻取口径）和 MAJOR-3（用户明细降级/新建会话回退）后，再重新审核。
