# Design: 统计页小时级预聚合

Base: `feat/user-storage-management` @ `fbd9e737`
Grill: `.trellis/grill/analytics-usage-hourly.md`

## 数据模型

集合 `usage_hourly`（新建）:

```python
{
  "bucket": datetime,          # UTC 整点截断: dt.replace(minute=0,second=0,microsecond=0)
  "user_id": str | None,
  "model": str | None,         # 快照回填为 None
  "persona_preset_id": str | None,
  "agent_id": str | None,
  "source": "live" | "snapshot",
  "tokens": int,               # $inc 累加
  "user_messages": int,        # $inc 累加
  "runs": int,                 # $inc 累加
  "updated_at": datetime,
}
```

索引：

| 名称 | 键 | 用途 |
|---|---|---|
| `usage_hourly_unique_idx` | bucket+user_id+model+persona_preset_id+agent_id+source | **唯一**，保证 `$inc` 原子累加 |
| `usage_hourly_bucket_model_idx` | bucket(-1)+model | by-model 查询 |
| `usage_hourly_bucket_user_idx` | bucket(-1)+user_id | by-user 查询 |

> MongoDB 唯一索引把 `null` 视为一个具体值，故 `model=None` 的回填行与
> `model="gpt-4o"` 的增量行**不会**冲突，二者共存于同一唯一键空间。
> `source` 入键是刻意的：回填行与增量行永不互相 `$inc`，是 R2 幂等的基础。

## 原子累加（AC2）

```python
await coll.update_one(
    {"bucket": b, "user_id": u, "model": m,
     "persona_preset_id": p, "agent_id": a, "source": "live"},
    {"$inc": {"tokens": t, "user_messages": msgs, "runs": r},
     "$set": {"updated_at": utc_now()}},
    upsert=True,
)
```

批量用 `bulk_write([UpdateOne(...)], ordered=False)`。

**明确不采用参照实现的写法**：new-api 是 `First` → 有则 UPDATE / 无则 INSERT
（`usedata.go:108-123`），且聚合维度无唯一键，多节点并发会插重复逻辑行。
MongoDB 的 `$inc`+`upsert`+唯一索引是单条原子操作，无此问题。

## 历史回填（R2 / AC3）

数据源 `analytics_daily_snapshot`，其分组键为
`(date, user_id, persona_preset_id, agent_id)`，度量含 `tokens`、`user_messages`
（`snapshot.py:435-453`）。**无 model 维度**。

映射：一条快照行 → 一条 `usage_hourly` 行，`bucket` 取该日 00:00 UTC，
`model=None`，`source="snapshot"`。

幂等策略：回填使用 **`$setOnInsert`（非 `$inc`）**。同一天重复跑，
文档已存在则不改写 → 天然幂等，且与 live 行因 `source` 不同而隔离。

分批：按日期游标推进，每批 N 天，记录进度（复用既有 worker 范式：
FastAPI lifespan + Redis NX 锁，参照 `analytics/daily_freeze.py`、
`checkpoint/cleanup_worker.py`）。可断点续跑。

## 模型维度降级（R3 / AC4）—— 本设计最关键处

快照回填行 `model=None`。若 by-model 直接 `$group` by model，
这些行会聚成一个 `null` 桶，视觉上等于"少了一大块"或被误读为 0。

契约：`/tokens/by-model` 响应增加

```json
{"items": [...], "partial": true, "model_data_since": "2026-09-20"}
```

- `model_data_since` = 最早的 `source="live"` 桶日期
- 当请求区间起点早于该日期时 `partial=true`
- 前端据此显示提示（本任务只保证后端契约诚实；前端展示可最小化）

**绝不**把 `model=None` 的 tokens 静默丢弃或计为 0。

## 读路径（R5 / AC5）

`AnalyticsStorage` 新增 `usage_hourly` 访问层。改造顺序（风险从低到高）：

1. `/tokens/by-model` —— 当前完全不走快照、最慢（实测 2060ms@90d），收益最大
2. 趋势类按天聚合 —— `$group` by `$dateToString(bucket)`

每个查询包一层：开关关 / 集合空 / 异常 → **回落现有实现**，不抛错。
开关 `ANALYTICS_USAGE_HOURLY_ENABLED` 默认 **False**，需显式开启（可逆）。

## 配置

沿用 `_definitions_extra.py` 既有 dict schema（参照 `CHECKPOINT_CLEANUP_*`）：

- `ANALYTICS_USAGE_HOURLY_ENABLED`（BOOLEAN，默认 False）
- `ANALYTICS_USAGE_HOURLY_FLUSH_SECONDS`（NUMBER，默认 300，仿 new-api 5 分钟）
- `ANALYTICS_USAGE_HOURLY_BACKFILL_BATCH_DAYS`（NUMBER，默认 7）

category 复用 `ANALYTICS`（若不存在则用既有最贴近者，勿新增 category）。

## 测试（R6）

`tests/infra/analytics/test_usage_hourly.py`：

1. `$inc` 并发累加不丢更新（模拟多次 bulk_write）
2. 回填幂等：同一天跑两遍，值不翻倍
3. 回填行与 live 行不互相污染（source 隔离）
4. by-model 对快照区间返回 `partial=true` + `model_data_since`
5. 开关关闭 / 集合缺失 → 回落旧路径，不抛异常

沿用既有 fake collection + monkeypatch 模式，不引入 mongomock。

## 风险

| 风险 | 缓解 |
|---|---|
| 回填期间双写导致重复计数 | `source` 入唯一键；回填用 `$setOnInsert` 而非 `$inc` |
| 历史无 model 维度被误读 | `partial` + `model_data_since` 显式契约，前端可见 |
| 新路径口径漂移 | 默认关闭；回落分支保留；口径测试对比新旧 |
| 回填拖慢生产 | 分批 + Redis 锁 + 可断点；只读快照（小表）不扫 traces |
