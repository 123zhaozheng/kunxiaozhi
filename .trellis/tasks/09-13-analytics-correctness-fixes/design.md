# 统计模块正确性修复 — 设计

## 背景

`08-28-analytics-dashboard-rebuild` 交付后，review 发现头号目标「历史数字不可变」未达成，
且筛选后跨卡片数字对不上。本任务修复这些缺陷，不引入新功能。

## 最高约束：非破坏性上线（HARD REQUIREMENT）

用户明确要求：上线过程不得对 MongoDB 现有数据做破坏性变更。所有实现必须满足：

### 必须遵守

1. **禁止写迁移脚本**。项目无 Alembic/迁移系统（见 `.trellis/spec/backend/database-guidelines.md`），
   schema 演进一律靠「新字段带默认值 + 读取端回退」。
2. **禁止 `update_many` / `delete_many` / `drop` 改写或删除任何历史文档**。
   已存在的 `analytics_daily_snapshot`、`user_daily_activity`、`sessions`、`traces` 文档
   一律保持原样。
3. **新字段只能是追加的**，且读取端必须能处理「旧文档没有该字段」的情况，
   缺失时回退到旧语义，不得报错。
4. **索引只能 `create_index(..., background=True)` 幂等新增**，
   禁止 `drop_index`；禁止把已有非唯一索引改成 unique。
   新索引若与旧索引同名但定义不同，必须换新名字。
5. **API 不得删除或重命名现有字段/参数**；新增查询参数一律可选且默认值等于旧行为。
6. **前端不得依赖后端新字段必然存在**，需容忍灰度期间后端尚未升级。
7. **回滚安全**：新版本写入的数据，旧版本代码读到时不能崩溃。

### 本次涉及的数据形状变更（全部为追加）

| 集合 | 新增字段 | 缺失时回退 |
|------|---------|-----------|
| `analytics_daily_snapshot` | `active_session_ids: list[str]` | 回退用 `active_sessions` 整数（旧语义） |
| `analytics_daily_snapshot` | `new_session_ids: list[str]` | 回退用 `new_sessions` 整数 |
| `analytics_backfill_state` | `failed_dates: list[str]` | 缺失视为空列表 |

> 这些数组存的是**标识符**，不是会话内容，仍满足 PRD「不存任何会话内容」的隐私约束。

## 修复清单

### P0-A 快照层（snapshot.py / backfill.py / main.py）

| ID | 问题 | 方案 |
|----|------|------|
| A1 | `stop_renewal()` 同步 `.result()` 抛 `InvalidStateError`，锁永不释放 | 改 async + `await task`，照抄 `backfill.py:452-461` |
| A2 | 锁 key 只取 `dates[0]`，不同区间对同一天不互斥 | 逐日加锁 |
| A3 | 续租失败后仍继续写入 | `lock_lost` 标志，每轮检查，失败即中止 |
| A4 | 完整性判断只看日期不看维度，`$setOnInsert` 致永久缺数据 | **冻结一律按全量日期写入，忽略 filters**；筛选只在读取时应用 |
| A5 | 活跃/新建会话跨天求和重复计数 | 快照追加 `active_session_ids`/`new_session_ids`，跨天求并集再取 size；旧文档回退整数 |
| A6 | 冻结只遍历 granular_docs，无 trace 的会话漏掉 | `new_sessions` 独立聚合 sessions |
| A7 | 跨日无定时冻结 | 新增 `DailyFreezeWorker`，UTC+8 日界后冻结昨天；分布式锁+幂等+失败不阻塞启动 |
| A8 | 回填游标失败仍推进 | 仅当 activity+snapshot 均成功才推进；失败记入 `failed_dates` 重试 |
| A9 | 回填 new_sessions 硬编码 0 | 独立聚合 `sessions.created_at`；已硬删的仍为 0（既有事实） |
| A10 | `by_agent` 声明未实现 | 实现 agent 维度聚合 |
| A11 | `using_users` 忽略 persona/agent 筛选 | 有筛选时从已筛选集合算 distinct |
| A12 | fallback 把 new_sessions 写死 0 | 移除错误 fallback |

### P0-B API 层

| ID | 问题 | 方案 |
|----|------|------|
| B1 | 三个环图端点不接收筛选参数（前端已在传） | 补可选参数并下沉；**默认值 = 旧行为** |
| B2 | role 筛选从「区间内新建会话的用户」反推 | 直接查 users 集合 `roles=role_id` |
| B3 | 活跃高峰按 trace started_at 分桶 | `$unwind` events，按 `events.timestamp` 且只取 user:message |
| B4 | `/usage/by-user` 与 CSV 绕过快照 | 走与 summary 同源路径 |
| B5 | 超长区间无上限；`9999-12-31` OverflowError → 500 | 加最大天数上限；捕获溢出返回 400 |

### P1-C 前端

| ID | 问题 | 方案 |
|----|------|------|
| C1 | `todayString()` 用浏览器本地时区 | 绝对时间 +08:00 偏移后取 `getUTC*` |
| C2 | 会话数 KPI 主值用 new_sessions | 活跃为主值、新建为副行（PRD R4） |
| C3 | 主看板/钻取/Modal 请求竞态 | 请求序号或 AbortController |
| C4 | 一个接口失败整页失败 | `Promise.allSettled` + 分区块错误态 |
| C5 | 首卡 sparkline 与主值口径不一致 | 口径对齐 |
| C6 | 洞察栏未全部可钻取 | 活跃高峰可点击；新增使用者真正过滤首次使用 |
| C7 | 死代码残留 | 删除未引用类型/重复 formatter |

### P0-D 测试

| ID | 问题 | 方案 |
|----|------|------|
| D1 | 不可变性测试同义反复 | 重写为真实流程：查询→冻结→删除→再查→断言相等 |
| D2 | `D:/code/python/LambChat` 硬编码致 6 测试 error | 改正常 import |
| D3 | 覆盖缺口 | 补跨天去重、锁竞争、回填中断、时区边界、筛选一致性 |

## 不修（用户明确豁免）

原 review 第五大点「设计权衡」全部不动：历史登录人数不可恢复、activity best-effort 写入、
上期为 0 时增长率 100%、反馈区/新增使用者不跟随筛选。

## 验证

- `uv run pytest tests/api tests/infra -q` 不得比基线更差
- `uv run ruff check src/infra/analytics src/api/routes/analytics.py` 零违规
- `uv run mypy src/infra/analytics` 零违规
- `cd frontend && pnpm run lint && pnpm run build` 通过
- 新增测试必须能在**没有真实 MongoDB** 的环境下运行（mock），真实 Mongo 测试用 skipif 保护

## 跨代理契约：`read_or_freeze()` 返回结构

所有消费方按此契约编码。新增 key 必须存在（由 snapshot 层保证），值可为空列表。

```python
{
  "total": {"new_sessions": int, "active_sessions": int,
            "user_messages": int, "tokens": int},
  "trend": [{"date": str, "new_sessions": int, "active_sessions": int,
             "user_messages": int, "tokens": int}],
  "by_persona": [{"persona_preset_id": str|None, "persona_preset_name": str|None,
                  "new_sessions": int, "active_sessions": int,
                  "user_messages": int, "tokens": int, "active_users": int}],
  "by_agent":  [{"agent_id": str|None, "new_sessions": int, "active_sessions": int,
                 "user_messages": int, "tokens": int, "active_users": int}],
  "by_user":   [{"user_id": str, "user_messages": int, "tokens": int}],
  # 新增：支撑 /usage/by-user 与 CSV 走同源（B4）
  "by_user_persona": [{"user_id": str, "persona_preset_id": str|None,
                       "persona_preset_name": str|None,
                       "user_messages": int, "tokens": int,
                       "active_sessions": int, "new_sessions": int,
                       "last_active_at": datetime|None}],
  "active_users": int,
  "using_users": int,
}
```

### 去重语义（A5）

`total.active_sessions` / `by_persona[].active_sessions` 等**区间级**去重指标，
必须由 `active_session_ids` 跨天求**并集**后取 size，不得逐日整数求和。
旧快照文档无 `active_session_ids` 时，回退为逐日整数求和（旧语义），
并在该路径打一条 `logger.debug`，不得抛错。

## 文件所有权（禁止跨界写入）

| 代理 | 独占文件 |
|------|---------|
| S（快照核心） | `src/infra/analytics/snapshot.py`、`backfill.py`、`daily_freeze.py`(新)、`activity_storage.py`、`usage_query.py`、`src/api/main.py`、`src/infra/task/executor.py`、`src/infra/task/manager.py`、`src/api/routes/chat.py`、`tests/infra/test_analytics_snapshot*.py`、`tests/infra/test_analytics_backfill.py`、`tests/infra/test_analytics_daily_*.py` |
| A（API 层） | `src/api/routes/analytics.py`、`src/infra/analytics/storage.py`、`manager.py`、`src/kernel/schemas/analytics.py`、`tests/api/routes/test_analytics_*.py`、`tests/infra/test_analytics_usage_query.py` |
| F（前端） | `frontend/src/**` |

`date_range.py` 为只读共享，两侧都不得修改（除 B5 由 A 代理加溢出保护）。
