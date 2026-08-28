# Design：统计看板重构

本文件是父任务的技术设计，四个子任务共用。子任务的 `implement.md` 只写执行顺序与验证命令，
不重复设计。

## 1. 根因与设计约束

### 1.1 历史数字为什么会变

统计层对**现存文档**实时聚合，而会话与 trace 会被硬删：

```
用户点删除会话
  → SessionManager.delete_session          src/infra/session/manager.py:172
      → clear_session_messages             src/infra/session/manager.py:164
          → trace_storage.delete_session_traces   trace_storage.py:1704  (delete_many)
      → session_storage.delete             storage.py:342               (delete_one)
```

于是昨天真实发生过的会话/消息/token，今天全部从聚合结果里消失。
「清空消息」只走 `clear_session_messages`，会话还在但 traces 没了，
表现为会话数不变、消息数与 token 变少。

已排除的其它可能：`sessions` / `traces` 上没有 TTL 索引，没有定时清理或保留期任务，
analytics 响应没有缓存层（`research/session-count-shrink-root-cause.md`、
`research/backend-analytics-current-state.md`）。

**设计约束 A**：不改删除行为（用户删了就该真删，隐私上也合理）。
历史数字的稳定性必须由**独立的、只存计数的快照**保证。

### 1.2 时区错开

| 层 | 行为 | 位置 |
|----|------|------|
| 前端算日边界 | `setHours(0,0,0,0)` / `setHours(23,59,59,999)` 用浏览器本地时区 | `AnalyticsPanel.tsx:98-107` |
| 前端传参 | `toISOString()` 转 UTC 瞬时 | `AnalyticsPanel.tsx:125` |
| 后端过滤 | `$gte`/`$lte` 按 UTC 瞬时比较 | `storage.py:206-207` |
| 后端分桶 | `$dateToString` 用 `Asia/Shanghai` | `storage.py:210-217` |

浏览器不在 UTC+8 时，"日"的定义在前端与后端不一致，边界记录归错桶。

**设计约束 B**：日界只允许在一处定义。前端传纯日期字符串，后端负责展开成 UTC+8 的
半开区间，分桶也用 UTC+8。

### 1.3 登录活跃无法回溯

登录只写 `users.updated_at`（`src/infra/user/storage.py:714` `touch_updated_at`，
被 `oauth.py:264`、`oa_login.py:68`、`user/manager.py:94` 三条登录路径调用）。
这是"只存最后一次"的字段：张三今天再登录一次，他就从"昨天活跃"里消失了。

**设计约束 C**：新增日粒度记录，纯新增，不动 `users.updated_at`、不动任何现存数据。
三条登录路径共用 `touch_updated_at` 这一个写入点，所以只需在该方法内追加一次写入。

## 2. 数据模型（两个新集合）

### 2.1 `user_daily_activity` — 谁在哪天活跃

```
{
  _id,
  user_id: str,            # str(users._id)
  date: "2026-08-25",      # UTC+8 日期
  sources: ["login", "message"],   # 集合语义，$addToSet
  first_at: datetime,      # UTC
  last_at: datetime,       # UTC
}
唯一索引: (user_id, date)
查询索引: (date, user_id)
```

写入点：

| 事件 | 位置 | source |
|------|------|--------|
| 登录成功 | `UserStorage.touch_updated_at`（三条登录路径的共用出口） | `login` |
| 用户发消息 | trace 写入 `user:message` 的路径 | `message` |

`message` 来源可由回填从 traces 推导，因此实时写入 `message` 是可选优化；
`login` 无法从任何现存数据推导，必须实时写入。

指标推导：

| 指标 | 推导 |
|------|------|
| 活跃用户数（未筛选） | 区间内 `distinct(user_id)` |
| 使用用户数 | 区间内 `distinct(user_id) where "message" in sources` |
| 本期新增使用者 | `min(date) where "message" in sources` 落在本期的用户数 |

### 2.2 `analytics_daily_snapshot` — 冻结的日计数

粒度：**日期 × 用户 × persona × 智能体**。只存计数，不存会话标题、消息文本等任何内容。

```
{
  _id,
  date: "2026-08-25",          # UTC+8
  user_id: str,
  persona_preset_id: str | None,
  agent_id: str,
  new_sessions: int,
  active_sessions: int,
  user_messages: int,
  tokens: int,
  last_active_at: datetime,     # UTC，该格内最后一条用户消息时间
  frozen_at: datetime,
}
唯一索引: (date, user_id, persona_preset_id, agent_id)
查询索引: (date, persona_preset_id) / (date, agent_id)
```

为什么选这个粒度：向上汇总即可得到全局总量、按 persona、按智能体、按人、以及任意筛选组合，
且 `distinct(user_id)` 能正确去重。规模约 `日活人数 × 人均 persona 数` 条/天。

**不存**在快照里的两项及其处理：

| 指标 | 为什么不进快照 | 处理 |
|------|----------------|------|
| 活跃高峰（星期×小时） | 需要小时粒度，会让快照膨胀 24 倍 | 实时聚合。用户接受该项不冻结 |
| 反馈数据 | 反馈表不参与删除链路，本身不会缩水 | 实时聚合 |

## 3. 查询层

### 3.1 保留并扩展 `usage_query.py`

`src/infra/analytics/usage_query.py` 的口径已由单测锁住，保留。`UsageFilters` 增加不了新语义，
只在其外层加"快照优先"的读取策略。

新增 `src/infra/analytics/snapshot.py`：

```python
def snapshot_match(filters: UsageFilters, dates: list[str]) -> dict: ...
def snapshot_group_stages(dimension: Literal["total","day","persona","agent","user"]) -> list[dict]: ...
async def read_or_freeze(filters: UsageFilters) -> UsageFactsResult: ...
```

`read_or_freeze` 的策略：

```
today   = 当前 UTC+8 日期
dates   = 区间内的每一天
missing = [d for d in dates if d != today and 快照不存在(d)]

if missing:
    对 missing 逐日实时聚合 → 批量 upsert 快照（拿 Redis 锁，幂等）
读取:
    历史天 → 从 analytics_daily_snapshot 聚合
    今天   → 从 traces 实时聚合（usage_facts_stages）
    合并两段结果
```

关键不变量：

1. 同一历史日期，任意两次查询返回相同数字（快照只写一次，`upsert` 用
   `$setOnInsert` 语义避免被后续覆盖）。
2. 今天的数字随时变化，这是预期行为。
3. 快照缺失时读取路径自动降级为实时聚合，不返回空。

### 3.2 日期参数

新增 `src/infra/analytics/date_range.py`：

```python
CST = timezone(timedelta(hours=8))

def resolve_range(start: str, end: str) -> tuple[datetime, datetime]:
    """'2026-08-22','2026-08-28' → [08-22T00:00+08:00, 08-29T00:00+08:00)"""

def previous_range(start: str, end: str) -> tuple[str, str]:
    """等长前推：('2026-08-22','2026-08-28') → ('2026-08-15','2026-08-21')"""

def day_buckets(start: str, end: str) -> list[str]: ...
```

- 所有 analytics 端点的 `start`/`end` 改为 `YYYY-MM-DD` 字符串，正则校验，非法 → 400。
- `$match` 改为半开区间 `{"$gte": start_dt, "$lt": end_dt}`，消除 `$lte` 的毫秒边界歧义。
- `_BUCKET_TZ` 常量与 `CST` 统一到 `date_range.py` 一处。

### 3.3 端点

保留（口径已验证）：`/usage/summary` `/usage/trend` `/usage/by-persona` `/usage/by-user`
`/usage/export.csv`。参数改纯日期，内部改走 `read_or_freeze`。

新增一个洞察端点，避免前端为四条结论发四个请求：

```http
GET /api/analytics/usage/insights?start&end&persona_preset_id&agent_id&role_id
→ {
    peak: { weekday: 5, hour: 13, user_messages: 128 },
    top_token_users: [ {user_id, username, display_name, tokens}, ... ],  # Top3
    fastest_growing_persona: { persona_preset_id, persona_preset_name,
                               current: int, previous: int, growth_pct: float },
    new_users: int,
  }
```

`summary` 响应增加对比字段（等长前推区间的同名指标），供 KPI 卡算 ±x%：

```python
class UsageSummaryResponse(BaseModel):
    active_users: int          # 登录去重人数
    using_users: int           # 发过消息的去重人数
    new_sessions: int
    active_sessions: int
    user_messages: int
    total_tokens: int
    previous: UsageSummaryPrevious | None    # 同结构，不含 previous
```

人均消息 = `user_messages / using_users`，会话均 Token = `total_tokens / active_sessions`，
两者在**前端**算，后端不额外给字段（避免除零语义分散到两层）。

### 3.4 筛选时的活跃用户口径切换

`active_users` 无 persona 归属（登录不带 persona）。契约：

| 条件 | 卡片标题 | 值 |
|------|----------|-----|
| 无 persona / agent 筛选 | 活跃用户 | `active_users`，副行"其中使用 N 人" |
| 有 persona 或 agent 筛选 | 使用用户 | `using_users`，副行"发过消息的人" |

后端两个字段都返回；筛选生效时 `active_users` 与 `using_users` 相等（因为筛选无法作用于
登录记录，此时后端把 `active_users` 设为 `using_users`，避免前端出现"筛选了但这张卡不动"）。

## 4. 回填 worker

形状照 `src/infra/session/backfill.py`（`SessionSearchBackfillWorker`）与
`src/api/main.py:492` 的挂载方式，不引入新机制。

`src/infra/analytics/backfill.py`：

```python
class AnalyticsBackfillWorker:
    """把历史每天的 user_daily_activity 与 analytics_daily_snapshot 补齐。"""
    async def run_once(self) -> int: ...
```

- Redis 分布式锁（键 `analytics:backfill:lock`），多副本只有一个跑。
- 分批：一次处理 N 天，批间 `asyncio.sleep` 让出事件循环。
- 幂等：`upsert`，重跑不产生重复。
- 进度标记：写入一个 `analytics_backfill_state` 文档记录已回填到哪天，下次启动跳过。
- 失败只 `logger.warning`，绝不阻塞启动（与现有 backfill 一致）。
- 边界：最早只能回填到 `traces` 最早一条记录的日期；早于该日期的区间返回零而非报错。
- 回填只能写 `source=message`（登录历史不存在，无法凭空造）。

同时暴露 `python -m src.infra.analytics.backfill --dry-run` 形状的 CLI 入口不做——
用户已确认走启动自动回填，不额外加手动脚本。

## 5. 前端结构

### 5.1 拆分

`AnalyticsPanel.tsx` 现在是单文件巨型组件。按图 3 的区块拆成：

```
frontend/src/components/panels/analytics/
  AnalyticsPanel.tsx          # 只负责筛选 state + 数据编排 + 区块布局
  AnalyticsFilterBar.tsx      # 日期区间 + [今天][7天][30天][自定义] + Persona/智能体下拉
  AnalyticsKpiRow.tsx         # 6 张 KPI 卡（含 sparkline + 较上一区间）
  AnalyticsTrendChart.tsx     # 核心趋势多指标对比
  AnalyticsInsightPanel.tsx   # 右侧硬数据洞察栏
  AnalyticsTopRow.tsx         # 智能体/Persona/模型Token 三个环图 + 反馈概览
  AnalyticsUsageTable.tsx     # 使用明细（搜索 + 导出 + 分页）
```

拆分标准：每个文件一个视觉区块，只接收已算好的数据与回调，不自己发请求。
请求全部在 `AnalyticsPanel` 里发，保证筛选变化时六个区块看到的是同一批参数。

### 5.2 复用与缺口

| 需要 | 现有 | 缺口 |
|------|------|------|
| 卡片容器 | `glass-card` 类 + 现有 panel 约定 | 无 |
| 折线 / 环图 / 条形 | recharts（项目已用） | 无 |
| sparkline | — | 用 recharts `<LineChart>` 去掉坐标轴与网格实现，不引新库 |
| 时间范围按钮组 | `AnalyticsPanel` 与 `PresetAnalyticsModal` 各写一份 | 抽成 `AnalyticsFilterBar` 内的一个小组件，两处共用 |
| 下拉 | 原生 select / `GlassSelect` | 无 |
| 分页 | `Pagination` | 无 |
| 加载态 | `PanelLoadingState` | 无 |
| 配色 | 项目 theme CSS 变量 | 图表系列色沿用现有 `PIE_COLORS` 语义但改为读 theme token |

参考图的配色不采用。

### 5.3 洞察栏点击行为

| 条目 | 点击 |
|------|------|
| 活跃高峰 | 无（结论句，无对应明细维度） |
| Token 大户 Top3 | 钻到使用明细并按该用户过滤 |
| 增长最快 Persona | 把顶部 persona 筛选设为该 persona |
| 本期新增使用者 | 钻到用户列表（新增使用者子集） |

### 5.4 数字一致性契约（前端必须满足）

1. 环图中心数字 == 对应 KPI 卡数字。
2. 使用明细表的合计 == 对应 KPI 卡数字（分页下用后端返回的 total，不用当前页求和）。
3. 导出 CSV 的行集 == 当前筛选下的明细全集（受 10000 行上限约束，超限时提示）。
4. 钻取抽屉的 total == 触发它的卡片数字。

## 6. 清理清单（执行前必须验证无引用）

| 位置 | 依据 |
|------|------|
| `/api/analytics/tokens/by-preset` | 名字说 preset 实际按 `agent_id` 分组；`usage/by-persona` 已提供真正按 persona 的 token |
| `/sessions/list`、`/sessions/export.csv` 的 `preset_id` 兼容分支 | 前端改为只传 `persona_preset_id` 后无调用方 |
| 被 `usage/*` 取代的旧聚合方法 | 同一指标两套 pipeline |
| 前端 `UsageByPersona` 客户端方法 | 定义了从未调用 |
| 前端重复的日期格式化工具 | `AnalyticsPanel` 与 `PresetAnalyticsModal` 各写一份 |
| 孤儿 i18n key | 无引用 |
| `users.updated_at` 索引（`storage.py:159`） | 活跃用户不再读该字段后，该索引仅为 analytics 而建，可删 |

验证方法：`grep` 全仓引用 + 前端 `pnpm run build` 通过 + 后端测试全绿。
`users.updated_at` **字段本身保留**（其它功能可能依赖），只删为 analytics 建的索引。

## 7. 兼容性与风险

| 项 | 结论 |
|----|------|
| 存量数据 | 零改写、零删除。两个新集合纯新增 |
| `users.updated_at` | 保留字段与写入行为，只是 analytics 不再据此判活跃 |
| 旧接口 | 参数从 ISO 时间戳改为纯日期是**破坏性变更**，但前后端同仓同版本发布，无外部调用方 |
| 快照与实时的边界 | 今天永远实时；跨午夜时"今天"变成"历史天"，下次查询自动冻结 |
| 首次查询变慢 | 某历史天首次被查到时要算一次并写快照；启动回填 worker 会提前把这批算掉 |
| 回填边界 | 早于 traces 最早记录的区间返回零，不报错 |
| `TRACE_EVENT_WRITE_MODE` | 所有实时聚合仍读 `traces.events` 数组。切到 `event_store` 会让实时段归零（既有耦合，本次不扩大）。快照一旦冻结不受影响 —— 这实际上**降低**了该风险 |

## 8. 取舍说明

**为什么不改成软删除**：会话软删除只能救回会话数，traces 仍被删，消息数与 token 照样缩水；
且改动落在删除链路上，风险高于新增一个只读的快照集合。

**为什么快照存到用户×persona 粒度**：若只存 persona 粒度，下方"使用明细"（每人一行）
历史区间只能实时算，于是明细加起来对不上卡片数字——正是本次要消灭的问题。

**为什么活跃高峰不进快照**：小时粒度会让快照条数膨胀 24 倍，而这一条只是结论句，
用户接受它随删除行为波动。

**为什么洞察栏不用 LLM**：四条全部可由下方图表与明细验算得出。生成式文案无法验算，
且要做五语 i18n。
