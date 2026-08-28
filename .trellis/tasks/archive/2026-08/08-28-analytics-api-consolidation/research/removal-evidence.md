# S4 清理取证记录（removal-evidence）

任务：08-28-analytics-api-consolidation（child-2，API 整合）
取证时间：2026-08-28；工作目录：`D:\code\python\LambChat`（Git Bash，命令均为 `grep -rn`）。
流程遵循 implement.md：每项先取证 → 记录 → 再删。以下输出为删除前状态。

---

## 项目 1：`/tokens/by-preset` 端点（路由 + manager + storage）

### 1.1 端点路径引用

```
$ grep -rn "by-preset" src/ tests/ frontend/src/
src/api/routes/analytics.py:196:@router.get("/tokens/by-preset", response_model=ByLabelResponse)
src/api/routes/analytics.py:466:@router.get("/feedback/by-preset", response_model=ByPresetFeedbackResponse)   # ← 保留（不在清理清单）
src/infra/analytics/storage.py:861:            logger.warning("feedback by-preset sessions aggregation failed: %s", ex)   # ← 保留（feedback 链路日志）
src/infra/analytics/storage.py:899:            logger.warning("feedback by-preset aggregation failed: %s", ex)           # ← 保留
frontend/src/services/api/analytics.ts:162:      `${BASE}/tokens/by-preset${rangeQuery(start, end)}&limit=${limit}`,
frontend/src/services/api/analytics.ts:218:      `${BASE}/feedback/by-preset${rangeQuery(start, end)}`,                 # ← 保留
```

测试与 i18n 零命中：

```
$ grep -rn "tokens/by-preset" tests/ frontend/src/i18n/ frontend/src/locales/
（无输出，exit 1）
```

### 1.2 方法引用

```
$ grep -rn "get_tokens_by_preset" src/ tests/
src/api/routes/analytics.py:197:async def get_tokens_by_preset(
src/api/routes/analytics.py:209:    items = await manager.get_tokens_by_preset(s, e, limit=limit)
src/infra/analytics/manager.py:69:    async def get_tokens_by_preset(
src/infra/analytics/manager.py:72:        return await self.storage.get_tokens_by_preset(start, end, limit=limit or 10)
src/infra/analytics/storage.py:482:    async def get_tokens_by_preset(
```

结论：仅后端三层自引用，无任何测试覆盖、无 i18n 文案。
⚠️ 前端仍有引用（`analytics.ts:156-164`、`AnalyticsPanel.tsx:820,1270,1274`），
按串行计划由 child-3（UI 重构）负责移除；本子任务不动前端（implement.md 约束）。

### 1.2b 前端调用链存活判定（2026-08-28 补充取证）

任务要求：若前端调用路径已是死代码则照删；若仍有活跃调用方则**不删**，等子3。
逐层取证结果：

```
$ grep -rn "getTokensByPreset|tokens/by-preset" frontend/src/
frontend/src/services/api/analytics.ts:156:  async getTokensByPreset(          # service 方法定义
frontend/src/services/api/analytics.ts:162:      `${BASE}/tokens/by-preset...` # 唯一路径引用点
frontend/src/components/panels/AnalyticsPanel.tsx:820:        analyticsApi.getTokensByPreset(start, end, 10),

$ grep -rn "tokensByPreset" frontend/src/
frontend/src/components/panels/AnalyticsPanel.tsx:725:  const [tokensByPreset, setTokensByPreset] = useState<ByLabelItem[]>([]);
frontend/src/components/panels/AnalyticsPanel.tsx:1270:              isEmpty={!isLoading && (tokensByPreset?.length ?? 0) === 0}
frontend/src/components/panels/AnalyticsPanel.tsx:1274:                data={tokensByPreset}     # PieBlock 渲染

$ grep -rn "AnalyticsPanel" frontend/src/components/layout/AppContent/TabContent.tsx
TabContent.tsx:82:  analytics: AnalyticsPanel,                                  # panelMap 挂载

$ grep -rn '"analytics"' frontend/src/App.tsx
App.tsx:299:  return <AppContent key="analytics" activeTab="analytics" />;      # 路由级页面
```

调用链：`App.tsx:299`（/analytics 页面路由）→ `TabContent.tsx:82`（analytics tab）→
`AnalyticsPanel.tsx`（`fetchData` 每次加载都调用 `getTokensByPreset`，:820）→
`tokensByPreset` state 渲染为 "Tokens by agent type (Top 10)" 饼图（:1262-1277）。
**结论：活跃调用方，非死代码。**

### 1.2c 最终决策：保留端点（撤销前一代理的删除）

前一实现代理已先行删除路由/manager/storage 三层，与上述判定冲突。
本次已将其**恢复**，并统一为 S1 日期参数契约（`_parse_range` + `$lt` 半开区间）：

- `src/api/routes/analytics.py`：恢复 `GET /tokens/by-preset`（位于 `/tokens/by-model` 之后），
  参数改为 `start`/`end` YYYY-MM-DD + `limit`，docstring 注明"待子3 切换后再删除"。
- `src/infra/analytics/manager.py`：恢复 `get_tokens_by_preset`。
- `src/infra/analytics/storage.py`：恢复 `get_tokens_by_preset`，
  `$match` 时间上界由原 `$lte` 改为 `$lt`（S1 半开区间契约）。

**删除推迟至子3（UI 重构）完成、前端切换到 `usage/by-persona` 之后。**

### 1.3 常量连带检查

```
$ grep -rn "_TOP_PRESET_LIMIT" src/
src/infra/analytics/storage.py:61:_TOP_PRESET_LIMIT = 10
src/infra/analytics/storage.py:486:        limit: int = _TOP_PRESET_LIMIT,   # get_tokens_by_preset（删）
src/infra/analytics/storage.py:533:        limit: int = _TOP_PRESET_LIMIT,   # get_sessions_by_agent（保留）
src/infra/analytics/storage.py:567:        limit: int = _TOP_PRESET_LIMIT,   # get_sessions_by_persona（保留）
src/api/routes/analytics.py:45:_MAX_TOP_PRESET_LIMIT = 100
src/api/routes/analytics.py:159:    limit: int = Query(10, ge=1, le=_MAX_TOP_PRESET_LIMIT, ...)  # sessions/by-agent（保留）
src/api/routes/analytics.py:173:    limit: int = Query(10, ge=1, le=_MAX_TOP_PRESET_LIMIT, ...)  # sessions/by-persona（保留）
src/api/routes/analytics.py:200:    limit: int = Query(10, ge=1, le=_MAX_TOP_PRESET_LIMIT, ...)  # tokens/by-preset（保留）
```

结论：两个常量均保留（仍有其他使用者）；路由层 `_MAX_TOP_PRESET_LIMIT` 不动。

**删除范围**：无（见 1.2c：端点保留，删除推迟至子3）。
原计划的 `GET /tokens/by-preset`（analytics.py）、`manager.get_tokens_by_preset`、
`storage.get_tokens_by_preset` 曾被前一代理删除，本次已全部恢复。

---

## 项目 2：`/sessions/list` 与 `/sessions/export.csv` 的 `preset_id` 兼容参数

### 2.1 后端引用

```
$ grep -rn "preset_id" src/api/routes/analytics.py src/infra/analytics/manager.py | grep -v persona_preset_id
src/api/routes/analytics.py:482:    preset_id: Optional[str] = Query(None, description="...兼容旧参数，等价 persona_preset_id")  # /sessions/list（删）
src/api/routes/analytics.py:506:        preset_id=preset_id,          # list_sessions 透传（删）
src/api/routes/analytics.py:520:    preset_id: Optional[str] = Query(...)   # /sessions/export.csv（删）
src/api/routes/analytics.py:542:        preset_id=preset_id,          # list_sessions 透传（删）
# 以下为保留项（各端点主参数，不在清理清单）：
#   :269-279 /presets/{preset_id} 指标端点；:674/:684 feedback list；:692/:700 runs list
src/infra/analytics/manager.py:108:        preset_id: Optional[str] = None,   # list_sessions 形参（删）
src/infra/analytics/manager.py:120:            preset_id=preset_id,           # 透传（删）
# 保留：:156/:161 list_feedback、:167/:171 list_runs 的 preset_id 主参数
```

```
$ grep -rn "preset_id" src/infra/analytics/storage.py | grep -v persona_preset_id
storage.py:965:  preset_id: str | None = None,        # _build_session_query 形参（删）
storage.py:972:  兼容旧参数 preset_id；新参数...        # docstring（删）
storage.py:978:  effective_preset = persona_preset_id or preset_id   # 兼容分支（改为直接用 persona_preset_id）
storage.py:991:  preset_id: str | None = None,        # list_sessions 形参（删）
storage.py:1015: preset_id=preset_id,                 # _build_session_query 调用透传（删）
# 保留：:1232 list_feedback、:1310 list_runs 的 preset_id 主参数
```

### 2.2 前端调用方取证（零命中）

```
$ grep -rn "preset_id" frontend/src/services/api/analytics.ts
43:    q = appendParam(q, "persona_preset_id", filters.personaPresetId);
67:    q = appendParam(q, "persona_preset_id", personaId);
294:      query = appendParam(query, "preset_id", options.presetId);   # listFeedback（保留）
311:      query = appendParam(query, "preset_id", options.presetId);   # listRuns（保留）
```

`listSessions` / `exportSessionsCsv` 仅经 `appendListFilters`（:43）发送 `persona_preset_id`，
从不发送 `preset_id` → 兼容参数零前端调用方。

### 2.3 测试调用方取证（零命中）

```
$ grep -rn "list_sessions" src/ tests/ --include="*.py"
# analytics 链路的调用点：
src/api/routes/analytics.py:503 / :539        # 两个路由（透传中仅 :506/:542 传 preset_id）
src/infra/analytics/manager.py:117
tests/api/routes/test_analytics_csv_export.py:37:  async def list_sessions(self, start, end, **kwargs)
tests/infra/test_analytics_storage_list_filters.py:140/177/231/274   # 均不传 preset_id
# session.py:172 / src/infra/session/* 为会话域同名方法，与本清理无关
```

无任何测试以 `preset_id=` 调用 analytics `list_sessions`（上表已逐一核对）。

**删除范围**：两个路由的 `preset_id` Query 形参与透传；`manager.list_sessions` 形参；
`storage.list_sessions` 形参；`_build_session_query` 形参/兼容分支/文档串。

### 2.4 删除后验证（2026-08-28）

已删内容核对（当前工作区）：

```
$ grep -n "preset_id" src/api/routes/analytics.py | grep -v persona_preset_id
270:@router.get("/presets/{preset_id}", ...)   # 路径参数，保留
667/:685: /feedback/list、/runs/list 的 preset_id 主参数     # 保留
# /sessions/list 与 /sessions/export.csv 的兼容参数已无残留

$ grep -n "preset_id" src/infra/analytics/manager.py | grep -v persona_preset_id
90/:92/:154/:165: get_preset_metrics / list_feedback / list_runs 主参数   # 保留
# list_sessions 的 preset_id 形参与透传已删

$ grep -n "effective_preset" src/infra/analytics/storage.py
（无输出）   # 兼容分支已删；_build_session_query 直接用 persona_preset_id
```

修复的遗留缺陷：前一代理删除形参时在 `storage.list_sessions` 内遗留了
`preset_id=preset_id` 透传行（运行时会 NameError），本次一并删除。

删除后测试记录：

```
$ uv run pytest tests/api/routes/test_analytics_date_params.py \
    tests/api/routes/test_analytics_insights_route.py \
    tests/api/routes/test_analytics_usage_routes.py \
    tests/api/routes/test_analytics_csv_export.py \
    tests/api/routes/test_analytics_preset_route.py \
    tests/infra/test_analytics_storage_list_filters.py \
    tests/infra/test_analytics_usage_query.py \
    tests/infra/test_analytics_usage_persona_parity.py \
    tests/infra/test_analytics_active_user_consistency.py -q
65 passed, 18 warnings in 2.62s
```

---

## 项目 3：被 `usage/*` 取代的旧聚合方法排查（孤儿方法扫描）

```
$ grep -rn "get_active_users_trend\|get_active_users_by_day\|_date_range_query\|get_overview\|get_sessions_trend" src/api/routes/analytics.py src/infra/analytics/
```

核对结果（删除前）：

| 方法 | 状态 | 依据 |
|---|---|---|
| `get_overview` | 保留 | 路由 `/overview` 在用，且已委托 usage 层（内部经 `_usage_summary_numbers`/snapshot） |
| `get_sessions_trend` | 保留 | 路由 `/sessions/trend` 在用，已委托 usage 层 |
| `get_active_users_trend` | 保留 | 路由 `/users/trend` 在用 |
| `get_active_users_by_day` | 保留 | 为 `get_active_users_trend` 的内部助手 |
| `_date_range_query` | 保留 | `get_overview` 内部使用 |
| `get_tokens_by_preset` | 保留（推迟至子3） | 见项目 1.2b/1.2c：前端活跃调用，端点不可删 |

结论：除项目 1/2 所列外，无额外孤儿方法可删。

---

## 项目 4：`ensure_indexes` 中 `users.updated_at` 索引

### 4.1 索引定义

```
$ grep -rn "updated_at" src/infra/analytics/
src/infra/analytics/storage.py:193:   # users.updated_at 用于活跃用户判定（自动 id-based 索引辅助）
src/infra/analytics/storage.py:194:   await self.users.create_index([("updated_at", -1)], background=True)
src/infra/analytics/storage.py:1026:  "updated_at": 1,                    # sessions 投影字段（保留）
src/infra/analytics/storage.py:1105:  updated_at=doc.get("updated_at"),   # sessions 行映射（保留）
src/infra/analytics/backfill.py:205:  "updated_at": now_utc,              # 用户文档写入（保留）
```

### 4.2 users 集合全部读取点（无一按 updated_at 查询/排序）

```
$ grep -A8 "self\.users\.find" src/infra/analytics/storage.py
storage.py:950   find({"_id": {"$in": object_ids}, "roles": role_id}, {"_id": 1})          # 角色成员
storage.py:1081  find({"_id": {"$in": object_ids}}, {"_id": 1, "username": 1})             # 用户名回填
storage.py:1196  find({"_id": {"$in": object_ids}}, {..., "display_name": 1, "roles": 1})  # by-user 名单
storage.py:1746  find({"_id": {"$in": object_ids}}, {..., "display_name": 1, "roles": 1})  # by-persona 名单
storage.py:1895  find({"_id": {"$in": object_ids}}, {"_id": 1, "username": 1, "display_name": 1})  # 反馈名单
```

全仓 `sort("updated_at")` 取证（排除无关集合后）：

```
$ grep -rn 'sort.*updated_at' src/
# 命中均为 persona_presets/memory/sessions/traces/skills/team 等集合的自有索引与排序，
# 无任何代码对 analytics 的 users 集合按 updated_at 排序或过滤。
```

结论：usage 层迁移后活跃用户改由 traces（`usage_facts_stages`）与
`user_daily_activity`（ActivityStorage）计算，`users.updated_at` 索引零使用。

**删除范围**：仅 `ensure_indexes` 中的索引创建行（含注释），字段与写入保留。

### 4.3 删除后验证（2026-08-28）

```
$ grep -n "create_index.*updated_at" src/infra/analytics/storage.py
（无输出，exit 1）   # 索引创建语句已删

$ grep -n '"updated_at"' src/infra/analytics/backfill.py
src/infra/analytics/backfill.py:205:  "updated_at": now_utc,   # 字段写入保留 ✓
```

删除后测试记录：同 2.4（65 passed）——`ensure_indexes` 在
`tests/infra/test_analytics_*` 各用例的 storage 初始化路径中被覆盖，无回归。

---

## 清理汇总（最终执行结果，2026-08-28）

1. **`/tokens/by-preset`：保留**（项目 1.2b/1.2c）。前端 AnalyticsPanel 活跃调用并渲染，
   非死代码；前一代理的三层删除已撤销恢复，端点统一为 S1 日期参数契约（`$lt` 半开区间）。
   删除推迟至子3（UI 重构）前端切换到 `usage/by-persona` 之后。
2. `/sessions/list`、`/sessions/export.csv` 的 `preset_id` 兼容分支：**已删**（路由形参+透传、
   `manager.list_sessions` 形参、`storage.list_sessions`/`_build_session_query` 形参、
   `effective_preset` 兼容分支与文档串）。另修复前一代理遗留的 `preset_id=preset_id` 透传
   残行（否则运行时 NameError）。
3. 旧聚合方法：排查后除上述外无孤儿方法可删（项目 3 表）。
4. `users.updated_at` 索引创建语句：**已删**（仅 `ensure_indexes` 中一行+注释）；
   字段与 `backfill.py:205` 写入保留。
5. 连带清理：无需删除的 schema（`ByLabelResponse` 等仍被保留端点使用）；无孤儿 import；
   无测试引用被删项。
6. 保留：`_TOP_PRESET_LIMIT`、`_MAX_TOP_PRESET_LIMIT`、`/feedback/by-preset`、
   `/presets/{preset_id}`、`list_feedback`/`list_runs` 的 `preset_id` 主参数、
   `users.updated_at` 字段本身、`/users/heatmap`（留待子4 判定）。

## 全量验证（2026-08-28）

```
$ uv run ruff check <本任务改动的 13 个文件>
All checks passed!

$ uv run mypy src/
src\infra\tool\mineru_client.py:101: error: Need type annotation for "first"  [var-annotated]
Found 1 error in 1 file (checked 397 source files)   # 存量唯一错误，与本任务无关

$ uv run pytest tests/api tests/infra -q --ignore=tests/infra/share/test_storage_limits.py
9 failed, 1638 passed, 31 warnings in 37.91s
```

- 收集错误 `tests/infra/share/test_storage_limits.py`：与 `tests/infra/envvar/test_storage_limits.py`
  同 basename 且两目录均无 `__init__.py` 导致的 pytest import 冲突——**存量结构问题**，
  两文件均在 HEAD 且本次未改动（`git diff HEAD` 为空），与本任务无关。
- 9 个失败用例（test_human_wait / test_shared_page_route×2 / wecom preferred_agent /
  sandbox_grep_timeout / skill loader_prompt / runtime_services×2 / s3_storage）：
  将本任务 src 改动 `git stash` 后在 HEAD 状态重跑，9 个**全部同样失败**，证明均为存量失败，
  与本任务无关。全部 9 个均不涉及 analytics 模块。
- `grep -n "\$lte" src/infra/analytics/storage.py` → 零命中（exit 1），S1 验收项通过。
- analytics 相关 9 个测试文件（含 date_params / insights / usage_routes / csv_export /
  preset_route / list_filters / usage_query / persona_parity / active_user_consistency）：
  **65 passed**。
