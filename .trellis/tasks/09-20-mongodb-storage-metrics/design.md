# Design: 后台配置页 MongoDB 存储指标面板

Base: `feat/user-storage-management` @ `fbd9e737`
Grill: `.trellis/grill/mongodb-storage-metrics.md`

## 架构概览

```
SettingsPanel.tsx  ──renders──▶  MongoStorageSection.tsx
                                        │ useEffect + healthApi-style fetch
                                        ▼
                          GET /health/mongodb  (settings:manage)
                                        │
                                        ▼
                    src/infra/monitoring/mongo_storage.py
                       ├── dbStats            (库级，元数据)
                       ├── $collStats         (集合级 storageStats，元数据)
                       └── sessions.count_documents(updated_at < cutoff)  ← 见「积压口径」
```

沿用 `/health/memory` 的整条范式（`src/api/routes/health.py:171-185`）：同一 router、
同一权限依赖、同一"返回聚合 dict"的形态。**不新建 router，不新建权限位。**

## 后端

### 新文件 `src/infra/monitoring/mongo_storage.py`

`src/infra/monitoring/` 现有 `memory.py` / `distributed_memory_health.py`，
本模块与之平级，导出经 `__init__.py`（与 `MemoryMonitor` 一致）。

#### 集合名来源（禁止散写字面量）

| 集合 | 来源 |
|---|---|
| `checkpoints` | `src/infra/storage/checkpoint.py` 的 `get_mongo_checkpointer` 默认参数 |
| `checkpoint_writes` | `langgraph-checkpoint-mongodb==0.4.0` `MongoDBSaver` 默认值 |
| sessions | `settings.MONGODB_SESSIONS_COLLECTION` |
| traces | `settings.MONGODB_TRACES_COLLECTION` |

前两者在本仓无常量，**新增**模块级常量
`CHECKPOINT_COLLECTION_NAME = "checkpoints"`、
`CHECKPOINT_WRITES_COLLECTION_NAME = "checkpoint_writes"` 于 `storage/checkpoint.py`，
并让 `get_mongo_checkpointer(collection_name=CHECKPOINT_COLLECTION_NAME)` 引用它，
消除"默认参数字面量"与面板之间的漂移风险。

#### `$collStats` 取数（R1 / AC2）

```python
pipeline = [{"$collStats": {"storageStats": {}}}]
docs = await db[name].aggregate(pipeline).to_list(length=None)
```

- **必须 `to_list` 全量并求和**：分片集合每分片一个文档（官方文档明确）。
  非分片为单文档，求和退化为恒等，两种拓扑同一段代码。
- 取用字段：`storageStats.size`（逻辑数据量）、`storageStats.storageSize`（磁盘）、
  `storageStats.totalIndexSize`、`storageStats.count`（元数据计数，非扫描）。
- `collStats` 命令自 6.2 弃用 → 只在 `$collStats` 抛 `OperationFailure` 时回退，
  回退失败即标记该集合 `available:false`。

#### 积压口径（R2）的性能取舍

复用 `cleanup_worker` 的 cutoff 语义（`utc_now() - timedelta(days=retention_days)`）。
计数走 `count_documents({"updated_at": {"$lt": cutoff}})`：

- 该查询**命中既有索引** `user_status_updated_idx`（`session/storage.py:91`），
  是索引计数而非全表扫描 —— 与 PRD"禁止全表扫描"不冲突（禁的是无索引的
  `countDocuments()` 全集合扫描）。
- 但仍加 `maxTimeMS` 上限（2000ms）兜底；超时按 `available:false` 降级，
  不拖慢整个面板。
- 刻意**不**复刻 worker 里的 `$or`/`$expr` 已清理判定：`$expr` 无法走索引，
  在大集合上会退化为扫描。面板给的是"数量级参考"，不是精确待删数，
  响应里以 `approximate: true` 明示这一口径差异。

#### 降级契约（R3 / AC4）

每个区块独立 `available` 布尔 + `error` 原因字符串，任一失败不影响其余：

```json
{
  "available": true,
  "database": {"name": "agent_state", "available": true, "data_size": 0, ...},
  "collections": [{"name": "checkpoints", "available": true, "size": 0, ...}],
  "cleanup": {"enabled": false, "retention_days": 30, "interval_hours": 24,
              "backlog_sessions": 0, "approximate": true, "available": true},
  "checkpoint_backend": {"backend": "mongodb", "enabled": true}
}
```

顶层 `available` 仅在**连接层面**失败时为 `false`。路由整体 `try/except`
兜底返回降级体，**不抛 5xx**。

### 路由 `src/api/routes/health.py`

```python
@router.get("/health/mongodb")
async def mongodb_storage_health(
    _=Depends(require_permissions("settings:manage")),
): ...
```

与 `/health/memory` 同文件同 router，`main.py` 无需改动（router 已注册于 `:762`）。

### Schema

`/health/memory` 返回裸 dict，未定义 response_model。本任务**保持一致**，
返回 dict；前端在 `services/api/health.ts` 侧定义 TS 类型（与 `MemoryDiagnostics`
的现有做法相同）。避免为单个诊断接口引入后端 schema 分歧。

## 前端

### 新组件 `frontend/src/components/panels/MongoStorageSection.tsx`

形态与 `SystemHealthSection.tsx` 对齐：卡片标题 + 折叠 + `RefreshCw` 手动刷新 +
`MetricCard` 指标格 + `StatusBadge` 降级徽标。图标取 `lucide-react`
（`Database`/`HardDrive`/`Layers`）。

### 挂载点

`SettingsPanel.tsx:674` 紧邻 `<SystemHealthSection />` 之后。
选择**无条件渲染 + 组件内按 `Permission.SETTINGS_MANAGE` 自降级**，
与 `SystemHealthSection` 完全一致，不引入新的条件分支风格。

### API

`frontend/src/services/api/health.ts` 增 `healthApi.mongodbStorage()`，
走既有 `authFetch<T>`（自动 Bearer + 401 refresh + `ApiRequestError`）。

### 字节格式化

新增纯函数 `formatBytes(n)`，自适应 B/KB/MB/GB/TB（AC "大集合读数可读"）。
放在组件同目录的工具文件以便按 `node:test` 单测。

## i18n（R5 / AC6）

新 namespace **`mongoStorage`**，5 个 locale 同步：
`frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`。
不进 `settingDesc`（那是 `SettingItem.description` 的 i18n key 专用空间）。

## 测试（R6 / AC7）

后端 `tests/api/routes/test_mongodb_storage_health.py`，沿用
`tests/api/test_opensandbox_admin_routes.py:449-486` 的 `get_mongo_client` 替换手法
与 fake collection/cursor 模式（**不引入 mongomock**）：

1. 成功路径：库级 + 集合级字段齐全，`checkpoints` 与 `checkpoint_writes` 均在。
2. 403：无 `settings:manage`。
3. 降级：`$collStats` 抛 `OperationFailure` → 该集合 `available:false`，其余照常，HTTP 200。
4. **分片求和**：`$collStats` 返回 2 个文档 → size 为两者之和（防"取首条"回归）。
5. 连接失败：顶层 `available:false`，仍 200。

前端：`formatBytes` 的 `node:test`（`npx tsx --test`）。

校验：`uv run pytest`、`npx tsc --noEmit`、`npx eslint`。

## 风险

| 风险 | 缓解 |
|---|---|
| 分片集群下取首条导致读数偏小 | 强制求和 + 专门的多文档测试用例 |
| 积压计数在超大 sessions 上变慢 | 走既有索引 + `maxTimeMS=2000` + 降级 |
| checkpoint 集合名漂移 | 抽常量，与 `get_mongo_checkpointer` 共用同一来源 |
| 托管实例权限不足 | 分区块 fail-soft，面板显示不可用而非报错 |
