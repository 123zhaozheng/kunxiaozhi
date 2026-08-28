# 子2：统计接口整合与清理

父任务：`.trellis/tasks/08-28-analytics-dashboard-rebuild`
设计依据：父任务 `design.md` §3.2、§3.3、§3.4、§6
前置：子1（`08-28-analytics-metrics-snapshot`）已完成

## Goal

把 analytics 的对外契约收敛成一套：纯日期参数、UTC+8 单一日界、KPI 与洞察栏所需数据一次给全，
并删掉无人调用的冗余接口与重复聚合。

## Requirements

### R1 日期参数改造

- 所有 analytics 端点的 `start`/`end` 从 ISO 时间戳改为 `YYYY-MM-DD` 字符串，
  正则校验，非法值返回 400。
- 内部统一走子1 的 `resolve_range`，展开为 `[start 00:00+08:00, end+1d 00:00+08:00)`。
- 分桶时区与过滤时区必须同源（消除 `storage.py:206` 按 UTC 过滤、`:214` 按
  `Asia/Shanghai` 分桶的错开）。

### R2 summary 增加对比字段

```python
class UsageSummaryResponse(BaseModel):
    active_users: int       # 登录去重人数；带 persona/agent 筛选时取 using_users
    using_users: int        # 发过消息的去重人数
    new_sessions: int
    active_sessions: int
    user_messages: int
    total_tokens: int
    previous: UsageSummaryPrevious | None   # 等长前推区间的同名指标
```

对比区间由 `previous_range` 算出，与主区间同筛选。
人均消息与会话均 Token **不**加字段，由前端除算。

### R3 洞察端点

```http
GET /api/analytics/usage/insights?start&end&persona_preset_id&agent_id&role_id
→ {
    peak: { weekday, hour, user_messages } | null,
    top_token_users: [{ user_id, username, display_name, tokens }],   # Top3
    fastest_growing_persona: { persona_preset_id, persona_preset_name,
                               current, previous, growth_pct } | null,
    new_users: int,
  }
```

- `peak` 口径从现在的"按会话创建时间"改为"按用户消息时间"，与其它指标同源。
- `new_users` = 首次使用日（`user_daily_activity` 中最早的 `message` 日期）落在本期的人数。
- 四项全部可由现有数据算出，不得引入 LLM 生成内容。
- 数据不足时返回 `null` / 空数组 / 0，不报错。

### R4 热力图

热力图本体不再上页面。`/users/heatmap` 端点保留但改为服务于 `insights.peak`
的口径（按用户消息时间）；若确认前端不再调用它，则并入 `insights` 后删除该端点。
判定依据：子3 完成后 `grep` 全仓无引用。

### R5 清理（每项删除前必须给出无引用证据）

| 位置 | 依据 |
|------|------|
| `/api/analytics/tokens/by-preset` | 名字说 preset 实际按 `agent_id` 分组；已被 `usage/by-persona` 取代 |
| `/sessions/list`、`/sessions/export.csv` 的 `preset_id` 兼容分支 | 前端只传 `persona_preset_id` 后无调用方 |
| 被 `usage/*` 取代的旧聚合方法 | 同一指标两套 pipeline |
| `users.updated_at` 索引（`src/infra/analytics/storage.py:159`） | 活跃用户改走 `user_daily_activity` 后该索引仅为 analytics 而建。**字段本身保留** |

删除后 `.trellis/spec/backend/analytics-persona-and-lists.md` 的签名清单需同步更新
（放到子4 的 spec 沉淀里做）。

## Out of Scope

- 前端任何改动（子3）
- 快照层实现（子1）
- 反馈指标公式
- token 拆输入/输出

## Acceptance Criteria

- [ ] `tests/api/routes/test_analytics_date_params.py`：
      `start=2026-08-22&end=2026-08-28` 展开为
      `[2026-08-22T00:00+08:00, 2026-08-29T00:00+08:00)`；
      非法日期（`2026-13-01`、`abc`、`end < start`）返回 400。
- [ ] `tests/api/routes/test_analytics_usage_routes.py` 扩展：
      `summary.previous` 为等长前推区间的数字；带 persona 筛选时
      `active_users == using_users`；无筛选时 `active_users >= using_users`。
- [ ] `tests/api/routes/test_analytics_insights_route.py`：四个字段的口径断言 +
      数据不足时返回空值不报错 + 无 `settings:manage` 权限时 403。
- [ ] `insights.peak` 由用户消息时间算出（测试用只有会话没有消息的数据断言 peak 为 null）。
- [ ] 清理清单每项在任务目录留下 `research/removal-evidence.md`，记录
      `grep` 命令与零命中输出。
- [ ] `uv run ruff check .`、`uv run mypy src/`、
      `uv run pytest tests/api tests/infra -q` 全绿。
