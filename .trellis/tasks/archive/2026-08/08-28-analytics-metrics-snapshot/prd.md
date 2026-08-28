# 子1：口径与日快照层

父任务：`.trellis/tasks/08-28-analytics-dashboard-rebuild`
设计依据：父任务 `design.md` §2、§3.1、§3.2、§4

## Goal

让统计数字具备两个性质：**历史不可变**、**活跃用户可回溯**。这是纯后端数据层工作，
不改任何 API 契约，不动前端。

## Background

统计层对现存文档实时聚合，而用户删会话是硬删（`src/infra/session/manager.py:172`
`delete_one` + `src/infra/session/trace_storage.py:1704` `delete_many`），
所以昨天的数字今天会变小。登录只写 `users.updated_at`
（`src/infra/user/storage.py:714`），这是只存最后一次的字段，无法回答"8/25 有多少人登录"。

## Requirements

### R1 `user_daily_activity` 集合

```
{ user_id, date: "YYYY-MM-DD"(UTC+8), sources: ["login"|"message"],
  first_at, last_at }
唯一索引 (user_id, date)；查询索引 (date, user_id)
```

- 新增 `src/infra/analytics/activity_storage.py`，遵循项目 Storage 约定
  （per-call 实例化、async、返回 Pydantic 模型；见 `.trellis/spec/backend/database-guidelines.md`）。
- 写入 `login`：在 `UserStorage.touch_updated_at` 内追加一次 upsert。
  该方法是三条登录路径的共用出口（`src/infra/auth/oauth.py:264`、
  `src/infra/auth/oa_login.py:68`、`src/infra/user/manager.py:94`），只需改一处。
  **保留** `updated_at` 的原有写入，不得删除。
- 写入 `message`：在 trace 写入 `user:message` 的路径上追加一次 upsert。
- 两处写入都必须是**尽力而为**：失败只记 warning，绝不影响登录或发消息主流程。

### R2 `analytics_daily_snapshot` 集合

```
{ date: "YYYY-MM-DD"(UTC+8), user_id, persona_preset_id, agent_id,
  new_sessions, active_sessions, user_messages, tokens,
  last_active_at, frozen_at }
唯一索引 (date, user_id, persona_preset_id, agent_id)
查询索引 (date, persona_preset_id) / (date, agent_id)
```

- 只存计数，**不得**存会话标题、消息文本、任何会话内容。
- 新增 `src/infra/analytics/snapshot.py`，实现 `read_or_freeze(filters)`：
  今天实时聚合不写快照；历史天有快照读快照、无快照则实时聚合并写入（拿 Redis 锁、幂等）；
  合并两段结果返回。
- 冻结写入用 upsert 且**不覆盖已有快照**（`$setOnInsert` 语义），保证同一天只冻一次。
- 快照缺失或读取异常时降级为实时聚合，不返回空。

### R3 日期区间工具

新增 `src/infra/analytics/date_range.py`：

```python
def resolve_range(start: str, end: str) -> tuple[datetime, datetime]
    # "2026-08-22","2026-08-28" → [08-22T00:00+08:00, 08-29T00:00+08:00)  半开区间
def previous_range(start: str, end: str) -> tuple[str, str]
    # 等长前推
def day_buckets(start: str, end: str) -> list[str]
```

- `storage.py:52` 的 `_BUCKET_TZ` 常量迁到此处，全仓单一定义。
- `$match` 用半开区间 `{"$gte": ..., "$lt": ...}`，替换现有 `$lte` 的毫秒边界。

### R4 回填 worker

新增 `src/infra/analytics/backfill.py`，形状照 `src/infra/session/backfill.py`
（`SessionSearchBackfillWorker`），挂载方式照 `src/api/main.py:492`：

- Redis 分布式锁 `analytics:backfill:lock`，多副本只有一个执行。
- 分批处理，批间 `asyncio.sleep` 让出事件循环。
- 幂等 upsert，重跑不产生重复。
- 进度标记写 `analytics_backfill_state`，下次启动跳过已完成部分。
- 失败只 `logger.warning`，**绝不阻塞启动**。
- 从现存 traces 推导历史每天的 `user_daily_activity`（只能写 `source=message`）
  与 `analytics_daily_snapshot`。
- 早于 traces 最早记录的日期返回零，不报错。

### R5 口径字段

`read_or_freeze` 的返回结构要能同时给出：

| 字段 | 含义 |
|------|------|
| `active_users` | 区间内 `user_daily_activity` 的 `distinct(user_id)`（登录口径） |
| `using_users` | 其中 `"message" in sources` 的 `distinct(user_id)` |
| `new_sessions` / `active_sessions` / `user_messages` / `tokens` | 见父任务 prd 口径表 |

当 `filters` 带 persona 或 agent 筛选时，`active_users` 取 `using_users` 的值
（登录记录无 persona 归属，见父任务 design §3.4）。

## Out of Scope

- API 端点参数改造与响应结构（子2）
- 洞察栏数据（子2）
- 任何前端改动（子3）
- 快照 TTL / 保留期（永久保留）
- 会话软删除改造

## Acceptance Criteria

- [ ] `tests/infra/test_analytics_snapshot_immutable.py`：构造某历史日期的数据 → 查询一次
      → 删掉该日一个会话及其 traces → 再查询，两次数字**相同**；同一测试断言"今天"的数字
      在同样操作后**会变**（证明只冻结历史）。
- [ ] `tests/infra/test_analytics_daily_activity.py`：
      - `touch_updated_at` 后 `user_daily_activity` 有 `source=login` 记录，
        且 `users.updated_at` 仍被写入（向前兼容）。
      - 同一用户同一天多次登录只有一条记录，`sources` 去重。
      - 活跃用户 = 登录去重人数；使用用户 = 发过消息去重人数；前者 ≥ 后者。
      - 活跃度写入失败时登录仍成功（注入异常断言不抛出）。
- [ ] `tests/infra/test_analytics_date_range.py`：
      `resolve_range("2026-08-22","2026-08-28")` == `(2026-08-22T00:00+08:00,
      2026-08-29T00:00+08:00)`；`previous_range` 等长前推正确（7天/1天/30天/自定义各一例）。
- [ ] `tests/infra/test_analytics_backfill.py`：幂等（跑两次结果一致）、
      锁未获取时返回 0、异常时不抛出、早于最早 trace 的日期返回零。
- [ ] 现有 `tests/infra/test_analytics_usage_query.py`、
      `test_analytics_active_user_consistency.py`、`test_analytics_usage_persona_parity.py`
      仍全绿（不得为了新逻辑改动这些断言，除非断言本身与新口径冲突且在 PR 说明中解释）。
- [ ] `uv run ruff check .`、`uv run mypy src/`、
      `uv run pytest tests/infra -q` 全绿。
- [ ] 快照文档中不含任何会话内容字段（测试用字段白名单断言）。
