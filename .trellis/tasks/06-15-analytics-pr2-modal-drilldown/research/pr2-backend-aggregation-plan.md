# Research: PR2 后端聚合查询的真实字段方案

- **Query**: PR2 要实现 6 个端点（presets/{id}、feedback/summary、feedback/by-preset、sessions/list、feedback/list、runs/list）+ PersonaPresetCard 分析按钮。确认每个端点用哪些集合/字段，给出 aggregation pipeline 关键阶段草案。
- **Scope**: internal
- **Date**: 2026-06-18

## 结论（给 implement 的一句话）

PR2 的 6 个端点建立在 4 个集合上：`traces`（token/消息/运行明细，E1 后带 `metadata.persona_preset_id`）、`sessions`（会话明细，`metadata.persona_preset_id` 已存在，chat.py:243 写入）、`feedback`（反馈明细，E2 后带 `reason`）、`persona_presets`（角色名称 $lookup）。**所有"按角色"查询的核心关联字段是 `metadata.persona_preset_id`（traces 和 sessions 各一份，feedback 需 $lookup sessions 拿 preset_id）**。分页统一用 skip/limit（现有 session list 已用此模式，session.py:129-188）。`PersonaPresetCard` 分析按钮的条件是 `preset.scope === "global" && hasPermission(CHANNEL_MANAGE)`——**当前 card 只收 `canWrite/canAdmin` props，需新增 `canAnalyze` prop（或直接在 card 内用 `useAuth().hasPermission(Permission.CHANNEL_MANAGE)`），由 `PersonaPlazaPanel` 传入**。

---

## Findings

### 集合与字段清单（已核实）

| 集合 | 关键字段 | 来源 |
|---|---|---|
| `traces` | `trace_id, session_id, run_id, agent_id, user_id, events[].event_type/data, event_count, started_at, completed_at, updated_at, status, metadata.agent_name, metadata.user_id, metadata.username, metadata.persona_preset_id(E1后)` | `trace_storage.py:217-229` doc 结构；`MONGODB_TRACES_COLLECTION="traces"`（config/base.py:137） |
| `sessions` | `session_id, name, user_id, agent_id, created_at, updated_at, is_active, task_status, unread_count, metadata.{current_run_id, agent_id, persona_preset_id, persona_preset_name, persona_snapshot, project_id, team_id, language, ...}` | `session.py:32-49` Session 模型；`session/storage.py:162-177` create；`chat.py:242-243` 写 `metadata.persona_preset_id`；`MONGODB_SESSIONS_COLLECTION="sessions"`（config/base.py:136） |
| `feedback` | `_id, user_id, username, session_id, run_id, rating, comment, reason(E2后), created_at` | `feedback/storage.py:89-97` create dict；集合名 `"feedback"`（storage.py:46） |
| `persona_presets` | `_id(ObjectId), scope, owner_user_id, name, description, avatar, tags, status, usage_count, ...` | `persona_preset/storage.py:64` 集合名；`persona_preset.py:129-156` 模型；**无 agent_id 字段**（PR1 调研确认） |
| `users` | `_id, username, updated_at, ...` | analytics/storage.py:69；用于活跃用户 + 显示名 |

**关键事实**：
- `traces.metadata.persona_preset_id` **仅 E1 落地后存在**，历史 trace 无此字段（$match 自然过滤）。
- `sessions.metadata.persona_preset_id` **已存在**（chat.py:242-243 `if request.persona_preset_id: conversation_config["persona_preset_id"] = ...`，经 `update_session_metadata` 落库）。**这是当前唯一可靠的"会话→角色"关联**，E1 之前 traces 没有。
- token 用量在 `traces.events` 数组里，event_type=`token:usage`，`data.total_tokens`（analytics/storage.py:145-155 已用此路径）。
- `feedback` 关联到角色**没有直接字段**，必须 `$lookup sessions`（on `session_id`）取 `metadata.persona_preset_id`。

### 端点 1: `GET /api/analytics/presets/{preset_id}` — 单角色完整指标

**响应建议字段**：`total_messages, total_sessions, active_users, total_tokens, up_vote_rate, down_reasons: [{reason, count}]`

- **total_tokens**（E1 后）：`traces` 聚合，`$match {"metadata.persona_preset_id": preset_id, "events.event_type": "token:usage", "started_at": {$gte, $lte}}` → `$unwind "$events"` → `$match events.event_type` → `$group _id: null, value: {$sum $ifNull events.data.total_tokens}`。**字段路径 `metadata.persona_preset_id`（顶层 metadata 下的 key）**。参考现有 `get_tokens_by_model`（analytics/storage.py:345-390）的 unwind 模式，把 `$match` 的 `events.event_type` 前置到外层（利用 `events_event_type_ts_idx` 索引，analytics/storage.py:89-93）。
- **total_sessions**：`sessions` 聚合，`$match {"metadata.persona_preset_id": preset_id, "created_at": {$gte, $lte}}` → `$count`。**注意 $match 用 `metadata.persona_preset_id`（嵌套字段路径）**。时间筛选用 `created_at`（session 创建时间）。
- **total_messages**：`traces` 聚合，`$match {"metadata.persona_preset_id": preset_id, "started_at": {$gte, $lte}, "event_count": {$exists, $gt: 0}}` → `$group _id: null, value: {$sum "$event_count"}`。参考 `get_sessions_trend` 的 messages_pipeline（analytics/storage.py:306-321）。**event_count 是 trace 的顶层字段（trace_storage.py:224），不是 events 数组长度**——它等于该 trace 的事件总数（含 user:message/assistant chunks/tool 等）。若要"只数 user 消息"需 unwind + match `user:message`，但 PRD "总消息数"用 event_count 求和即可（与全局看板 messages 趋势口径一致，analytics/storage.py:316 `$sum "$event_count"`）。
- **active_users**：上述 sessions（或 traces）的 `user_id` 去重。用 sessions：`$match {"metadata.persona_preset_id": preset_id, "created_at": {$gte, $lte}, "user_id": {$ne: null}}` → `$group _id: "$user_id"` → `$count`。或用 traces 的 `user_id`（traces 顶层有 user_id，trace_storage.py:222）。**建议用 sessions**（口径与 total_sessions 一致）。
- **up_vote_rate + down_reasons**：`feedback` $lookup sessions 取 preset_id。pipeline：
  - `$match {"created_at": {$gte, $lte}}`
  - `$lookup { from: "sessions", localField: "session_id", foreignField: "session_id", as: "session" }`（**注意 sessions 用 `session_id` 字段做关联，不是 `_id`**——session/storage.py:181 有 `session_id` 索引；custom session_id 存在 `session_id` 字段，`_id` 是 ObjectId）
  - `$unwind "$session"`（或 `$arrayElemAt`）
  - `$match {"session.metadata.persona_preset_id": preset_id}`
  - `$group _id: null, total: {$sum 1}, up: {$sum {$cond rating=="up"}}, down_reasons: {$push "$reason"}}`
  - up_vote_rate = up/total*100；down_reasons 在 Python 侧 reduce 成 `[{reason, count}]`（只统计非 None 的 reason，None 不计入分布）。
  - **性能注意**：feedback → sessions $lookup 全量再过滤可能慢。优化：先在 sessions 侧找出该 preset 的 session_id 集合，再用 `$match {"session_id": {$in: session_ids}}` 过滤 feedback（两步查询，避免 $lookup 大表）。**建议 implement 用两步法**：Step1 `sessions.distinct("session_id", {"metadata.persona_preset_id": preset_id, ...})`，Step2 feedback $match session_id $in。

### 端点 2: `GET /api/analytics/feedback/summary` — 全局反馈汇总

- 直接 `feedback` 集合聚合，无 $lookup。
- pipeline：`$match {"created_at": {$gte, $lte}}` → `$group _id: null, total: {$sum 1}, up: {$sum cond rating=="up"}, down: {$sum cond rating=="down"}, reasons: {$push "$reason"}}`。
- 响应：`total, up_count, down_count, up_percentage, reason_distribution: [{reason, count}]`（reason_distribution 只含非 None）。
- 参考现有 `feedback/storage.py:get_stats`（storage.py:262-312）的 $group 模式，扩展加 reason 收集。

### 端点 3: `GET /api/analytics/feedback/by-preset` — 按角色分反馈

- **两步法**（避免 feedback→sessions 大 $lookup）：
  - Step1：`sessions.aggregate([{$match: {created_at: {$gte,$lte}, "metadata.persona_preset_id": {$exists, $ne: null}}}, {$group: {_id: "$metadata.persona_preset_id", session_ids: {$addToSet: "$session_id"}}}])` → 得到 `{preset_id: [session_ids]}`。
  - Step2：对每个 preset_id，`feedback.aggregate([{$match: {created_at: {$gte,$lte}, session_id: {$in: session_ids}}}, {$group: {_id: "$rating", count: {$sum 1}}}, {$project: ...}])`，或一次 `$group _id: {preset_id, rating}` 全量。
- **$lookup persona_presets 取名称**（显示用）：`$lookup { from: "persona_presets", let: {pid: "$preset_id"}, pipeline: [{$match: {$expr: {$eq: [{$toString: "$_id"}, "$$pid"]}}}, {$project: {name: 1}}], as: "preset" }`。**persona_presets._id 是 ObjectId，preset_id 是字符串，必须 `{$toString: "$_id"}` 比较**（PR1 调研确认 persona_presets 无 agent_id，唯一关联是 _id.toString）。
- 响应：`items: [{preset_id, preset_name, up_count, down_count, total, up_percentage}]`，按 total 降序。
- **单步 $lookup 写法（若不用两步法）**：feedback $lookup sessions（on session_id）→ $group _id: `session.metadata.persona_preset_id` → 再 $lookup persona_presets 取 name。但 feedback→sessions $lookup 全量较重，**推荐两步法**。

### 端点 4: `GET /api/analytics/sessions/list` — 会话明细列表

- query：`start, end, preset_id?(optional), skip=0, limit=20`。
- `sessions` 集合，`$match {created_at: {$gte,$lte}}`，可选加 `"metadata.persona_preset_id": preset_id`。
- **字段**（Session 模型，session.py:32-49）：`id(session_id), name, user_id, agent_id, created_at, updated_at, task_status, unread_count, metadata.persona_preset_id, metadata.persona_preset_name`。
- **分页**：skip/limit（现有 session list 用此，session.py:130-131）。`.sort("created_at", -1).skip(skip).limit(limit)`。total 用 `count_documents`。
- **无 message_count 字段**：Session 模型无 message_count。若列表要显示消息数，需 $lookup traces 按 session_id 求 event_count 和（`traces.aggregate([{$match: {session_id}}, {$group: _id: null, msg: {$sum event_count}}])`），或在 list 查询里用 `$lookup traces` 聚合。**建议 PR2 列表先不显示 message_count**（PRD 列表字段未明确要求），若要显示用 $lookup。
- **用户名显示**：列表若要显示用户名，$lookup users（on user_id → _id.toString）或 Python 侧批量查。**建议列表显示 user_id 即可**（管理员视角），user_id→username 的 $lookup users 用 `{$toString: "$_id"}` == `user_id`。
- 响应：`{items: Session[], total, skip, limit, has_more}`（沿用 session.py:182-188 格式）。

### 端点 5: `GET /api/analytics/feedback/list` — 反馈明细列表

- query：`start, end, preset_id?(optional), rating?(optional), skip=0, limit=20`。
- `feedback` 集合 `$match {created_at: {$gte,$lte}}`，可选 rating 过滤。
- **preset_id 过滤**：两步法（先查 sessions 的 session_ids for preset，再 $match session_id $in）。
- **字段**：`id, user_id, username, session_id, run_id, rating, comment, reason, created_at`（Feedback 模型，feedback.py:31-41）。
- **显示名**：username 已在 feedback 文档里（feedback/storage.py:92 `"username": username`），**无需 $lookup users**。
- **session 名/preset 名（可选）**：若列表要显示所属会话名或角色名，$lookup sessions（取 name + metadata.persona_preset_name）。建议显示 `session_id` + `persona_preset_name`（session metadata 已存 persona_preset_name，chat.py:245）。
- 分页 skip/limit，`.sort("created_at", -1)`。
- 响应：`{items: Feedback[], total, skip, limit, has_more}`。

### 端点 6: `GET /api/analytics/runs/list` — 运行明细列表（含 token 用量）

- query：`start, end, preset_id?(optional), skip=0, limit=20`。
- `traces` 集合，每条 trace = 一个 run。
- **$match**：`{started_at: {$gte,$lte}}`，可选 `"metadata.persona_preset_id": preset_id`（E1 后）。
- **token 汇总**：每条 trace 的 token = unwind events match token:usage sum total_tokens。**不能直接用 trace 顶层字段**（trace 无顶层 token 字段，token 在 events 数组）。pipeline：
  - `$match {started_at: {$gte,$lte}, ...}`
  - `$lookup` 自连接或 `$unwind events` + `$group` 求每 trace token。**推荐用 `$project` + `$reduce` 在 events 数组上求和**（避免 unwind 爆炸）：
    ```python
    {"$project": {
        "run_id": 1, "session_id": 1, "agent_id": 1,
        "started_at": 1, "completed_at": 1, "status": 1, "event_count": 1,
        "user_id": 1, "metadata.persona_preset_id": 1,
        "total_tokens": {
            "$sum": {
                "$map": {
                    "input": {"$filter": {"input": "$events", "as": "e", "cond": {"$eq": ["$$e.event_type", "token:usage"]}}},
                    "as": "te", "in": {"$ifNull": ["$$te.data.total_tokens", 0]}
                }
            }
        }
    }}
    ```
  - 或更简单：`$unwind events` → `$match events.event_type=="token:usage"` → `$group _id: {trace_id, run_id, ...}, total_tokens: {$sum events.data.total_tokens}, ...`（一次 $group 同时取 trace 元字段和 token 和）。**此法在 event_count 大时 unwind 量大，但 traces 已有 `events_event_type_ts_idx` 索引（analytics/storage.py:89）辅助**。
- **字段**：`run_id, session_id, agent_id, started_at, completed_at, status, event_count, total_tokens, user_id, persona_preset_id`。
- 分页：**$group 后分页复杂**（需先 $sort 再 $skip/$limit，且 $group 后字段已是聚合结果）。建议 pipeline 顺序：`$match` → `$sort started_at -1` → `$skip` → `$limit` → 然后对这页 traces 再求 token（用 `$project + $reduce` 或 Python 侧逐条 `get_trace_events`）。**最稳妥**：先 `$match + $sort + $skip + $limit` 取当前页 trace 摘要（不含 events），total 用 count_documents，然后对页内每条 trace 用 `trace_storage.get_trace_events(trace_id, event_types=["token:usage"])`（trace_storage.py:422-478）求 token 和。**这样分页简单、token 查询用现成方法**。
- 响应：`{items: [{run_id, session_id, agent_id, started_at, completed_at, status, event_count, total_tokens, user_id, persona_preset_id}], total, skip, limit, has_more}`。

### 7. PersonaPresetCard 分析按钮

**文件**：`frontend/src/components/persona/PersonaPresetCard.tsx`

- **当前操作按钮**（PersonaPresetCard.tsx:221-267）：在卡片底部 actions 区，有 Use/Clear、Copy、Edit、Delete 四个按钮，通过 `capabilities`（PersonaPresetCard.tsx:49-52 `getPersonaPresetCapabilities(preset, {canWrite, canAdmin})`）控制可见性。
- **global preset 判定字段**：`preset.scope === "global"`（PersonaPresetCard.tsx:141、145 已用此判定显示"官方"/"已发布"标签）。`PersonaPresetScope = "global" | "user"`（personaPreset.ts:1）。
- **channel:manage 权限**：`Permission.CHANNEL_MANAGE = "channel:manage"`（auth.ts:49）。**当前 card 不持有此权限判断**——card 只收 `canWrite/canAdmin` props（PersonaPresetCard.tsx:17-18），来自 `usePersonaPlaza` 的 `hasPermission(PERSONA_PRESET_ADMIN)`（usePersonaPlaza.ts:90）。
- **分析按钮条件**：`preset.scope === "global" && hasPermission(Permission.CHANNEL_MANAGE)`（PRD prd.md:23 "global preset + channel:manage"）。
- **分析按钮加在哪**：
  - **方案 A（推荐）**：在 card 内直接调 `useAuth().hasPermission(Permission.CHANNEL_MANAGE)`（card 目前没调 useAuth，需 import）。在 actions 区（PersonaPresetCard.tsx:221 `<div className="flex items-center gap-1.5">`）或卡片顶部 banner 加一个 "分析" 按钮（BarChart 图标）。点击调 `onAnalyze?.(preset)`，由 `PersonaPlazaPanel` 处理弹 `PresetAnalyticsModal`。
  - **方案 B**：card 加 `canAnalyze: boolean` prop，由 `PersonaPlazaPanel` 传 `canAnalyze={hasPermission(Permission.CHANNEL_MANAGE)}`（usePersonaPlaza.ts:90 旁加 `const canAnalyze = hasPermission(Permission.CHANNEL_MANAGE)`，return 里加 canAnalyze，PersonaPlazaPanel.tsx:259-272 传给 card）。card 内条件 `preset.scope === "global" && canAnalyze`。
  - **推荐方案 B**（与现有 canWrite/canAdmin 模式一致，card 保持纯展示，权限逻辑在 hook 层）。需改：usePersonaPlaza.ts（加 canAnalyze）、PersonaPlazaPanel.tsx（传 prop）、PersonaPresetCard.tsx（加 prop + 按钮渲染 + onAnalyze callback prop）。
- **PresetAnalyticsModal**（PR2 新建组件）：接收 `preset` + `start/end`，调 `analyticsApi.getPresetAnalytics(preset.id, start, end)`（新 API 方法），渲染 4 个概览卡 + 点踩原因分布柱状图。

---

## 给 implement 的每个端点聚合 pipeline 关键阶段草案

### 端点 1: `GET /presets/{preset_id}` — `AnalyticsStorage.get_preset_metrics(preset_id, start, end)`

并发 4 个 pipeline（用 `_fan_out`，analytics/storage.py:478-495）：
1. **total_tokens**（traces）：
   - `$match: {"metadata.persona_preset_id": preset_id, "events.event_type": "token:usage", "started_at": {$gte, $lte}}`
   - `$unwind: "$events"` → `$match: {"events.event_type": "token:usage"}`
   - `$group: {_id: null, value: {$sum: {$ifNull: ["$events.data.total_tokens", 0]}}}`
2. **total_sessions + active_users**（sessions，一次 $group 出两值）：
   - `$match: {"metadata.persona_preset_id": preset_id, "created_at": {$gte, $lte}}`
   - `$group: {_id: null, total_sessions: {$sum: 1}, active_user_ids: {$addToSet: "$user_id"}}`
   - Python 侧 `active_users = len(active_user_ids)`
3. **total_messages**（traces）：
   - `$match: {"metadata.persona_preset_id": preset_id, "started_at": {$gte, $lte}, "event_count": {$exists: true, $gt: 0}}`
   - `$group: {_id: null, value: {$sum: "$event_count"}}`
4. **feedback（up_rate + down_reasons）**（两步法）：
   - Step1: `sessions.distinct("session_id", {"metadata.persona_preset_id": preset_id, "created_at": {$gte, $lte}})` → `session_ids`
   - Step2: `feedback.aggregate([{$match: {created_at: {$gte,$lte}, session_id: {$in: session_ids}}}, {$group: {_id: null, total: {$sum:1}, up: {$sum: {$cond: [{$eq:["$rating","up"]},1,0]}}, reasons: {$push: "$reason"}}}])`
   - Python 侧算 up_rate + reduce reasons 成 `[{reason, count}]`（过滤 None）

**响应 schema（新建，analytics.py 加）**：`PresetAnalyticsResponse { total_messages, total_sessions, active_users, total_tokens, up_vote_rate, down_reasons: list[ByLabelItem] }`（复用 ByLabelItem，label=reason enum key）。

### 端点 2: `GET /feedback/summary` — `AnalyticsStorage.get_feedback_summary(start, end)`

- `feedback.aggregate([{$match: {created_at: {$gte,$lte}}}, {$group: {_id: null, total: {$sum:1}, up: {$sum cond rating=="up"}, down: {$sum cond rating=="down"}, reasons: {$push "$reason"}}}])`
- 响应：`FeedbackSummaryResponse { total, up_count, down_count, up_percentage, reason_distribution: list[ByLabelItem] }`。

### 端点 3: `GET /feedback/by-preset` — `AnalyticsStorage.get_feedback_by_preset(start, end)`

两步法：
- Step1: `sessions.aggregate([{$match: {created_at: {$gte,$lte}, "metadata.persona_preset_id": {$exists:true, $ne: null}}}, {$group: {_id: "$metadata.persona_preset_id", session_ids: {$addToSet: "$session_id"}}}])` → `{preset_id: [session_ids]}`
- Step2: 对全部 session_ids 分组（或一次 $group）：`feedback.aggregate([{$match: {created_at: {$gte,$lte}, session_id: {$in: all_session_ids}}}, {$lookup: sessions...}]` —— 更简单：把 step1 的 preset_id→session_ids 映射在 Python 侧建 dict，feedback $match session_id 后 Python 侧按映射分组。
- Step3: $lookup persona_presets 取 name（每个 preset_id → name）：`persona_presets.aggregate([{$match: {_id: {$in: [ObjectId(pid) for pid in preset_ids]}}}, {$project: {_id: {$toString}, name: 1}}])`（Python 侧转 ObjectId 查询，比 $expr $toString 快）。
- 响应：`ByPresetFeedbackResponse { items: list[{preset_id, preset_name, up_count, down_count, total, up_percentage}] }`。

### 端点 4: `GET /sessions/list` — `AnalyticsStorage.list_sessions(start, end, preset_id?, skip, limit)`

- `sessions.find({created_at: {$gte,$lte}, ("metadata.persona_preset_id": preset_id if preset_id)}, projection).sort(created_at, -1).skip(skip).limit(limit)`
- `total = sessions.count_documents(match)`
- 响应：`{items: list[Session], total, skip, limit, has_more}`（复用 Session 模型）。

### 端点 5: `GET /feedback/list` — `AnalyticsStorage.list_feedback(start, end, preset_id?, rating?, skip, limit)`

- preset_id 过滤：两步法取 session_ids（同端点 1/3）。
- `feedback.find({created_at: {$gte,$lte}, (session_id: {$in: session_ids} if preset_id), (rating if rating)}).sort(created_at, -1).skip(skip).limit(limit)`
- `total = feedback.count_documents(match)`
- 响应：`{items: list[Feedback], total, skip, limit, has_more}`。

### 端点 6: `GET /runs/list` — `AnalyticsStorage.list_runs(start, end, preset_id?, skip, limit)`

- Step1: `traces.find({started_at: {$gte,$lte}, ("metadata.persona_preset_id": preset_id if preset_id)}, {events: 0}).sort(started_at, -1).skip(skip).limit(limit)` → 当前页 trace 摘要（不含 events 大数组，参考 trace_storage.py:601-612 list_traces 的 projection）
- Step2: 对页内每条 trace，`trace_storage.get_trace_events(trace_id, event_types=["token:usage"])`（trace_storage.py:422）求 total_tokens 和。或用 `$project + $reduce` 在一次查询内算（见上文端点 6 的 $project 草案，但分页后用 $project 更高效）。
- `total = traces.count_documents(match)`
- 响应：`{items: list[{run_id, session_id, agent_id, started_at, completed_at, status, event_count, total_tokens, user_id, persona_preset_id}], total, skip, limit, has_more}`。

### 路由层（`src/api/routes/analytics.py`）

- 6 个新端点都 `Depends(require_permissions("settings:manage"))`（PRD D3 继承父任务 D8，analytics.py:56 现有端点已用此权限）。
- start/end 用现有 `_parse_iso_datetime`（analytics.py:37-49）解析。
- preset_id 是路径参数（端点 1）或 query 参数（端点 3/4/5/6 可选）。
- 分页参数 `skip: int = Query(0, ge=0)`, `limit: int = Query(20, ge=1, le=100)`（参考 session.py:130-131）。

### 前端 API 服务（`frontend/src/services/api/analytics.ts`）

加方法：
- `getPresetAnalytics(presetId, start, end)` → `GET /presets/${presetId}${rangeQuery}`
- `getFeedbackSummary(start, end)` → `GET /feedback/summary${rangeQuery}`
- `getFeedbackByPreset(start, end)` → `GET /feedback/by-preset${rangeQuery}`
- `listSessions(start, end, presetId?, skip, limit)` → `GET /sessions/list?...`
- `listFeedback(start, end, presetId?, rating?, skip, limit)` → `GET /feedback/list?...`
- `listRuns(start, end, presetId?, skip, limit)` → `GET /runs/list?...`

### 前端类型（`frontend/src/types/analytics.ts`）

加：`PresetAnalyticsResponse`, `FeedbackSummaryResponse`, `ByPresetFeedbackResponse`, `SessionListItem`(复用 Session), `FeedbackListItem`(复用 Feedback), `RunListItem`, 及对应的 `ListResponse<T>` 分页包装。

---

## Caveats / Not Found

- **E1 是端点 1/3/4/5/6 按 preset 过滤 traces 的前提**：`traces.metadata.persona_preset_id` 仅 E1 落地后存在。PR2 implement 必须先做 E1，否则按 preset 过滤 traces 的端点（1 的 token/messages、6 的 runs by preset）只能返回空/历史数据不计。**E1 和后端端点应同一 PR 交付**。
- **sessions.metadata.persona_preset_id 已存在**（chat.py:242-243），端点 1 的 total_sessions/active_users、端点 3/4/5 的 preset 过滤**不依赖 E1**，E1 落地前也可工作（基于 sessions）。**建议端点 1 的 token/messages 用 traces.metadata.persona_preset_id（E1 后），total_sessions/active_users 用 sessions.metadata.persona_preset_id（已存在）**——两者字段名相同但来源不同，implement 注意区分查询的集合。
- **$lookup sessions 用 session_id 不是 _id**：custom session_id 存在 `session_id` 字段（session/storage.py:181），feedback.session_id 与 sessions.session_id 关联。**不要用 _id 关联**（_id 是 ObjectId，feedback.session_id 是字符串 session_id）。
- **persona_presets._id 是 ObjectId**：$lookup 或查询时 preset_id（字符串）需转 ObjectId 或用 `{$toString: "$_id"}`。Python 侧 `ObjectId(preset_id)` 更高效。
- **未验证** unwind events 在大 trace 下的性能。traces 已有 `events_event_type_ts_idx`（analytics/storage.py:89）索引，且 `$match` 前置 `events.event_type` 走索引扫描，性能可控。runs/list 用 Step1 find（不 unwind）+ Step2 逐条 token 查询，避免单次大 unwind。
- **分页**：统一 skip/limit（与现有 session.py 一致）。未用 cursor 分页（数据量级看板场景 skip/limit 足够）。
- **runs/list 的 token**：trace 无顶层 token 字段，必须从 events 数组求。implement 可选 Step1+Step2（推荐，分页简单）或单次 $project+$reduce（一次查询，但分页要在 $project 前用 $sort+$skip+$limit，$project 在分页后）。
- **i18n**：前端新端点对应的 i18n key（PresetAnalyticsModal 标题、列表表头、分页文案、reason 分布柱状图标签复用 `feedback.reason.*`）需五语言补全，详见 E2 文档的 i18n 命名建议。
