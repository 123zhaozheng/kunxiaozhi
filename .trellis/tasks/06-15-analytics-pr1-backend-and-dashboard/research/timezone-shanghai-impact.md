# Research: 时区改 Asia/Shanghai 的影响面确认

- **Query**: 把 `AnalyticsStorage` 分桶 timezone 从 "UTC" 改为 "Asia/Shanghai" 的影响面：后端 $dateToString/$dayOfWeek/$hour 位置、MongoDB 版本兼容性、前端日期函数改动
- **Scope**: internal + external（MongoDB 时区支持）
- **Date**: 2026-06-18

## 结论（给 implement 的一句话）

**后端改动集中在 `src/infra/analytics/storage.py` 的 3 处**：`_day_bucket_expr`（102-109 行，timezone: "UTC"）、`get_users_heatmap` 的 `$dayOfWeek`/`$hour`（246-247 行，未显式 timezone，默认 UTC）。MongoDB 7.x（项目 Docker 用 `mongo:7`）原生支持 IANA 时区名 "Asia/Shanghai"，可直接透传，风险低。**前端 `AnalyticsPanel.tsx` 有 4 个函数用 `setUTCHours`/`setUTCDate` 构造"今天"边界，改东八区需改成 `setHours`/`setDate`（本地时区=东八区时）或手动 +8 偏移**——但要注意浏览器本地时区不一定都是东八区，最稳妥是用一个固定偏移函数。前后端时区口径必须一起改，否则边界错 8 小时。

---

## Findings

### 问题 1：storage.py 里所有用到 timezone: "UTC" / $dateToString / $dayOfWeek / $hour 的位置

全量 grep `timezone` / `$dateToString` / `$dayOfWeek` / `$hour` 在 `src/infra/analytics/storage.py`：

| 行号 | 代码 | 用途 | 改动 |
|---|---|---|---|
| 10 | `from datetime import datetime, timezone` | Python 标准库 timezone（用于 `_ensure_datetime` 给 naive datetime 加 UTC） | **不改**——这是 Python tz，与 MongoDB 分桶时区无关。`_ensure_datetime`（30-34 行）把查询边界转 UTC 用于 `$gte/$lte` 比较，**比较语义应保持 UTC**（MongoDB 存的就是 UTC datetime），改了反而错。 |
| 102-109 | `_day_bucket_expr(field)` → `{"$dateToString": {"format": "%Y-%m-%d", "date": field, "timezone": "UTC"}}` | **天分桶**：用于 `get_active_users_trend`（208 行 `$updated_at`）、`get_sessions_trend`（286 行 `$created_at`、302 行 `$started_at`） | **改 "UTC" → "Asia/Shanghai"**。这是核心改动点。改后所有按天分桶的折线图按东八区日期聚合。 |
| 246-247 | `{"$dayOfWeek": "$created_at"}` / `{"$hour": "$created_at"}` | **热力图**：`get_users_heatmap` 的星期×小时分桶。**未显式传 timezone，MongoDB 默认用 UTC** | **改：给两个操作都加 `"timezone": "Asia/Shanghai"`**，即 `{"$dayOfWeek": {"date": "$created_at", "timezone": "Asia/Shanghai"}}` 和 `{"$hour": {"date": "$created_at", "timezone": "Asia/Shanghai"}}`。注意 `$dayOfWeek`/`$hour` 的语法是 `{"$dayOfWeek": {"date": <expr>, "timezone": <str>}}`，不是位置参数。 |

**其他聚合里没有用到时区分桶的地方**：
- `get_overview`（113-194 行）：纯 `$match` + `$count`/`$sum`，无分桶，timezone 无关。但 `$match` 的 `$gte/$lte` 用的是 `_ensure_datetime` 转的 UTC 边界——**边界本身要不要改东八区见问题 3 的前端讨论**。
- `get_tokens_by_model`（332-377 行）：无分桶。
- `get_tokens_by_preset`（379-455 行）：无分桶（且见 preset-agent-linkage.md，建议降级）。
- `get_tokens_trend`（457-492 行）：用 `_day_bucket_expr("$events.timestamp")`（476 行）→ 会随 `_day_bucket_expr` 改动自动生效，**无需单独改**。

**改动汇总（后端）**：
1. `storage.py:107` `"timezone": "UTC"` → `"timezone": "Asia/Shanghai"`（一处，影响所有用 `_day_bucket_expr` 的趋势图）。
2. `storage.py:246-247` 给 `$dayOfWeek` 和 `$hour` 加 `"timezone": "Asia/Shanghai"`（两处，热力图）。
3. 可选：把时区提取成常量（如 `_BUCKET_TZ = "Asia/Shanghai"`）放在文件顶部（26 行 `_TOKEN_USAGE_EVENT` 附近），方便后续切换。不强制，但 implement 可顺手做。

### 问题 2：MongoDB $dateToString / $dayOfWeek / $hour 是否支持 "Asia/Shanghai" IANA 时区名？

**支持，且项目 MongoDB 版本满足要求。**

- MongoDB `$dateToString` / `$dayOfWeek` / `$hour` 的 `timezone` 参数自 **MongoDB 4.0 起支持 IANA 时区名**（如 "Asia/Shanghai"、"America/New_York"）。5.0+ 进一步完善。参考 MongoDB 官方文档（$dateToString、Aggregation Expression Operators）。
- **项目 MongoDB 版本**：Docker 部署用 `mongo:7`（`docs/en/deploy/docker.md:29` `| mongo | mongo:7 | 27017 | MongoDB database |`，`docs/zh/deploy/docker.md:28` 同）。MongoDB 7.x 完全支持 IANA 时区名。**风险低**。
- motor/pymongo 透传：`timezone` 是字符串值，放在 aggregation pipeline dict 里，motor/pymongo 原样透传给 MongoDB 服务端执行，客户端无需任何时区处理。现有 `"timezone": "UTC"` 就是字符串透传，改成 `"Asia/Shanghai"` 同理。
- **唯一风险**：若生产环境用的是比 4.0 更老的 MongoDB（非官方 Docker 部署，而是自建老库），则 IANA 时区名不被识别会报错。但代码仓内无版本锁定（grep `MONGODB_VERSION`/`mongo_version` 零结果），Docker 默认 `mongo:7`，可假定 5.0+。implement 时若想稳妥，可在 `ensure_indexes` 或启动日志里打一行 MongoDB server version，但非本 PR 必须。

**IANA 时区名注意点**：用 "Asia/Shanghai"（带斜杠），不是 "Shanghai" 也不是 "+08:00"。MongoDB 对 Olson/IANA 名（如 "Asia/Shanghai"）和固定偏移（如 "+08:00"）都支持，但 IANA 名能正确处理历史夏令时（中国无夏令时，二者结果一致，但 IANA 名更规范）。**推荐用 "Asia/Shanghai"**。

**关于 `$hour` 返回值**：`$hour` with timezone 返回的是该时区下的小时（0-23），热力图前端 `HeatmapGrid`（AnalyticsPanel.tsx:276-331）按 0-23 渲染，无需改前端热力图逻辑。`$dayOfWeek` 返回 1-7（周日=1），storage.py:256-257 已做 `$subtract: [weekday, 1]` 转成 0-6，前端 `WEEKDAY_LABELS = ["Sun"..."Sat"]`（AnalyticsPanel.tsx:60）按 0-6 渲染，也无需改。**热力图前端不动，只改后端 timezone 参数即可**。

### 问题 3：前端 AnalyticsPanel.tsx 的日期函数改动

`frontend/src/components/panels/AnalyticsPanel.tsx` 里用 `setUTCHours`/`setUTCDate` 的函数：

| 行号 | 函数 | 当前逻辑 | 改动需求 |
|---|---|---|---|
| 67-71 | `endOfDayUtc(d)` | `result.setUTCHours(23, 59, 59, 999)` | **改**：东八区"今天 23:59:59" |
| 73-77 | `startOfDayUtc(d)` | `result.setUTCHours(0, 0, 0, 0)` | **改**：东八区"今天 00:00:00" |
| 79-92 | `rangeForPreset(preset)` | 用 `endOfDayUtc(new Date())` 当 end；7d/30d 用 `start.setUTCDate(start.getUTCDate() - 6/29)` 再 `startOfDayUtc` | **改**：end 和 start 都按东八区算 |
| 575-576 | `applyCustomRange` | `new Date(\`${customStart}T00:00:00Z\`)` / `new Date(\`${customEnd}T23:59:59Z\`)` | **改**：`Z`（UTC）→ 东八区偏移，或 `+08:00` |
| 598-600 | `presetLabel` 里 `fmt(d)` | `d.getUTCFullYear()/getUTCMonth()/getUTCDate()` 格式化显示 | **改**：显示用东八区日期，否则 label 与实际查询边界对不上 |

**关键矛盾**：后端改了分桶时区为 Asia/Shanghai 后，"今天"的边界是东八区 00:00-23:59。但前端 `rangeForPreset` 用 `setUTCHours` 算出的是 UTC 00:00-23:59（= 东八区 08:00-次日 08:00）。**若前端不改，传给后端的 start/end 仍是 UTC 边界，后端按 UTC 边界 `$match started_at/created_at/updated_at`（这些 `$match` 用的是 `_ensure_datetime` 转的 UTC，与分桶 timezone 无关），但分桶按 Asia/Shanghai——会出现"边界是 UTC 0点-24点，但桶按东八区切"的不一致**：比如 UTC 23:30 的 trace 在 UTC 边界内，但按东八区分桶是次日 07:30，可能落到边界外的桶里被忽略，导致趋势图少算。

**所以前后端必须同步改。** 推荐前端改法：

**方案 A（推荐，最简单）**：假设用户浏览器在东八区（中国用户为主的产品，合理假设），把所有 `setUTCHours` → `setHours`、`setUTCDate` → `setDate`、`getUTCDate` → `getDate` 等。`new Date()` 在东八区浏览器里本地时间就是东八区时间，`setHours(0,0,0,0)` 得到东八区 00:00 本地时间，`toISOString()` 转成 UTC 字符串传给后端（后端 `_parse_iso_datetime` 解析后 `.astimezone(utc)`，得到正确的 UTC 瞬时值）。`applyCustomRange` 的 `${customStart}T00:00:00Z` 改成 `${customStart}T00:00:00+08:00`（或直接 `T00:00:00` 不带 Z，让浏览器按本地时区解析）。

**方案 B（更稳妥，不假设浏览器时区）**：写一个固定的东八区边界函数，手动构造 UTC 瞬时：
```ts
const CST_OFFSET_MS = 8 * 60 * 60 * 1000;
function startOfDayCST(d: Date): Date {
  // 取东八区当前日期的 00:00，转回 UTC 瞬时
  const cst = new Date(d.getTime() + CST_OFFSET_MS);
  cst.setUTCHours(0, 0, 0, 0);
  return new Date(cst.getTime() - CST_OFFSET_MS);
}
function endOfDayCST(d: Date): Date {
  const cst = new Date(d.getTime() + CST_OFFSET_MS);
  cst.setUTCHours(23, 59, 59, 999);
  return new Date(cst.getTime() - CST_OFFSET_MS);
}
```
rangeForPreset 里 `setUTCDate(start.getUTCDate() - 6)` 也要在 CST 域里做。`applyCustomRange` 用 `${customStart}T00:00:00+08:00`。`presetLabel` 的 `fmt` 用 CST 域的年月日。

**推荐方案 A**（产品是中国为主，浏览器时区=东八区是合理默认，代码更简洁）。若担心海外用户，用方案 B。**向产品确认目标用户时区后再选**。

**前端需改的函数清单（方案 A）**：
- `endOfDayUtc`（67-71）→ 重命名 `endOfDayCST`，`setUTCHours` → `setHours`
- `startOfDayUtc`（73-77）→ 重命名 `startOfDayCST`，`setUTCHours` → `setHours`
- `rangeForPreset`（79-92）→ `setUTCDate` → `setDate`、`getUTCDate` → `getDate`，调用改名后的函数
- `applyCustomRange`（575-576）→ `T00:00:00Z` → `T00:00:00+08:00`、`T23:59:59Z` → `T23:59:59+08:00`
- `presetLabel` 的 `fmt`（598-600）→ `getUTCFullYear/Month/Date` → `getFullYear/Month/Date`

**注意**：`toIso`（94-96 `d.toISOString()`）不用改——它把本地 Date 转 UTC ISO 字符串传后端，后端按 UTC 瞬时解析，正确。改的是"如何构造本地 Date 的边界"，不是"如何序列化"。

---

## 给 implement 的改动清单

**后端（src/infra/analytics/storage.py）**：
1. 第 107 行 `"timezone": "UTC"` → `"Asia/Shanghai"`。
2. 第 246-247 行 `$dayOfWeek` / `$hour` 加 `"timezone": "Asia/Shanghai"`（语法改成 `{"$dayOfWeek": {"date": "$created_at", "timezone": "Asia/Shanghai"}}`）。
3. 可选：提取常量 `_BUCKET_TZ = "Asia/Shanghai"`。
4. **不要改** `_ensure_datetime`（30-34 行）和 `_date_range_query`（97-99 行）——`$match` 的 `$gte/$lte` 比较必须保持 UTC 瞬时，改了会错。只要前端传的 start/end 是正确的 UTC 瞬时（见前端改动），后端 `$match` 自然对齐东八区"今天"。

**前端（frontend/src/components/panels/AnalyticsPanel.tsx）**：
5. `endOfDayUtc`/`startOfDayUtc`/`rangeForPreset`/`applyCustomRange`/`presetLabel.fmt` 按方案 A 改（setUTCHours→setHours 等），或按方案 B 写固定 +8 偏移函数。**必须与后端同步改**。

**验证**：
- 后端单测：构造一个 UTC 16:00（= 东八区次日 00:00）的 trace/session，改前分桶落到当天，改后落到次日。构造 UTC 15:59（= 东八区 23:59）验证仍在当天。
- 端到端：浏览器在东八区，选"1d"，看趋势图是否只包含东八区"今天"的数据；对比改前后 token 总数是否变化（边界差 8 小时，数据量会变）。
- 热力图：东八区晚上 20-22 点的 session 应落在 hour=20-22 格子（改前因 UTC 会落在 12-14 格子）。

---

## Caveats / Not Found

- **MongoDB 版本未在代码/配置中硬锁定**，结论基于 Docker `mongo:7`。若生产用自建老版 MongoDB（<5.0），IANA 时区可能不支持。implement 前建议确认生产 MongoDB 版本，或在测试环境先跑一遍聚合验证。
- **前端方案 A 假设浏览器时区=东八区**。若产品有海外用户，方案 A 会让海外用户看到错误的"今天"边界（他们的本地今天不是东八区今天）。需向产品确认用户群体。方案 B 更稳妥但代码略繁。
- **未核查 AnalyticsStorage.ensure_indexes 的调用时机**（是否在应用启动时被调用），与 login-updated-at-refresh.md 同一 caveat。若 ensure_indexes 未跑，`users.updated_at` 索引缺失，时区改动不影响索引，但活跃用户查询性能可能差——与时区调研独立。
- **analytics manager 层（src/infra/analytics/manager.py）未读**，但时区改动只在 storage.py 的 aggregation pipeline，manager 只是透传 start/end，无需改。若 implement 发现 manager 有时区相关逻辑需再核查。
