# 后台配置页 MongoDB 存储指标面板

## Goal

让管理员在后台配置页直接看到 MongoDB 的空间占用，尤其是 checkpoint 两张集合的
体量，从而能对已有的「checkpoint 定期清理」做出有依据的保留期配置决策。

当前问题：清理逻辑（`CHECKPOINT_CLEANUP_*`，见 `.trellis/grill/checkpoint-retention-cleanup.md`）
已上线，但效果完全不可见 —— 管理员既不知道清理前后差多少，也不知道该把保留期
设成几天。全仓 `src/` 目前零处 Mongo 诊断调用。

## Scope

只读空间指标。经 grill 确认（`.trellis/grill/mongodb-storage-metrics.md`）：

- 只做**空间**，不做服务器压力（`serverStatus` 需 `clusterMonitor` 角色）。
- 只读，**不提供**「立即清理」按钮。
- 实时查询 + 手动刷新，**不引入**后台采样 worker 或历史趋势。

## Requirements

### R1 — 后端指标接口

- 新增管理员只读接口，返回 MongoDB 空间指标。
- 权限沿用 `settings:manage`，与 `/health/memory`、analytics 全线一致。
- 数据来源必须是**元数据级**读取，禁止任何全表扫描（明确禁止 `countDocuments()`）：
  - 库级：`dbStats`（`dataSize` / `storageSize` / `indexSize` / `objects` / `collections`）。
  - 集合级：`$collStats` 的 `storageStats`（MongoDB 6.2 起 `collStats` 命令已弃用，
    生产为 8.2.5）。
- 集合级至少覆盖：`checkpoints`、`checkpoint_writes`、sessions、traces。
  集合名必须从既有配置/常量推导，不得散写字面量。
- `$collStats` 在分片集合上每分片返回一个文档 → 必须对结果求和，不可取首条。

### R2 — 清理上下文并置

指标必须能转化为决策，因此同一响应需附带当前清理配置的生效值：
`CHECKPOINT_CLEANUP_ENABLED` / `RETENTION_DAYS` / `INTERVAL_HOURS`，
以及「待清理积压」会话数（`sessions.updated_at < cutoff`，
复用既有索引 `user_status_updated_idx`）。

### R3 — 失败降级（fail-soft）

- 任一指标不可得（无权限、命令不支持、连接异常）时，返回 `available:false` + 原因，
  **不抛 5xx**。配置页不得因诊断查询失败而打不开。
- 单个集合查询失败不影响其余集合与库级指标。

### R4 — 前端面板

- 在后台配置页展示，复用 `SystemHealthSection` 的既有形态（卡片 + 指标格 +
  状态徽标 + 手动刷新），取数用 `useState/useCallback/useEffect`（全仓不用 react-query）。
- 不新增 category（`mongodb`/`checkpoint` 已存在），不改路由、不加 `TabType`。
- 字节数自适应单位（B/KB/MB/GB），避免大集合读数不可读。
- 无权限或不可用时显示降级态，不显示空白或报错。

### R5 — i18n

新增文案同步 5 个 locale（`frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`）。
运行时指标文案使用独立 namespace，**不**塞进 `settingDesc`（那是设置项描述专用）。

### R6 — 测试

- 后端：路由测试覆盖成功、403 无权限、指标不可得降级、分片多文档求和。
  遵循既有 fake collection + dependency override 模式，不引入 mongomock。
- 前端：若新增纯函数（如字节格式化），按 `node:test` 约定补测试。

## Acceptance Criteria

- [ ] AC1 管理员在后台配置页能看到数据库总占用与 `checkpoints`、`checkpoint_writes`
      两张集合各自的大小（两张都在，不只一张）。
- [ ] AC2 接口不执行任何全表扫描；集合大小来自 `$collStats.storageStats`。
- [ ] AC3 面板同时显示当前保留期配置与待清理积压会话数。
- [ ] AC4 Mongo 不可用或权限不足时，面板显示「不可用」降级态，配置页其余部分正常。
- [ ] AC5 非 `settings:manage` 用户访问接口返回 403。
- [ ] AC6 5 个 locale 文件均含新增 key，无遗漏。
- [ ] AC7 后端测试通过；`tsc --noEmit` 与 eslint 通过。

## Constraints

- 当前 thread 对 `123zhaozheng/kunxiaozhi` **只有只读权限**，无法 push；
  交付形态为补丁 + 本任务文档。
- 生产 MongoDB 8.2.5，k8s 内网无认证部署；但代码不得依赖 admin-only 命令。
- 保留期下限 7 天、批量上限 1000 等既有约束不在本任务变更范围内。

## Out of Scope

- `serverStatus` 压力指标（连接数、QPS、WiredTiger 缓存命中）。
- 「立即清理」等任何写操作。
- 指标历史趋势、采样存储、告警。
- 清理 traces / sessions 本身。
