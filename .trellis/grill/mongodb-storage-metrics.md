# Grill: 后台配置页 MongoDB 存储指标面板

Date: 2026-09-20
Status: 第一波已确认，进入 Phase 1 规划
Target project: github.com/123zhaozheng/kunxiaozhi（当前 thread 无 push 权限，交付补丁）
Predecessor: `.trellis/grill/checkpoint-retention-cleanup.md`（清理侧已实现）

## Intent

checkpoint 定期清理已落地，但清理效果与数据库水位**不可见**：管理员无法判断
该把 `CHECKPOINT_CLEANUP_RETENTION_DAYS` 调成多少。本任务给后台配置页加一张
只读指标卡，回答两个问题：数据库整体占多大、checkpoint 两张集合各占多大。

## Verified findings（代码证据）

### 1. 全仓零 Mongo 诊断调用 → 这是第一处
- `src/` 内 `dbStats` / `collStats` / `serverStatus` / `$collStats` / `admin.command`
  **零匹配**。唯一命中是测试里的连通性探测
  `tests/api/routes/test_analytics_cross_consistency.py:512-519` 的 `admin.command("ping")`。
- 结论：需要新建诊断封装，没有可复用的现成 helper。

### 2. 已有一个几乎同形的先例 → 照抄即可
- `/health/memory`：`src/api/routes/health.py:171-185`，
  `Depends(require_permissions("settings:manage"))`，只读诊断，返回聚合 dict。
- 前端 `SystemHealthSection.tsx` 直接嵌在配置页 `SettingsPanel.tsx:674`，
  **无条件渲染**，内部按 `Permission` 自行降级。
- 取数为 `useState + useCallback + useEffect`（`useSettings.ts:12-35` 同款），
  全仓不用 react-query/SWR（`AnalyticsPanel.tsx:205-290` 佐证）。
- → 新面板不需要动路由、不需要加 `TabType`、不需要改 `App.tsx`。

### 3. checkpoint 是**两张**集合，不是一张（决定性）
- `langgraph-checkpoint-mongodb==0.4.0`（`uv.lock:2066-2076` 锁定）wheel 源码：
  `checkpoint_collection_name: str = "checkpoints"`、
  `writes_collection_name: str = "checkpoint_writes"`。
- 本仓只传了前者：`src/infra/storage/checkpoint.py:135-163`，后者取默认值。
- `delete_thread()` 对两张集合分别 `delete_many({thread_id})`。
- → 只显示 `checkpoints` 会严重低估占用；`checkpoint_writes` 通常更大。

### 4. 数据库句柄与集合名来源
- 单例 `get_mongo_client()`：`src/infra/storage/mongodb.py:30-74`（`@lru_cache`，
  Motor，`maxPoolSize=20`）；`client[settings.MONGODB_DB]`，默认库 `agent_state`
  （`src/kernel/config/base.py:183-190`）。
- sessions / traces 集合名可配置（`MONGODB_SESSIONS_COLLECTION` 等），
  checkpoint 两张是硬编码默认名 → 面板里集合名必须从同一来源推导，不能散写字面量。

### 5. 部署形态：无认证内网
- `k8s/kunxiaozhi.yaml` 原注释："MongoDB StatefulSet — 无认证,内网 k8s 内访问"；
  `deploy/docker-compose.yml` 的 `mongod --wiredTigerCacheSizeGB 0.5` 亦无 `--auth`。
- `.env.example:76-80` 的 `MONGODB_USERNAME/PASSWORD` 为可选留空。
- → 权限不是障碍；但代码仍须对"有认证且权限不足"fail-soft（见决策 3）。

### 6. 配置页分类已存在
- `mongodb`、`checkpoint` 两个 category 后端前端都已定义：
  `src/kernel/schemas/setting.py:36,38`、`frontend/src/types/settings.ts:19,21`、
  `SettingsPanel.constants.ts:9-12`。→ 不需要新增 category。

## Key decisions（第一波 grill 确认）

- **Q1 → 只要空间。** 不做 `serverStatus`（连接数/QPS/WiredTiger 缓存）。
  理由：`serverStatus` 需 `clusterMonitor` 角色，而 `dbStats`/`collStats` 内置
  `read` 角色即有（MongoDB 官方 Built-In Roles）。只做空间 = 权限门槛最低、
  在 Atlas / 托管实例上也不会 403。压力维度留待后续任务。

- **Q2 → 生产无认证（k8s 内网）。** 与代码库证据一致（第 5 点）。
  但仍按最小权限假设编码，不依赖 admin-only 命令。

- **Q3 → 选开销小的那个（用户原话"那个压力小选那个"）。**
  决策：**`dbStats` + 逐集合 `$collStats`/`collStats` 的 `storageStats`，
  一律走 metadata 读取，绝不使用 `count: {}` 之外的扫描型统计。**
  - `dbStats` 读的是库级元数据，O(集合数)，不扫文档。
  - `$collStats.storageStats` 读 WiredTiger 元数据，**不扫全表**；
    与之相对，`countDocuments()` 会全表扫描 —— 明确禁止。
  - `collStats` 命令自 MongoDB 6.2 起弃用，生产为 8.2.5
    （`deploy/docker-compose.yml`、`k8s/kunxiaozhi.yaml` 均 `mongo:8.2.5`），
    → 首选 `$collStats` 聚合阶段，仅在其不可用时回退 `collStats` 命令。
  - 取数时机：**请求时实时查 + 前端手动刷新**，不加后台采样 worker。
    理由：元数据读取廉价，且新增 worker 会重蹈"多副本需要分布式锁"的复杂度。

- **Q4 → 面板要能解释"定时清理"的效果。**
  用户已有按天数配置的清理逻辑（`CHECKPOINT_CLEANUP_RETENTION_DAYS`，默认 30，
  下限 7）。面板必须把**空间**与**该配置**并置，否则数字无法转化为决策。
  → 除集合大小外，附带展示当前生效的保留期/开关状态，
  并给出"待清理积压"口径（按 `sessions.updated_at < cutoff` 计数，
  复用已有索引 `user_status_updated_idx`，`src/infra/session/storage.py:91`）。
  **不做**"立即清理"按钮：那会把只读面板变成写操作，范围与风险等级完全不同
  （已否决，见 Out of scope）。

- **决策：fail-soft，不 fail-closed。** 指标不可得时返回 `available:false`
  + 原因，前端显示"不可用"徽标（`SystemHealthSection` 的 `StatusBadge`
  已有 `unavailable` 分支可照抄），而不是抛 5xx 污染配置页。
  理由：这是诊断信息，不是业务依赖；配置页不能因为指标查询失败而打不开。

- **决策：权限沿用 `settings:manage`**，与 `/health/memory`、analytics 全线一致
  （`src/api/deps.py:349-367`）。不新增权限位。

## Assumptions（标注验证状态）

- ✅ 已验证：checkpoint 两张集合默认名（读 wheel 源码确认）。
- ✅ 已验证：生产 Mongo 8.2.5 且无认证（读 manifest/compose 确认）。
- ✅ 已验证：配置页可无侵入插卡（读 `SettingsPanel.tsx:674` 确认）。
- ⚠️ 未验证（无生产访问）：实际 checkpoints 集合体量级别。
  影响：仅影响文案单位选择，已用自适应单位（B/KB/MB/GB）规避。
- ⚠️ 未验证：是否存在分片集群。`$collStats` 在分片集合上**每分片返回一个文档**，
  必须对结果求和而非取首条 —— 实现须按"可能多文档"处理。

## Out of scope

- `serverStatus` 压力指标（连接数、opcounters、缓存命中率）—— Q1 明确排除。
- "立即清理"按钮 / 任何写操作 —— 只读面板，避免误触删数据。
- 后台定时采样与历史趋势曲线 —— 需要新集合与 worker，另立任务。
- 清理 traces/sessions 本身 —— 前序 grill 已定为禁区。
