# Implement：口径与日快照层

依赖：父任务 `design.md` §2、§3.1、§3.2、§4。串行执行 S1 → S5。

---

## S1 日期区间工具（其它一切的基础）

- [x] 新建 `src/infra/analytics/date_range.py`：`CST` 常量、`resolve_range`、
      `previous_range`、`day_buckets`。纯函数，无 IO。
- [x] 把 `src/infra/analytics/storage.py:52` 的 `_BUCKET_TZ` 改为从此模块导入，
      不保留第二处定义。
- [x] `tests/infra/test_analytics_date_range.py`

验证：`uv run pytest tests/infra/test_analytics_date_range.py -q`

## S2 日活跃记录

- [x] 新建 `src/kernel/schemas/analytics_activity.py`（或并入现有 analytics schema，
      按 `.trellis/spec/backend/database-guidelines.md` 的 Base/Create/Full 约定）。
- [x] 新建 `src/infra/analytics/activity_storage.py`：`ensure_indexes`、
      `record(user_id, source, at)`（upsert + `$addToSet`）、
      `distinct_users(start, end, source=None)`、`first_message_date(user_ids)`。
- [x] `UserStorage.touch_updated_at`（`src/infra/user/storage.py:714`）内追加
      `source=login` 写入。**保留原有 `updated_at` 更新**。用 try/except 包裹，
      失败只 warning。
- [x] trace 写入 `user:message` 的路径追加 `source=message` 写入，同样尽力而为。
- [x] `tests/infra/test_analytics_daily_activity.py`

验证：`uv run pytest tests/infra/test_analytics_daily_activity.py -q`

## S3 日快照层

- [x] 新建 `src/infra/analytics/snapshot.py`：快照文档 schema、`ensure_indexes`、
      `freeze_day(date, filters)`、`read_or_freeze(filters)`。
- [x] `read_or_freeze` 按父任务 design §3.1 的策略实现：今天实时、历史读快照、
      缺失则冻结后读、异常降级实时。
- [x] 冻结使用 upsert 且不覆盖已存在快照。
- [x] Redis 锁复用 `src/infra/storage/redis.py` 的 client 创建方式，
      锁键 `analytics:snapshot:freeze:{date}`。
- [x] `AnalyticsStorage` 的 usage 系列方法改为经 `read_or_freeze` 取数，
      `usage_facts_stages()` 仍是唯一的实时聚合口径来源（不得新增第二套 pipeline）。
- [x] `tests/infra/test_analytics_snapshot_immutable.py`

验证：`uv run pytest tests/infra/test_analytics_snapshot_immutable.py tests/infra/test_analytics_usage_query.py tests/infra/test_analytics_usage_persona_parity.py -q`

## S4 回填 worker

- [x] 新建 `src/infra/analytics/backfill.py`：`AnalyticsBackfillWorker.run_once()`，
      形状对照 `src/infra/session/backfill.py`（锁、分批、续锁、幂等）。
- [x] `src/api/main.py` 按 `:492` 的 `_backfill_session_search` 形状挂一个
      `create_task`，异常只 warning。任务名加入文件顶部的后台任务名单
      （`main.py:87` 附近那组常量）。
- [x] 进度标记集合 `analytics_backfill_state`。
- [x] `tests/infra/test_analytics_backfill.py`

验证：`uv run pytest tests/infra/test_analytics_backfill.py -q`

## S5 收口

- [x] `AnalyticsStorage.ensure_indexes` 增补两个新集合的索引创建
      （或在各自 storage 的 `ensure_indexes` 内，与项目习惯一致者优先）。
- [x] 全量：`uv run ruff check .`；`uv run mypy src/`；`uv run pytest tests/infra tests/api -q`
- [x] 确认未改动任何前端文件、未改动 API 端点签名。

---

## 风险点

| 风险 | 处理 |
|------|------|
| 在 `touch_updated_at` 里加 IO 会拖慢登录 | 写入是单条 upsert 且 try/except 包裹；若压测显示影响，改为 fire-and-forget 后台任务（项目已有 `BestEffortTaskLimiter`，见 `src/infra/user/manager.py:9`） |
| 首次启动回填在大数据量下跑很久 | 分批 + 让出事件循环 + 进度标记；失败不阻塞启动 |
| 冻结逻辑并发重复写 | 唯一索引 + upsert 不覆盖 + Redis 锁三重保证 |

## 回滚

两个新集合与 worker 均为纯增量。回滚方式：撤掉 `main.py` 的 worker 挂载与
`read_or_freeze` 的调用点，统计即回到实时聚合；集合留着不影响任何功能。
