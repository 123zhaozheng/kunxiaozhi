# 统计看板重构（父任务）

## Goal

把 `/analytics` 统计看板重做成一个**数字可信、版面可用**的管理报表：
领导打开就能回答"多少人在用、用得多不多、烧了多少 token、谁在用哪个 persona"，
并且**同一天的数字明天再看还是同一个数字**。

## Background

上一轮（`08-24-usage-report-metrics`，未提交）统一了后端口径并新增了 `usage/*` 五个端点，
口径部分是对的且有单测锁住。但仍留下两类问题：

### 问题一：历史数字会变（用户明确报的缺陷）

现象：当天看会话数正常，第二天再看前一天的会话数变少了。

根因（本次已定位，不是时区问题）：

| 位置 | 行为 | 后果 |
|------|------|------|
| `src/infra/session/manager.py:172` `delete_session` | `delete_one` 掉 sessions 文档 | 会话数减少 |
| `src/infra/session/trace_storage.py:1704` `delete_session_traces` | `delete_many` 掉该会话全部 traces | 用户消息数、token 一起减少 |
| `src/infra/session/manager.py:164` `clear_session_messages` | 删 traces、保留会话 | 会话数不变但消息数/token 减少 |
| `src/infra/analytics/storage.py` 全部聚合 | 对**现存文档**实时聚合 | 用户今天清理昨天的对话，昨天的报表就跟着变小 |

已排除：无 TTL 索引、无定时清理任务、无响应缓存（见
`research/session-count-shrink-root-cause.md`、`research/backend-analytics-current-state.md`）。

附带确认的真 bug（不是上述根因，但要修）：
前端用浏览器本地时区算日边界（`frontend/src/components/panels/AnalyticsPanel.tsx:98-107`
的 `setHours` + `toISOString`），后端按 UTC 过滤却按 `Asia/Shanghai` 分桶
（`src/infra/analytics/storage.py:208` vs `:214`）。浏览器不在 UTC+8 时边界错 8 小时。

### 问题二：版面信息密度低、结论不明显

现有版面是"筛选行 → 4 张卡 → 用户区 → 会话区 → token 区 → 明细表 → 反馈区"的纵向罗列，
一屏看不完，也没有"结论层"。用户提供了三张参考排版，选定图 3 为骨架。

## 已确认的口径定义（本任务唯一真相）

| 指标 | 定义 |
|------|------|
| 活跃用户数 | **区间内登录过的人**（去重）。未筛选时用此口径；卡片副行显示"其中使用 N 人" |
| 使用用户数 | 区间内发过 `user:message` 的人（去重）。选了 persona/智能体筛选时，活跃用户卡切换为此口径并改标题为「使用用户」 |
| 用户消息数 | 区间内 `user:message` 事件条数（不含 `message:chunk`、`tool:call`、`token:usage`） |
| 新建会话数 | `sessions.created_at` 落在区间内 |
| 活跃会话数 | 区间内有 `user:message` 的会话去重计数 |
| Token | 区间内 `token:usage` 事件 `data.total_tokens` 求和（只给合计） |
| 人均消息 | 用户消息数 ÷ **使用用户数**（分母不是登录人数；卡片带悬停说明） |
| 会话均 Token | Token ÷ 活跃会话数 |
| persona 归属 | 会话 `metadata.persona_preset_id` 优先，缺失时回退 trace `metadata.persona_preset_id` |
| 时间归属 | 消息/token 按 `traces.started_at`；新建会话按 `sessions.created_at`；全链路 UTC+8 |
| 对比基准 | 等长前推一个区间。近 7 天 → 前 7 天；今天 → 昨天；近 30 天 → 前 30 天；自定义同理。文案用「较上一区间」 |

## Requirements

### R1 历史数字不可变（日快照）

新增日快照集合，粒度 **日期 × 用户 × persona × 智能体**，只存计数不存任何会话内容。
读取规则：

```
区间内每一天 d:
  d == 今天（UTC+8） → 实时聚合，不写快照
  快照已存在(d)      → 读快照
  否则               → 实时聚合 + 写快照（分布式锁、幂等）
```

向上汇总可得全局总量、按 persona、按智能体、按人、以及任意筛选组合，
因此顶部筛选后的历史数字同样稳定。

启动时挂一个回填 worker（形状照 `src/infra/session/backfill.py` + `src/api/main.py:492`）：
分布式锁 + 分批 + 幂等 + 失败只记日志不阻塞启动，完成后写标记，下次启动跳过。
用户上线流程不变，拉镜像重启即可。

### R2 登录活跃记录（向前兼容）

现状：登录只把 `users.updated_at` 刷成当前时间（`src/infra/auth/oauth.py:264`、
`src/infra/auth/oa_login.py:68`），这是"只存最后一次"的字段，无法回答"8/25 有多少人登录"。

新增日粒度活跃记录集合（纯新增，不改不删任何现存数据、不动 `users.updated_at`）：
登录成功写 `source=login`，用户发消息写 `source=message`。
同一个回填 worker 从现存 traces 把历史每天"发过消息的人"补进去，
使历史区间有数字且不再变动。

### R3 接口整合与日期参数

- 查询参数从 ISO 时间戳改为**纯日期**（`start=2026-08-22&end=2026-08-28`），
  后端展开为 `[start 00:00 +08:00, end+1d 00:00 +08:00)`，分桶同样用 +08:00。
  日界只在一处定义。
- 补齐右侧洞察栏所需数据：活跃高峰（星期×小时最大格，口径从"按会话创建时间"改为
  "按用户消息时间"）、Token 大户 Top3（按人）、增长最快 persona（本期 vs 上期）、
  本期新增使用者（首次使用日落在本期）。
- 热力图本体不上页面，只用其数据算"活跃高峰"结论句。
- 删除无人调用的冗余代码（详见 R5）。

### R4 前端版面按图 3 重写

```
┌ 标题 + 日期区间 + [今天][7天][30天][自定义] + 筛选 ┐
│ 数据更新时间                                       │
├ KPI ×6（每张带 sparkline + 较上一区间）             ┤
├─────────────────────────┬───────────────┤
│ 核心趋势（多指标同图对比，大折线）      │ 硬数据洞察栏   │
├──────────┬──────────┬──────────┬────────┤
│ 按智能体Top5 │ 按PersonaTop5 │ 模型TokenTop5 │ 反馈概览 │
├─────────────────────────────────────┤
│ 使用明细（搜索 + 导出CSV + 分页）                   │
└─────────────────────────────────────┘
```

- KPI 六张：活跃用户（副行"其中使用 N 人"）/ 会话数（活跃为主值、副行"新建 M"）/
  用户消息 / 总 Token / 人均消息 / 会话均 Token。
- 洞察栏四条：活跃高峰、Token 大户 Top3、增长最快 Persona、本期新增使用者。每条可点击钻取。
- 第三行四张卡全保留；环图中心数字与对应 KPI 卡**严格相等**。
- 使用明细只列有使用行为的人，表头右上角显示"N 人使用 / 共 M 人登录"。
- 配色/圆角/阴影全部沿用项目现有 theme token，不引入参考图的配色。
- 保留钻取抽屉，但把里面手填 preset id 的输入框换成与顶部同源的下拉。
- 中英日韩俄五语言文案齐备。

### R5 清理冗余

「没人调用就删」。已识别候选（实施前必须先验证无引用）：

| 位置 | 问题 |
|------|------|
| `/api/analytics/tokens/by-preset` | 名字说 preset，实际按 `agent_id` 分组；已被 `usage/by-persona` 取代 |
| `/sessions/list`、`/sessions/export.csv` 的 `preset_id` 参数分支 | 已废弃的兼容分支 |
| 被 `usage/*` 取代的旧聚合方法 | 重复口径 |
| 前端 `UsageByPersona` 客户端方法 | 定义了从未调用 |
| 前端重复的日期格式化工具、重复的时间范围按钮组 | `AnalyticsPanel` 与 `PresetAnalyticsModal` 各写一份 |
| 孤儿 i18n key | 无引用 |

## Out of Scope

- 多时区支持（固定 UTC+8）
- token 拆输入/输出、费用估算
- 快照保留期 / TTL（永久保留，只存计数）
- 会话软删除改造（不动现有删除行为）
- 反馈指标公式重构（只调整卡片位置）
- 零使用员工名单（"谁没在用"）
- 把汇总字段落到 trace 文档 + 历史回填

## 子任务

| 子任务 | 交付物 |
|--------|--------|
| `08-28-analytics-metrics-snapshot` | R1 + R2：日快照层、登录活跃记录、回填 worker |
| `08-28-analytics-api-consolidation` | R3 + R5 后端部分：纯日期参数、洞察栏数据、删无用接口 |
| `08-28-analytics-dashboard-ui` | R4 + R5 前端部分：图 3 版面重写、i18n 五语 |
| `08-28-analytics-verification` | 全量验证、点击清单、spec 沉淀 |

执行顺序串行：snapshot → api-consolidation → dashboard-ui → verification。

## Acceptance Criteria（跨子任务）

- [x] 同一历史日期在两次不同时刻查询（其间删掉一个该日的会话）返回**相同**数字；
      有单测用"聚合后删会话再查"断言快照生效。
      （`test_analytics_snapshot_immutable` 锁住；浏览器复验见 `manual-verification.md` §5）
- [x] 未筛选时活跃用户卡 = 区间内登录去重人数；副行 = 发过消息去重人数；
      选了 persona/智能体后卡片标题变「使用用户」且值 = 该筛选下发过消息的人数。
      （口径见 spec 口径表；`test_analytics_usage_routes` + `test_analytics_cross_consistency`）
- [x] 「用户消息数」等于 `user:message` 条数；单测断言 `message:chunk`、`tool:call`、
      `token:usage` 不被计入。（`test_analytics_usage_query`）
- [x] 前端只传纯日期，后端全链路 UTC+8；单测断言 `start=2026-08-22&end=2026-08-28`
      展开为 `[2026-08-22T00:00+08:00, 2026-08-29T00:00+08:00)`。
      （`test_analytics_date_range` + `test_analytics_date_params`）
- [x] 六张 KPI 卡、三个环图中心数字、明细表合计、导出 CSV 在同一筛选下互相对得上。
      （后端五条等式：`test_analytics_cross_consistency`，含真实 MongoDB 集成；
      前端同源：`analyticsConsistency.test.ts` + `analyticsKpiDerivation.test.ts`）
- [x] 洞察栏四条数字均可由下方图表/明细验算得出，无 LLM 生成内容。
      （`test_analytics_insights_route`；洞察全部由聚合管线算出）
- [x] `uv run ruff check .`、`uv run mypy src/`、
      `uv run pytest tests/api tests/infra -q` 全绿。
      （改动文件零违规；残留失败均为既有问题，基线对照证明，
      见子4 `research/verification-log.md`）
- [x] `cd frontend; pnpm run lint`、`pnpm run build`、新增前端测试全绿。
      （新增/相关前端测试 31/31 通过；存量 44 条失败为既有环境性问题，
      stash 基线数字完全一致）
- [x] R5 删除清单执行完毕，且删除前有"无引用"证据。
      （子4 `research/removal-evidence.md` E1–E6）
- [x] 交付一份浏览器点击验证清单（点哪里、预期看到什么、哪两个数字必须相等）给用户。
      （本任务目录 `manual-verification.md`）
