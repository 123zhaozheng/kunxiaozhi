# 快照与冻结层独立审核报告

审核 stage：`review-snapshot`  
审核分支：`fix/analytics-correctness`（HEAD `816dcaec`），基线 `main`（`075e9041`）  
审核范围：`snapshot.py`、`daily_freeze.py`、`backfill.py`、analytics `manager.py`、`activity_storage.py` 及四个指定测试文件。  
结论：**NEEDS_CHANGES**。

## 验证记录

按要求运行限定测试：

```text
uv run pytest tests/infra/test_analytics_snapshot_immutable.py tests/infra/test_analytics_daily_freeze.py tests/infra/test_analytics_backfill.py tests/infra/test_analytics_daily_activity.py -q
28 passed
```

测试通过不能排除下述回退、并发和完成标记问题；其中一条锁测试本身还存在 mock 不可 await、异常被生产代码吞掉的问题，详见 MINOR-2。

## 发现

### MAJOR-1：冻结失败、Redis 不可用或锁竞争时，读取层可能把部分快照当成完整结果

**位置：** `src/infra/analytics/snapshot.py:243-271`、`:274-516`、`:504`、`:712-714`。

`read_or_freeze()` 仅在快照检查本身抛异常时设置 `snapshot_unavailable=True`。Redis client 创建失败只记录日志而不设置该标志（`:243-250`）；Redis 不可用时，若日期已经存在部分快照行，后续仍直接读取这些行。更严重的是 `_freeze_dates()` 对单日 trace/session 聚合、bulk write 和完成标记写入的异常都在内部记录后返回，调用方不知道哪些日期冻结失败；而 `_merge_results()` 只有在 `skip_historical` 为真或 `historical_items` 整体为空时才走实时回退。只要部分行存在，失败日期就不会回退。

同样的窗口也出现在并发场景：实例 A 正在写某日快照，实例 B 取得不到该日锁后立即继续读取；如果此时 A 已写出部分行但尚未写完成标记，B 会把部分行聚合成历史结果。结果是历史 KPI、趋势和维度明细被低估，且同一日期在不同请求中可能出现“部分快照/实时回退/完整快照”三种口径。

**建议修法：** 让 `_freeze_dates()` 返回成功/失败日期集合，或在 `read_or_freeze()` 冻结后重新按完成标记确认每个日期；任何缺标记日期都不能把已有行视为完整结果。对锁竞争可等待短暂重读完成标记，超时则按日期执行完整实时回退；Redis 创建失败和冻结失败也必须显式进入回退状态，而不是只看 `historical_items` 是否非空。

### MAJOR-2：快照层实时回退的日期归属和跨日去重不正确

**位置：** `src/infra/analytics/snapshot.py:712-714`、`:1065-1159`，重点为 `:1079-1094`、`:1125-1155`、`:1156-1159`。

历史快照不可用时，`_merge_results()` 用完整请求区间的 `filters` 调用 `_compute_realtime_aggregate()`，但同时只传入 `historical_dates`。当请求包含“无历史 trace 的昨天 + 有数据的今天”且快照不可用时，聚合结果只会有今天的日期；兼容分支 `if trace_docs and not any(str(doc.get("_id")) in dates ...)` 随后把这些今天的消息/token 汇总强行标成 `dates[0]`（昨天）。之后今天又会在 `_merge_results()` 中单独实时聚合一次，导致昨天被错误填入今天数据，且总数可能重复计算。

此外，回退趋势先按天计算 `active_sessions`，最后在 `:1156-1159` 逐日求和，没有保留跨日 `active_session_ids` 并集。因此同一会话在两个历史日均发消息时，Redis/快照不可用的回退结果仍会计为 2，违反 A5 的区间级去重要求。该回退函数还只返回 `trend` 和 `active_user_ids`，不会构造 `by_persona`、`by_agent`、`by_user` 或 `by_user_persona`；这也独立确认了已有 `review-api.md` 的 MAJOR-3 所指出的用户明细残缺问题，但这里的上游缺陷不局限于 `by_user_persona`。

**建议修法：** 回退应按每个历史日期构造半开区间并仅聚合该日期，不能用包含今天的完整 `filters` 再用 `dates[0]` 猜日期；删除或严格限制 `_id=None` 兼容分支，至少确认文档确实属于当前日期。回退聚合必须保留各日 ID 集合并在区间总数处取并集；同时补齐所有契约维度，或在回退时调用与正常实时路径等价的完整维度聚合。

### MINOR-1：回填成功的日期没有写完成标记

**位置：** `src/infra/analytics/backfill.py:326-487`，尤其 `:476-487`；标记定义和读取判断在 `src/infra/analytics/snapshot.py:34`、`:253-263`。

`_backfill_snapshot_for_day()` 成功 bulk upsert 快照行后直接返回 `True`，没有插入 `user_id = "__snapshot_complete__"` 的完成标记；空数据日期也同样没有标记。于是回填游标虽然推进，下一次 `read_or_freeze()` 仍会把每个回填日期判断为未完成并再次尝试冻结。正常情况下 `$setOnInsert` 使数字不被覆盖，但这会造成每次读取重复查库；当 Redis 不可用或回填后存在部分写入时，还会与 MAJOR-1 的不完整回退路径叠加。

**建议修法：** 只有 activity 与 snapshot 均成功后，在同一日期写入完成标记；空日期也必须写标记。标记写入失败应让该日期保持失败并进入 `failed_dates`，不能推进为已完成。

### MINOR-2：逐日锁测试没有真正验证成功冻结和完成标记

**位置：** `tests/infra/test_analytics_snapshot_immutable.py:389-423`。

`test_freeze_releases_each_daily_lock_after_cancellation` 只设置了 `storage.snapshot.bulk_write = AsyncMock()`，没有把 `storage.snapshot.update_one` 设置为异步 mock。生产代码在写完成标记处执行 `await storage.snapshot.update_one(...)` 时，`MagicMock` 返回值不可 await，会抛 `TypeError`；该异常又被 `_freeze_dates()` 的 `except Exception` 吞掉。测试仍只断言 Redis 的 set/release key，因此即使完成标记永远写不成功、冻结主体失败，测试也会通过。

**建议修法：** 将 `snapshot.update_one` 配置为 `AsyncMock` 并断言标记写入的 query/update；同时让 fake snapshot 真正保存 bulk upsert 和 marker，再断言第一天成功、第二天未获锁跳过，以及所有成功日期的结果数字。测试名中的 cancellation 也应使用可控 renewal task 或显式注入取消，而不是依赖 task 创建后立即被 finally 取消。

## 逐条核对结果（未发现新增实现问题）

- **A1 锁续租停止：** `snapshot.py:331-347` 的 `stop_renewal()` 已为 async 并 await task，`CancelledError` 被吞掉；`snapshot.py:496-516` 使用嵌套 `finally`，续租任务异常不会阻止 Redis release。`backfill.py:557-568` 的同类实现也采用 await/cancel 方式。
- **A2 逐日锁：** `snapshot.py:285-304` 每个日期使用独立 key，拿不到某日锁时 `continue`，不会提前 return；指定测试至少验证了 set key 序列和竞争日跳过。
- **A3 续租丢失检查：** `snapshot.py:348-374` 设置 `lock_lost`，在 trace 聚合、session 聚合及写入前后检查，未发现丢锁后仍主动写完成标记的正常路径。MAJOR-1 关注的是锁竞争/异常结果没有向读取层传递，并非该检查本身缺失。
- **A4 冻结忽略筛选：** `snapshot.py:367-376` 创建的 `date_filters` 只含日期，未把请求的 persona、agent、role 带入冻结聚合；筛选仅在 `_merge_results()` 的 `base_match` 中应用。
- **完成标记排除：** `read_or_freeze()` 的 marker 查询是有意读取标记；`_merge_results()` 的所有 snapshot dimension pipeline、persona/agent 用户集合 pipeline 都共享 `base_match`，其中 `user_id != "__snapshot_complete__"`，未发现绕过该排除条件的快照聚合入口。该结论不改变 MINOR-1 所述回填未写 marker 的问题。
- **A5 正常快照区间去重与旧字段回退：** `_visible_metric()`/`_group_ids()` 在现代数组字段存在时取 ID 并集，缺字段时回退存储整数；`_merge_metric_items()` 对维度行保留现代 ID 集合与旧整数。正常快照读取路径的跨日 active/new session 逻辑成立；MAJOR-2 仅涉及快照不可用时的实时回退。
- **A6 用户×persona 合并：** `snapshot.py:1008-1015` 复用 `_merge_metric_items(user_persona_items, ("user_id", "persona_preset_id"))`，未发现另一份会把旧文档 ID 缺失计为零的手写合并实现。
- **A7 DailyFreezeWorker：** `daily_freeze.py:77-84` 按 UTC+8 取昨天，默认间隔为 3600 秒；`src/api/main.py:89` 登记关闭任务，`:548` 创建任务，关闭 helper 会取消并等待任务；worker 对普通异常只记录并返回，不阻塞 lifespan 启动。Redis 全局锁与快照逐日锁共同提供多实例下的基本互斥。
- **回填游标失败保护：** `backfill.py:214-252` 只有 activity 与 snapshot 都成功才更新 cursor，失败日期写入 `failed_dates`；未发现本批改动会在单个 per-day helper 失败后无条件推进游标。
- **ActivityStorage：** `record()` 按 UTC+8 日期 upsert 且 sources 使用 `$addToSet`；`record_message_activity()` 为 best-effort，未发现本范围内会因 activity 写入异常阻断主流程的新问题。
- **analytics manager 参数透传：** `src/infra/analytics/manager.py` 新增的 filters/model/first_use 等参数均向 storage 透传，未发现本范围内的参数丢失。

## 未重复展开的并行审核结论

`review-api.md` 中关于 `storage.py` 的峰值时间过滤、模型钻取口径以及 `/usage/by-user` 的具体 API 层问题不在本 stage 的源码范围内；其中 `/usage/by-user` 在快照内部回退时缺少 `by_user_persona`，已在本报告 MAJOR-2 从快照层独立确认。前端报告中的首卡钻取口径和竞态测试问题未在本 stage 重审。
