# Implement：统计接口整合与清理

依赖：子1 已完成（`date_range.py`、`snapshot.py`、`activity_storage.py` 可用）。
串行执行 S1 → S4。

---

## S1 日期参数

- [x] `src/api/routes/analytics.py`：所有端点 `start`/`end` 改为 `str`，
      用 FastAPI `Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")` 校验，
      非法组合（`end < start`）在处理函数里 400。
- [x] 统一调用子1 的 `resolve_range`，把 datetime 传给 storage 层。
      storage 层内部不再自己算边界。
- [x] `$match` 全部改半开区间；确认 `storage.py` 中不再有 `$lte` 形式的时间上界。
- [x] `tests/api/routes/test_analytics_date_params.py`

验证：`uv run pytest tests/api/routes/test_analytics_date_params.py -q`

## S2 summary 对比字段

- [x] `src/kernel/schemas/analytics.py`：`UsageSummaryResponse` 增 `using_users`
      与 `previous`；新增 `UsageSummaryPrevious`。
- [x] `AnalyticsManager` / `AnalyticsStorage`：主区间与 `previous_range` 各取一次，
      同筛选。带 persona/agent 筛选时把 `active_users` 置为 `using_users`。
- [x] 扩展 `tests/api/routes/test_analytics_usage_routes.py`

验证：`uv run pytest tests/api/routes/test_analytics_usage_routes.py -q`

## S3 洞察端点

- [x] `src/kernel/schemas/analytics.py`：`UsageInsightsResponse` 及四个子结构。
- [x] `AnalyticsStorage` 新增四个取数方法，全部复用 `usage_facts_stages()`
      与子1 的 `activity_storage`，不新写会话/消息计数 pipeline。
- [x] `peak` 改为按 `user:message` 的 `timestamp`（或 trace `started_at`，
      与其它指标同源者优先）分 `$dayOfWeek`/`$hour` 桶，时区用 `CST`。
- [x] `GET /api/analytics/usage/insights` 端点，权限 `settings:manage`。
- [x] `tests/api/routes/test_analytics_insights_route.py`

验证：`uv run pytest tests/api/routes/test_analytics_insights_route.py -q`

## S4 清理

按顺序对每一项执行：先 grep 取证 → 写入 `research/removal-evidence.md` → 再删。

- [x] `/tokens/by-preset`：grep 前后端引用（含 i18n key、测试）。
- [x] `/sessions/list`、`/sessions/export.csv` 的 `preset_id` 分支。
- [x] 被 `usage/*` 取代的旧聚合方法（逐个确认无调用方）。
- [x] `storage.py:159` 的 `users.updated_at` 索引创建语句（**只删索引，保留字段**）。
- [x] 删除后连带清理：无用的 schema、无用的 import、无用的测试。

验证：
```powershell
uv run ruff check .
uv run mypy src/
uv run pytest tests/api tests/infra -q
```

> `/users/heatmap` 的存废依赖子3 的实际调用情况，留到子4 判定，本子任务不删。

---

## 破坏性变更说明

`start`/`end` 从 ISO 时间戳改为纯日期是接口的破坏性变更。前后端同仓同版本发布、
无外部调用方，因此不做双参数兼容期。若发现任何仓外调用方，立即回到本文件补兼容层。

## 回滚

改动集中在 `src/api/routes/analytics.py`、`src/infra/analytics/storage.py`、
`src/kernel/schemas/analytics.py` 三个文件，`git checkout` 即可退回。
