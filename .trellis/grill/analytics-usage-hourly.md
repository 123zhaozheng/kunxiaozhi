# Grill: 统计页性能——小时级预聚合表

Date: 2026-09-20
Status: 方案已定，进入 Phase 1
Target: github.com/123zhaozheng/kunxiaozhi（本 thread 只读，补丁交付）
参照: QuantumNous/new-api @ 972aed1（`model/usedata.go`）

## Intent

统计页卡。管理员打开后台统计要等数秒。目标：不改统计口径的前提下，
把首屏从"每次扫原始 traces 现算"改为"读预聚合小表"。

## 实测证据（非估算）

本地起 MongoDB 8.0.4，灌 60,000 trace / 733.5 MB（含真实长尾：85% 小文档、
1% 超大文档 1000-4000 事件），复刻生产索引后实测：

| 查询 | 现状 | 预聚合 | 提速 |
|---|---:|---:|---:|
| `/tokens/by-model` 30d | 680 ms | 22 ms | 31× |
| `/tokens/by-model` 90d | 2060 ms | 68 ms | 30× |
| `usage_facts`（summary）30d | 722 ms | — | — |
| persona 过滤 90d | — | 70 ms | — |
| 按天趋势 90d | — | 94 ms | — |

预聚合表 **10.1 MB** vs traces **733.5 MB**。
`explain` 证实 90d 查询 `totalDocsExamined=60000`（全集合）。

## 卡的三个叠加原因（代码证据）

1. **每次从 raw traces 现算。** 单 trace 最多 10,000 事件
   （`kernel/config/base.py:60`），平均文档 12.5 KB。
   `$unwind "$events"` 见 `analytics/storage.py:455`、`:1822`；
   `$filter/$size/$map` 见 `analytics/usage_query.py:61-89`。
2. **首屏并发 9 个请求**（`AnalyticsPanel.tsx:211-221`），多端点重复算同一区间。
3. **今天永不走快照**（`snapshot.py:793` `if today in dates` → 回扫原始数据）；
   且 `/tokens/by-model`（`storage.py:430-474`）与 `/users/active`
   （`storage.py:1991-2003`）**整个区间**都不走快照。

隐蔽问题：**persona 过滤器索引失效**。过滤发生在 `$lookup sessions` 之后
（`usage_query.py:33-58`），`metadata_preset_started_at_idx` 无法提前收窄。

## new-api 的做法（已读源码确认）

- `quota_data` 按**小时**分桶：`createdAt - createdAt%3600`（`usedata.go:78-85`）
- 维度：user_id/username/model_name/use_group/token_id/channel_id/node_name
  （`usedata.go:13-26`），已反规范化，查询无需 JOIN
- 请求线程只写进程内 map（`usedata.go:51-76`），后台 goroutine
  **每 5 分钟** flush（`constants.go:28-30`、`usedata.go:41-49`）
- Dashboard 只查这张小表（`usedata.go:173-182`），**从不碰 raw logs**

## Key decisions

- **决策：采用小时桶预聚合，不采用 new-api 的 `First→Create/Update` 写法。**
  new-api 没有聚合维度唯一键，也无 `clause.OnConflict`（`usedata.go:108-135`），
  多节点并发理论上会插重复逻辑行（它靠读取时 `SUM...GROUP BY` 兜底）。
  本仓库是多副本部署 → 必须用 **复合唯一索引 + `$inc` + `upsert:true`**，
  单条原子操作，比参照实现更干净。

- **决策：历史数据用「已有每日快照」回填，不扫全量 traces。**（用户要求，已验证可行）
  `analytics_daily_snapshot` 无 TTL、永久保留（全仓无 snapshot TTL 声明），
  且历史值经 `$setOnInsert` 冻结后不可变（`snapshot.py:496,512`）。
  回填只需读快照 → 写 usage_hourly，避免我实测中"Python 侧回填 6 万 trace
  耗时 10 分钟+"的问题。

- **⚠️ 关键限制（必须写进设计）：快照没有 model 维度。**
  快照分组键只有 `(date, user_id, persona_preset_id, agent_id)`
  （`snapshot.py:435-453`），全文件 `model` **零匹配**；
  而 `/tokens/by-model` 需要 `events.data.model_id|model`
  （`storage.py:74-89` `_model_label_expr`）。
  → 由此推出**双粒度**设计（见下），否则回填出来的表支撑不了 by-model。

- **决策：双粒度写入。**
  - `usage_hourly`：新增量按**小时 + 含 model 维度**实时 `$inc`（前向数据完整）
  - 历史回填：从快照按**天 + model=null** 写入，标记 `source="snapshot"`
  - 读取时：by-model 查询只覆盖"回填分界点之后"的区间，之前的区间显式
    返回"历史数据无模型维度"而非静默给错数。
  **绝不把 model 维度缺失伪装成 0。**

- **决策：延迟可接受**（用户明确"没有很多的延迟要求"）。
  → 采用后台批量 flush（仿 new-api 5 分钟），而非每请求同步写，
  降低对聊天主链路的写放大。

- **决策：新表与现有 snapshot 并存，不删不改 snapshot 逻辑。**
  快照是回填数据源，也是既有统计的正确性基线；本任务只做"加速读"，
  不动"冻结口径"。可回滚：开关关闭即回落现有路径。

## Assumptions

- ✅ 已验证：快照永久保留、历史值不可变、无 model 维度。
- ✅ 已验证：预聚合方案在 733 MB 数据集上 30× 提速。
- ⚠️ 未验证（无生产访问）：生产 traces 真实体量与快照覆盖的最早日期。
  影响：回填耗时与 by-model 历史可用区间；设计上以"分界点"显式表达。

## Out of scope

- 修改统计口径 / 冻结语义
- 前端 9 请求合并为批量端点（可另立任务）
- 删除或精简 traces.events（前序 grill 已定为禁区）
