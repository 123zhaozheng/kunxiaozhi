# 统计页性能：小时级预聚合表

## Goal

后台统计页每次打开都从原始 `traces` 现算，单个 widget 实测 680ms（30 天）到
2060ms（90 天），首屏并发 9 个请求互相排队，因此很卡。

目标：引入小时级预聚合集合 `usage_hourly`，把读路径从"扫 733 MB 原始数据"
改为"读 10 MB 小表"，**在不改变统计口径的前提下**大幅提速。

实测依据见 `.trellis/grill/analytics-usage-hourly.md`（本地 MongoDB 8.0.4，
60,000 trace / 733.5 MB 数据集，30× 提速）。

## Requirements

### R1 — 预聚合集合

- 新集合 `usage_hourly`，桶粒度为**小时**（UTC 整点截断）。
- 维度：`bucket` + `user_id` + `model` + `persona_preset_id` + `agent_id`。
- 度量：`tokens`、`user_messages`、`runs`（累加值）。
- **复合唯一索引**覆盖全部维度键，配合 `$inc` + `upsert:true` 实现原子累加。
  不得使用"先查后写"（read-modify-write），多副本下会产生重复行。
- 另建 `(bucket, model)`、`(bucket, user_id)` 查询索引。

### R2 — 历史回填（用快照，不扫 traces）

- 回填数据源为既有 `analytics_daily_snapshot`（永久保留、历史值已冻结不可变）。
- 回填为**分批、可断点续跑**的后台任务，不得一次性全量跑。
- 幂等：重复执行不得重复累加（用 `$setOnInsert` 或按 `source` 标记区分，
  绝不对同一天重复 `$inc`）。

### R3 — 模型维度的诚实降级（关键）

快照**没有 model 维度**（分组键仅 date/user/persona/agent）。因此：

- 回填行以 `model = null`、`source = "snapshot"` 写入。
- 增量行以真实 model、`source = "live"` 写入。
- `/tokens/by-model` 查询到含快照回填的区间时，**必须显式表达**该区间无模型
  维度（例如返回 `partial: true` + 起始可用日期），**不得**把缺失静默计为 0
  或悄悄少算。

### R4 — 增量写入

- 新产生的用量按小时桶累加进 `usage_hourly`。
- 允许分钟级延迟（用户明确无强实时要求）→ 采用后台批量 flush，
  避免对聊天主链路造成同步写放大。
- flush 间隔、开关可配置，沿用既有 `SETTING_DEFINITIONS` 体系。

### R5 — 读路径切换与可回滚

- 统计端点优先读 `usage_hourly`；开关关闭或数据不可用时**回落现有路径**。
- 统计口径不变：相同区间相同筛选，新旧路径结果应一致（模型维度除外，见 R3）。

### R6 — 测试

- 原子累加正确性（并发 `$inc` 不丢更新）。
- 回填幂等（跑两遍结果相同）。
- 模型维度降级（历史区间返回 partial 标记而非错误数字）。
- 读路径回落（预聚合不可用时不报错）。

## Acceptance Criteria

- [ ] AC1 `usage_hourly` 存在且具备覆盖全维度的复合唯一索引。
- [ ] AC2 写入为 `$inc` + `upsert` 原子操作，代码中无"先查后写"累加。
- [ ] AC3 历史回填仅读快照，不扫 `traces`；分批且可重复执行而不重复计数。
- [ ] AC4 `/tokens/by-model` 对快照回填区间显式返回 partial 标记，不静默给 0。
- [ ] AC5 预聚合关闭或缺数据时，统计页回落旧路径且不报错。
- [ ] AC6 后端测试通过；`ruff`/`mypy` 对改动文件通过。

## Constraints

- 本 thread 对上游仓库只读 → 补丁交付。
- 不修改统计冻结语义与 `analytics_daily_snapshot` 写入逻辑。
- 不删减 `traces.events`。

## Out of Scope

- 前端 9 个首屏请求合并为批量端点（可另立任务）
- Redis 响应缓存（可作为后续增量）
- 统计口径变更
