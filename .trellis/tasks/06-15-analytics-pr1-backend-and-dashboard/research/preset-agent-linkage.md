# Research: traces.agent_id 与 persona_presets 的真实关联字段

- **Query**: `get_tokens_by_preset` 用 `traces.agent_id` 分组并 `$lookup` persona_presets，但 persona_presets 没有 agent_id 字段——确认真实关联路径或判定无法关联
- **Scope**: internal
- **Date**: 2026-06-18

## 结论（给 implement 的一句话）

**traces.agent_id 无法可靠关联到 persona_presets**。`traces.agent_id` 存的是 Agent factory ID（固定字符串 `"search"` / `"fast"` / `"team"`），不是 persona_preset 的 ObjectId；persona_presets 集合也根本没有 `agent_id` 字段。现有 `$lookup` 里 `persona_presets.agent_id == $$agent_id_str` 是**死代码**，而 `_id.toString() == agent_id_str` 分支也几乎不可能命中（除非某个 preset 的 ObjectId 恰好等于 "search" 这种字符串，不可能）。`persona_preset_id` 只存在于 **sessions.metadata** 里，从不在 traces 里。

**建议方案**：把 `get_tokens_by_preset` 降级为"按 Agent 类型聚合"——直接按 `traces.agent_id` 分组展示（label 用 agent_name 或 agent_id），不再 `$lookup` persona_presets；同时改语义为"按 Agent 类型"而非"按角色预设"。若产品方坚持要按 persona preset 统计 token，则必须改写入链路：在 trace 创建时把 `persona_preset_id` 写进 traces.metadata，这是 PR1 范围外的改动，不应在本 PR 做。

---

## Findings

### 问题 1：traces.agent_id 的写入位置和时机

**写入点：唯一来源是 `TraceStorage.create_trace`，在 insert 时一次性写入，不是后续 $set。**

证据链（自下而上）：

1. `src/infra/session/trace_storage.py:191-229` — `create_trace()` 构建 `doc` dict，第 220 行 `"agent_id": agent_id`，然后第 232 行 `insert_one(doc)`。这是 agent_id 唯一落库点。注释里的文档结构（trace_storage.py:9-24）也确认 `agent_id` 是顶层字段。
2. `src/infra/session/dual_writer.py:179-195` — `DualEventWriter.create_trace()` 只是透传，把 `agent_id` 参数转交给 `trace.create_trace(...)`。**重要**：`dual_writer.py:112-132` 的 `_build_mongo_bulk_operations` 里 `$setOnInsert` 只有 `session_id/run_id/status/started_at`，**不含 agent_id**。但这条路径（write_event 触发的 upsert）不会覆盖 agent_id——因为 create_trace 已经先 insert 了完整 doc；bulk_write 的 upsert 只在 trace 不存在时用 `$setOnInsert`，此时 agent_id 不会被设置（会缺失）。不过实践中 create_trace 先于 write_event 调用（见 presenter_storage._ensure_trace），所以正常 trace 都有 agent_id。
3. `src/infra/writer/presenter_storage.py:122-129` — `_ensure_trace()` 调用 `dual_writer.create_trace(..., agent_id=self.config.agent_id, ...)`。agent_id 来自 PresenterConfig。
4. `src/infra/task/executor.py:94-105` — Presenter 用 `PresenterConfig(agent_id=agent_id, ...)` 构造，agent_id 来自 `run_task` 参数。
5. `src/infra/task/manager.py:230-343` — `submit(session_id, agent_id, ...)` 把 agent_id 透传给 `task_executor.run_task(..., agent_id, ...)`。
6. `src/api/routes/chat.py:281` / `chat.py:356` / `chat.py:426` — `agent_id` 是路由路径参数 `/{agent_id}/stream`、`/{agent_id}/chat`，即 **URL 里的 agent_id**。
7. `src/agents/fast_agent/graph.py:48` `_agent_id = "fast"`；`src/agents/search_agent/graph.py:54` `_agent_id = "search"`；`src/agents/team_agent/graph.py:48` `_agent_id = "team"`。`src/agents/core/base.py:130` `_agent_id: str = "base"`。

**结论：traces.agent_id 的取值是 Agent factory 注册的固定字符串 ID（"fast"/"search"/"team" 等），由前端在 URL 里指定。它与 persona_preset 的 ObjectId 完全是两个命名空间。**

### 问题 2：traces 里是否还有其他能关联 persona preset 的字段？

**没有。** 全量 grep `merged_preset_id` / `preset_id` / `agent_id` 在 src/infra/ 和 src/api/routes/ 的结果：

- `merged_preset_id`：**全代码库只有一处出现**——`src/infra/analytics/storage.py:387` 的 docstring 注释里提到"通过 metadata.merged_preset_id 或 agent_id 关联"。没有任何写入点把它写进 traces.metadata。该注释是**臆想的字段**，实际不存在。
- `preset_id` / `persona_preset_id`：traces 写入链路（trace_storage.py / dual_writer.py / presenter_storage.py / event_merger.py）中**零出现**。`persona_preset_id` 只出现在：
  - `src/api/routes/chat.py:242-243` — `conversation_config["persona_preset_id"] = request.persona_preset_id`，写入 **sessions.metadata**（经 `session_manager.update_session_metadata`），不是 traces。
  - `src/infra/task/recovery.py:296` — `"persona_preset_id": session_metadata.get("persona_preset_id")`，从 session metadata 读，仍属于 sessions 侧。
  - `src/api/routes/share.py:353,371` — 从 session metadata 读 persona_preset_id 用于分享页。
  - `src/infra/agent/wecom/handler.py:248` — WeCom 入口构造请求时带 `persona_preset_id`，最终同样落进 sessions.metadata。
  - `src/kernel/schemas/agent.py:47` — `AgentRequest.persona_preset_id` 请求字段。
  - persona_preset 自身的 CRUD（storage/manager/routes）和 team 成员配置。
- `agent_id` 在 traces 上的写入点：仅 `trace_storage.py:220`（见问题 1）。其他 agent_id 出现都是 events 数组里的 `data.agent_id`（presenter_events.py 注入，用于子代理事件区分）或 sessions/roles 的 agent_id，与 traces 顶层 agent_id 同源（都是 factory ID）。

**trace 的 metadata 内容**（presenter_storage.py:78-84 `_build_trace_metadata`）：只有 `agent_name` + 身份元数据（`_build_identity_metadata`），**不含 preset_id**。

### 问题 3：persona_presets 集合到底有没有 agent_id 字段？

**没有。** 证据：

- `src/kernel/schemas/persona_preset.py:129-157` — `PersonaPreset` 模型字段完整清单：`id, scope, owner_user_id, name, description, avatar, tags, system_prompt, starter_prompts, skill_names, visibility, status, source_preset_id, copied_from_version, version, usage_count, is_favorite, is_pinned, last_used_at, created_by, updated_by, created_at, updated_at`。**无 agent_id**。
- `src/infra/persona_preset/storage.py:98-107` — `create()` 把传入 `data` dict 直接 spread 进 doc（`**data`），再加 created_at/updated_at。理论上调用方可以塞 agent_id 进 data，但 grep 全仓 `persona_preset` 相关写入点（manager.py 的 create/copy、persona_preset.py 路由）**没有任何地方传入 agent_id**。
- `src/infra/persona_preset/storage.py:77-86` — `_REQUIRED_DEFAULTS` 也不含 agent_id。
- `.trellis/tasks/06-11-analytics-dashboard/research/data-sources-inventory.md:213-239` 列出的 persona_presets 字段表也无 agent_id，与本次核查一致。

**因此 `storage.py:422` 的 `{"$eq": ["$agent_id", "$$agent_id_str"]}` 分支是死代码——persona_presets 没有 agent_id 字段，该条件永远不命中。**

### 问题 4：traces.agent_id 的取值空间 + 真实样例推断

取值空间 = `AgentFactory` 注册的 agent ID 字符串集合。从代码推断的真实样例：

1. **"search"** — 用户用默认 search agent 聊天（`chat.py:356` 默认 `agent_id: str = "search"`，`search_agent/graph.py:54`）。这是最常见值，绝大多数 trace 的 agent_id = "search"。
2. **"fast"** — 用户选 fast agent（`fast_agent/graph.py:48`）。
3. **"team"** — team agent 多成员协作（`team_agent/graph.py:48`，`chat.py:251` 有 `agent_id == "team"` 分支）。
4. 极少数情况可能为 `"default"` — `src/kernel/schemas/session.py:37` `agent_id: str = "default"` 是 Session schema 默认值，`src/infra/session/storage.py:166` 创建 session 时 `session_data.metadata.get("agent_id", "default")`。但 trace 的 agent_id 来自 presenter config，走的是 chat 路由的路径参数，不会是 "default"（除非有未走标准路由的入口）。

**这些值都不是 ObjectId 格式**（ObjectId 是 24 位 hex），所以 `storage.py:421` 的 `{"$toString": "$_id"} == $$agent_id_str` 分支也不可能命中——persona_preset 的 `_id` 转 string 是 24 位 hex，永远不等于 "search"。

**推论：现有 `get_tokens_by_preset` 的 $lookup 永远查不到 preset 名字，label 会 fallback 成 `$_id`（即 "search"/"fast"/"team"）。所以当前线上的"按角色预设"饼图实际显示的就是 Agent 类型名，且因为绝大多数是 "search"，基本只有一个扇区。**

### 问题 5：给 implement 的明确建议

**推荐方案 A（PR1 内可行，最小改动）**：降级为"按 Agent 类型聚合"，删掉无效 $lookup。

在 `src/infra/analytics/storage.py` 的 `get_tokens_by_preset`（379-455 行）中：

1. 保留 `$match` → `$unwind` → `$group _id: "$agent_id"` 的主链路（402-411 行），这是有效的，按 agent_id 分组 token 总量。
2. **删除 412-432 行的整个 `$lookup` persona_presets block**（死代码，永远查不到）。
3. **改 label 生成逻辑**（433-443 行）：直接用 agent_id 作为 label，或用 agent_name。但 traces 里 agent_name 在 metadata 里（`presenter_storage._build_trace_metadata` 写了 `metadata.agent_name`），可以 `$ifNull: ["$metadata.agent_name", "$_id"]`。不过更简单稳妥：直接 `label = "$_id"`（即 agent_id 字符串），前端再做友好显示。
4. **重命名语义**：把方法/docstring/前端 i18n key 从"按角色预设/persona preset"改为"按 Agent 类型/by agent"。涉及：
   - `storage.py:385-389` docstring
   - `src/api/routes/analytics.py:130-144` 端点描述"按角色智能体（persona）Top N token 消耗"——注意路由路径仍是 `/tokens/by-preset`，可保留路径不变（避免 breaking change）但描述改掉，或后续 PR 重命名。
   - 前端 `AnalyticsPanel.tsx:818-829` 的 `analytics.tokens.byPreset` / `byPresetHint` i18n 文案 + `frontend/src/i18n/locales/*.json` 对应 key。

**不推荐方案 B（PR1 外）**：若产品方坚持按 persona preset 统计 token，需改写入链路——在 `presenter_storage._ensure_trace` / `_build_trace_metadata` 里把 `persona_preset_id` 写进 traces.metadata，再在 aggregation 里按 `metadata.persona_preset_id` 分组并 $lookup persona_presets.name。但这要改 presenter config 传递 persona_preset_id（目前 PresenterConfig 不带 persona_preset_id，见 executor.py:94-105），影响面大，属 PR2 范围。本 PR 不要做。

**实施前需向产品确认**：当前 PRD 9 个完善点里，"按预设统计 token"这一项的真实诉求是"按 Agent 类型"还是"按 persona preset"？若是后者，本 PR 只能把该卡片标记为"数据不可用/待 PR2"，不要硬凑。

---

## Caveats / Not Found

- 未连接真实 MongoDB 验证 traces.agent_id 的实际分布，结论基于写入链路代码推断。但写入链路单一且明确（trace_storage.create_trace 唯一落库点），推断可信度高。
- `session_events_counter` 集合（trace_storage.py:27 注释提及）与本次调研无关，未深入。
- 若历史上曾有迁移脚本给 persona_presets 补过 agent_id 字段，则方案 A 的"死代码"判断需打折扣——但 grep 仓内无此类迁移脚本，且 PersonaPreset schema 也不认该字段，可判定不存在。
