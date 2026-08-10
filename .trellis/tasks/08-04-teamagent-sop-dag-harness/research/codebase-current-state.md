# Research: TeamAgent 丝滑 harness（SOP DAG 规划与可视化）—— 代码库现状

- **Query**: 摸清团队模型 / TeamAgent 现状 / 审批基础设施 / 事件与 presenter / 前端聊天与团队 UI / 附件沙箱链路 / 昨日撤回实现（9c99d480→eca662a2）/ 现有测试
- **Scope**: internal
- **Date**: 2026-08-04

> 前置结论（供主 agent 直接引用）：
> - 昨日（08-03）实现 `9c99d480 feat(team): add approved TeamAgent orchestration harness` 已被 `eca662a2 Revert` 整体撤回（见 §7）。当前工作区已回到「团队路由、无规划/审批」状态，`src/agents/team_agent/` 仅剩 5 个文件：`graph.py / nodes.py / context.py / state.py / prompt.py`（`.pyc` 缓存里仍有 orchestration/roster/attachments 残留，但无源码）。
> - 仓库根 `research/` 下有两份**直接相关的前置调研**：`multi-agent-sop-orchestration.md`（架构/数据模型/事件契约建议）与 `dag-visualization-frontend.md`（React Flow DAG 渲染方案）——新设计应以此为基础，配合 §7 的「值得复用概念」。
> - 被撤回的 `.trellis/spec/backend/teamagent-harness.md` 与 `.trellis/spec/frontend/teamagent-plan-ui.md` 仍可从 `git show 9c99d480:<path>` 取回，是现成的契约文档模板。

---

## 1. 团队模型（Team）

### 1.1 Schema（`src/kernel/schemas/team.py`）

| 类型 | 关键字段（行号） |
|---|---|
| `TeamMemberCreate` | `member_id`(可选,自动生成)、`persona_preset_id`(必填)、`role_name`(≤80)、`role_avatar`、`role_tags`(≤20)、`role_instructions`(≤2000)、`position`、`enabled`（`team.py:23-32`） |
| `TeamMemberResponse` | 同字段 + `member_id` 必填（`team.py:47-55`） |
| `TeamCreate` | `name`(1-80)、`description`、`avatar`、`tags`(≤20,自动去重)、`members`(≤20)、`default_member_id`、`team_instructions`(≤4000)、`starter_prompts`(≤20, `PersonaStarterPrompt`)（`team.py:58-86`） |
| `TeamUpdate` | 全可选（`team.py:89-108`） |
| `TeamPreferenceUpdate` | `is_favorite` / `is_pinned`（`team.py:112-116`） |
| `TeamResponse` | `id`、`owner_user_id`、`visibility`(仅 `private`)、`is_favorite/is_pinned/last_used_at`；**`active_members` 是 `@property`：过滤 `enabled` 的成员**（`team.py:119-145`，property 在 143-145） |
| 常量 | `TEAM_MEMBERS_MAX=20`、`TEAM_TAGS_MAX=20`、`TEAM_STARTER_PROMPTS_MAX=20`（`team.py:12-14`） |

### 1.2 存储（`src/infra/team/storage.py`）

- MongoDB collection：**`teams`**（`storage.py:43-45`），按 `owner_user_id` 隔离；成员文档 `_member_doc` 自动生成 `member_id`（`m-<hex12>`）（`storage.py:88-101`）。
- `default_member_id` 解析：无有效值时回退到第一个成员（`storage.py:130-145`）；**`update_team` 对 members 是全量替换语义，会重新生成 member_id**（`storage.py:443-453`，注意 clone/update 后 member_id 会变）。
- 用户偏好（favorite/pinned）存 `users` collection 的 `metadata.pinned_team_ids/favorite_team_ids`（`storage.py:181-251`）。
- `_doc_to_response` 负责 Mongo 文档→`TeamResponse`（`storage.py:141-177`）。

### 1.3 业务层（`src/infra/team/manager.py`）

- `TeamManager` 组合 `TeamStorage` + `PersonaPresetManager`（`manager.py:25-31`）。
- **`_hydrate_member_display_metadata`**：用 persona preset 的 `name/avatar/tags` 回填 member 的 `role_name/role_avatar/role_tags`（preset 为空时保留用户自定义值）（`manager.py:34-57`）——即 `role_name` 是「用户覆盖 + preset 兜底」。
- **`resolve_team_for_runtime`（团队运行入口）**：存在性校验 + `active_members` 非空 + `validate_team_members`（preset 必须存在）过滤，返回净化后的 `TeamResponse`（`manager.py:219-234`）。
- 单例 `get_team_manager()`（`manager.py:238-245`）。

### 1.4 CRUD 路由（`src/api/routes/team.py`，前缀 `/api/teams`）

- `GET ""` list（favorite/pinned/q/tag 过滤，limit≤200）、`POST ""` create(201)、`GET /{team_id}`、`PUT /{team_id}`（全量替换）、`PATCH /{team_id}/preference`、`DELETE /{team_id}`(204)、`POST /{team_id}/clone`（`team.py:24-120`）。均需登录，scope 到 `user.sub`。

### 1.5 团队如何被聊天调用（team_id 链路）

- `AgentRequest.team_id`：`src/kernel/schemas/agent.py:63`（`Optional[str]`，注释 "Team ID for team agent mode"）。
- `chat.py /stream`：`validate_team_agent_request(agent_id, request)`（`chat.py:422`）→ 校验后 **显式团队模式强制清空 `enabled_skills` 与 persona**（`chat_validation.py:6-14`）→ `team_id` 写入 `task_context`（`chat.py:462`）→ arq/submit 传 `team_id`（`chat.py:570, 593`）→ 最终 `_execute_agent_stream(... team_id=...)`（`chat.py:289, 317`）→ `agent.stream(... team_id=team_id)`。
- `build_conversation_config`：`agent_id == "team" and request.team_id` 时写 `conversation_config["team_id"]`（`chat.py:245-246`），供 session 元数据持久化。
- `TeamAgent._stream`：`team_id = kwargs.get("team_id")`（`graph.py:143`）→ 写入 config 的 `configurable["team_id"]`（`graph.py:174`）→ `team_router_node` 里 `configurable.get("team_id")` 读取（`nodes.py:183`）。

---

## 2. TeamAgent 现状（`src/agents/team_agent/`，5 文件）

### 2.1 各文件职责

| 文件 | 职责 |
|---|---|
| `graph.py` | `@register_agent("team")` 的 `TeamAgent(BaseGraphAgent)`（`graph.py:39-46`）；外层图 **`START -> team_router_node -> END`**（`graph.py:83-87`）；`compile(checkpointer=None)`，注释明确警告"外层图扩展为多节点工作流需加独立命名空间的 checkpointer，避免与内层 message state 冲突"（`graph.py:96-104`）；`_stream` 构建 config/initial_state（`graph.py:113-198`），有 `goal:start/goal:end` 与 `presenter.done()` 收尾（`graph.py:226-241`）。 |
| `nodes.py` | `team_router_node` 单节点实现（见 2.2）+ `resolve_runtime_team`（`nodes.py:92-125`）。 |
| `state.py` | `TeamAgentState = {input, session_id, messages, output, attachments}`（`state.py:5-12`），很薄。 |
| `context.py` | `TeamAgentContext(FastAgentContext)` 空壳（`context.py:5-8`），复用 fast_agent 的工具/技能加载。 |
| `prompt.py` | router 系统提示（`TEAM_ROUTER_SYSTEM_PROMPT` legacy/compact_en/compact_zh 三套，`prompt.py:11-59`）、`build_team_members_description`（`prompt.py:93-109`）、`summarize_role_system_prompt`(≤500 字角色能力摘要，`prompt.py:112-118`)、**`build_team_member_subagent_type`（生成稳定 subagent_type：`team-<member_slug>-<role_slug>`，`prompt.py:158-166`）**、`build_team_subagent_display_names/avatars`（`prompt.py:141-155`）。 |

### 2.2 `team_router_node` 完整逻辑（`nodes.py:126-653`）

流程：
1. 取 `context`/`agent_options`/`attachments`（state 里），`LLMClient.get_model` 建 LLM（`nodes.py:140-158`），解析 fallback/vision（`nodes.py:161-174`）。
2. **团队解析**：`resolve_runtime_team(team_id=configurable.get("team_id"), ...)`（`nodes.py:179-184`）；无 `team_id` 或解析失败 → 返回 `None`/抛 `ValueError("team_not_found_or_unavailable")`（`nodes.py:92-125`）。显式团队模式下主代理不做 persona（`persona_sections = []`，`nodes.py:188-191`）。
3. **角色提示构建**：对每个 `team.active_members` 调 `preset_mgr.use_preset(...)` 取 `system_prompt/skill_names`，得到 `role_system_prompts / role_skill_prompts / role_summaries`（`nodes.py:209-234`）；`default_role` 由 `default_member_id` 或第一个成员决定（`nodes.py:237-258`）。
4. **Backend**：`ENABLE_SANDBOX=False` → `create_persistent_backend_factory`；否则 `get_session_sandbox_manager().get_or_create(session_id, user_id)` 拿 `(sandbox_backend, sandbox_work_dir)`，发 `sandbox:starting/ready/error` 事件，`create_sandbox_backend_factory`（`nodes.py:266-318`）。team 模式下 system_prompt 前拼 `SEARCH_SANDBOX_SYSTEM_PROMPT`（`nodes.py:305-306`）。
5. **内层 deep agent**：`create_deep_agent(model, system_prompt, backend, tools=filtered_tools, checkpointer=get_async_checkpointer(thread_id=session_id), store, subagents=custom_subagents, middleware=user_middleware)`（`nodes.py:540-550`）。
6. **子代理构建**：每个成员生成一个 `custom_subagents` 项——`name=subagent_type`、`description` 含 member_id、`system_prompt=SUBAGENT_PROMPT`、`middleware=_build_subagent_middleware(...)` 注入 `build_role_subagent_section`（角色/团队指令/role_instructions）+ 技能 + 记忆 + 沙箱段（`nodes.py:399-465`）。**`task` 工具是 DeepAgents 内建的分派机制，router 通过 prompt 指引 LLM 使用**，没有应用层校验。
7. 无团队/无成员回退：单个 `general-purpose` 子代理 + `FAST_SYSTEM_PROMPT`（`nodes.py:469-487, 256-260`）。
8. **事件**：`AgentEventProcessor(presenter, subagent_display_names, subagent_avatars)` + `inner_graph.astream_events(v2)`（`nodes.py:586-616`）。
9. 附件：vision 支持时 `inline_image_attachments_as_data_urls`，否则 `describe_image_attachments`，再 `build_human_message`（`nodes.py:574-585`）。

**注意（用户特别指出）**：`nodes.py` 当前**没有规划/审批阶段**——路由与执行在同一个 deep agent 内循环里即时完成，`team_router_node` 是唯一节点。

---

## 3. 审批 / 人工介入基础设施

### 3.1 存储模型（`src/infra/storage/mongodb.py`）

- `PendingApproval`：`id/message/type(默认"form")/fields/status("pending")/session_id/user_id/created_at/expires_at/extensions`（`mongodb.py:153-165`）。
- `ApprovalResponse`：`approved: bool` + `response: dict`（`mongodb.py:168-172`）。
- `ApprovalStorage`（collection `approvals`）：`create/get/list_pending/get_response/update_status/extend_expires_at/delete`（`mongodb.py:175+`），索引 `ensure_indexes`（幂等）。

### 3.2 API 契约（`src/api/routes/human.py`，前缀 `/api/human`）

| 路由 | 行为 |
|---|---|
| `GET /pending` | 返回当前用户 pending 列表 `{approvals, count}`（`human.py:231-244`，权限 `chat:write`） |
| `POST /{approval_id}/respond?approved=&response=` | 幂等写响应，`approved=true→"approved"` 否则 `"rejected"`；Redis Pub/Sub `notify_approval_response` + 本地 `asyncio.Event` 唤醒（`human.py:245-284`） |
| `POST /{approval_id}/extend?extra_seconds=` | 延长 expires_at，最多 `MAX_EXTENSIONS`（`human.py:287-305`） |
| `GET /{approval_id}` | 详情；不存在返回 `{id, status:"not_found"}` 200（`human.py:307-315`） |
| `DELETE /{approval_id}` | 取消并清理（`human.py:318-331`） |

- Agent 侧核心函数：`create_approval(message, approval_type, fields, session_id, user_id)`（`human.py:105-145`）、`wait_for_response(approval_id, timeout=300)`（本地 Event + MongoDB 轮询双通道，`human.py:147-208`）。创建时经 `_notify_approval_created` 回调通知前端（`human.py:56-66`，注册见 `register_approval_callback`）。
- **撤回实现在此文件加过 `team_plan` 幂等分支**：`respond_to_approval` 对 `type=="team_plan"` 且已处理的情况返回既有决策而非 400（见 §7.4 diff）。

### 3.3 ask_human 工具（`src/infra/tool/human_tool/tool.py`）

- `AskHumanTool`（name=`ask_human`）：入参 `AskHumanInput{message, fields[], timeout(10-3600, 默认300), allow_other}`（`models.py:101-120`）；`FieldType = text/textarea/number/checkbox/select/multi_select`（`models.py:14-35`）；`FormField{name,label,type,placeholder,default,required,options}`（`models.py:40-68`）。
- `_arun` 流程：解析字段 → `create_approval(approval_type="form")` → **`_send_approval_event` 通过 `dual_writer.write_event(session_id, event_type="approval_required", data={id,message,type,fields,timeout}, run_id)` 推送 SSE**（`tool.py:319-364`）→ `wait_for_response` → 返回 JSON：`{status: success|timeout|rejected, message, values}`（`tool.py:111-209`）。
- `get_human_tool(session_id)` 工厂（`tool.py:369-379`）。session_id 优先取 `TraceContext`（`tool.py:148-157`）。

### 3.4 前端审批处理

- **`useApprovals`（`frontend/src/hooks/useApprovals.ts`）**：`approvals[]` 状态、`fetchApprovals`（轮询 `GET /human/pending`）、`addApproval`（SSE 发现时插入）、`respondToApproval`（`POST /human/{id}/respond`，成功后从列表移除）、`clearApprovals`（`useApprovals.ts:10-96`）。
- **SSE 消费**：`eventHandlers.ts` `case "approval_required"` → `handleApprovalRequired`：先 `GET /human/{id}` 确认 pending，再回调 `ctx.options.onApprovalRequired({id, message, type, fields, expires_at, timeout})`（`eventHandlers.ts:250-255, 507-536`）。
- **`ApprovalPanel`（`frontend/src/components/panels/ApprovalPanel.tsx`）**：多审批轮播（左右切换）、表单字段渲染、提交/取消、超时倒计时 + `extend` 接口（`ApprovalPanel.tsx:195-549`）。
- **接线点**：`ChatAppContent.tsx` 的 `useAgent({ onApprovalRequired, onClearApprovals })` —— 发 `appNotificationService.notify({type:"approval"})` + `addApproval(...)`（`ChatAppContent.tsx:189-208`）；`ApprovalPanel` 由 `ChatView` 侧渲染（approvals 来自 `useApprovals`）。
- **`useWebSocket`**（`frontend/src/hooks/useWebSocket.ts`）**不处理审批**——只处理 `task:complete` / `notification:feedback` 两类通知（`useWebSocket.ts:168-175`），是任务完成/通知通道，与审批无关。

---

## 4. 事件 / Presenter 基础设施

### 4.1 Presenter（`src/infra/writer/`）

- `Presenter(EventPresenterMixin, StoragePresenterMixin)`（`present.py:42`）：同步 `present_*` 构建事件（`presenter_events.py`），异步 `emit_*` 构建 + `save_event` 持久化（`present.py:114-244`），事件经 Redis + MongoDB 双写（`DualEventWriter`）。
- 事件类型清单（`presenter_events.py`，`_build_event` 统一加 `depth/agent_id/timestamp`，`presenter_events.py:83-109`）：

| 事件 | 方法 | 数据 |
|---|---|---|
| `metadata` | `presenter_events.py:116-128` | session_id/agent_id/agent_name/trace_id/run_id |
| `message:chunk` | `:129-154` | content/text_id |
| `summary` | `:158-179` | content/summary_id |
| `recommend:questions` | `:184-203` | questions[] |
| `thinking` | `:207-227` | content/thinking_id |
| `todo:updated` | `:233-251` | todos[] |
| `agent:call` / `agent:result` | `:257-313` | step/agent_name/input; result/success/error |
| `tool:start` / `tool:result` | `:317-383` | tool/args(截断)/tool_call_id; result/success |
| `approval_required` | `:387-417` | id/message/type/choices/default |
| `user:message` | `:421-435` | content/attachments/message_id/run_id |
| `sandbox:starting` / `sandbox:ready` / `sandbox:error` | `:437-475` | sandbox_id/work_dir; error |
| `token:usage` | `:478-516` | tokens/duration/model |
| `skills:changed` | `:519-537` | action/skill_name/files_count |
| `done` | `:543-554` | status/trace_id/steps/tool_calls |
| `error` | `:557-569` | error/type/trace_id |

- **撤回提交新增 `present_team_event`**（白名单 `{"team:plan","team:step","team:run","approval_required"}`）+ `emit_team_event`（见 §7.4）。
- `save_event` 由 `StoragePresenterMixin`（`presenter_storage.py`）负责 → `DualEventWriter.write_event(session_id, event_type, data, run_id)`（`dual_writer.py:206`），Redis Stream + Mongo 批量落盘。

### 4.2 SSE 推送链路

- `POST /api/chat/stream` 提交任务 → 后台 executor（`_execute_agent_stream`，`chat.py:276-345`）→ 事件经 `DualEventWriter` 写 Redis/Mongo。
- 前端 SSE：`GET /api/chat/sessions/{session_id}/stream?run_id=`（`chat.py:616+`）由 `connectToSSE`（`sseConnection.ts:97-142`，`fetchEventSource`）消费；历史回放走 `GET /api/sessions/{session_id}/events?run_id=&completed_only=true`（`session.py:274-341`）。
- 前端消费：`handleStreamEvent(event, messageId, eventId, timestamp, ctx)`（`eventHandlers.ts:53`），事件按 `event.data` 里 `depth/agent_id` 区分主/子代理；`processedEventIdsRef` + 时间戳双重去重（`eventHandlers.ts:61-98`）；`historyLoader.ts` 按事件重建消息。

---

## 5. 前端聊天与团队 UI

### 5.1 useAgent 系列（`frontend/src/hooks/`）

- `useAgent.ts`：核心 hook。**`selectedTeamId` state（`useAgent.ts:77`）**；`selectTeam(teamId)`（`useAgent.ts:885-887`）；发送时 `requestTeamId = currentAgent === "team" ? selectedTeamId : null`（`useAgent.ts:604`），写 `conversationConfig.team_id`（`useAgent.ts:679-680, 727-728`）；返回 `selectedTeamId/selectTeam/...`（`useAgent.ts:806, 971`）。
- 子模块：`sseConnection.ts`（SSE 连接/重连）、`eventHandlers.ts`（事件分发）、`eventProcessor.ts`（消息 parts）、`historyLoader.ts`（历史重建）、`agentSelection.ts`、`types.ts`（`UseAgentOptions/UseAgentReturn/EventType/EventData/HistoryEventData`，`types.ts:257` 有 `selectedTeamId`）。
- 撤回提交为 teamPlan 加了：`teamPlan` state、`team:plan/team:step/team:run/approval_required(team_plan)` 四个事件类型（`types.ts:24-30`）与 `TeamPlanState` 类型、事件处理分支、历史重建（`reduceTeamPlan` 折叠）、`canAttachEventTypeToPreviousAssistant` 排除 plan 事件（详见 §7.5）。

### 5.2 ChatView 与团队选择交互

- `ChatView.tsx`：`useCurrentTeam(currentAgent, selectedTeamId)` 取当前团队（`ChatViewProps.tsx:27-38`，`teamApi.get(selectedTeamId)`）；`currentTeam` 决定 `assistantIdentity`（`ChatView.tsx:188-198`）；`selectedTeamId` 传给 ChatInput（`ChatView.tsx:459`）。
- `ChatInput.tsx`：接收 `selectedTeamId`（`:86`）；团队 mention 模式 `@` 触发 `TeamMentionPopup`（`:228-243`，`:638`）。
- `TeamMentionPopup.tsx`：输入框 `@` 弹出的团队候选列表（头像/名称/成员数），选中即切换团队。
- `TeamPickerModal.tsx`：全屏模态选团队（搜索、`teamApi.list`），`onSelect(teamId)`、`onCreateNew`、`onManageTeams`（`:20-93`）。
- `ChatInputToolbar.tsx`：团队模式下展示当前团队 chip + 换队按钮（`:112-121, 185, 225`）。
- 团队模式激活条件：`currentAgent === "team" && selectedTeamId`（`useAgent.ts:604`）。

### 5.3 /team 页面与 TeamBuilder

- 路由：`App.tsx` `path="/team"` → `TeamPage` → `<AppContent activeTab="team" />`（`App.tsx:254-260, 543-546`）。
- `TabContent.tsx`：`team: TeamBuilderPanel`（lazy `TeamBuilderWrapper`）（`TabContent.tsx:53-56, 79`）。
- `TeamBuilderWrapper.tsx`：`TeamBuilder` + footer 状态（`TeamBuilderWrapper.tsx:173-197`）。
- `components/team/` 其他：`TeamBuilder.tsx`（主构建器）、`TeamRoster.tsx`、`TeamMemberCard.tsx`、`RoleSquare.tsx`、`TeamAvatar.tsx` + `teamAvatarUtils.ts`（头像/回退标签）、`teamExport.ts`。测试：`teamBuilderPresentation/teamBuilderUseAction/teamBuilderTagsImportExport/teamAvatarSurfaces/teamExport`。
- 前端类型：`types/team.ts` `Team{id,name,members,default_member_id,team_instructions,starter_prompts,...}`、`TeamMember{member_id,persona_preset_id,role_name,role_avatar,role_tags,role_instructions,position,enabled}`（`:4-32`）。
- API 客户端：`services/api/team.ts`（BASE `/api/teams`）；`services/api/session.ts` 发消息时 `body.team_id = teamId`（`session.ts:96, 116-117`）。

---

## 6. 附件 / 沙箱路径

### 6.1 前端 → 后端

- 上传：`POST /api/upload/file`（`upload.py:392+`）→ S3/本地存储，key 形如 `{category}/{user_id}/{short_id}.{ext}`（`upload.py:493-499`）；返回 `{key, name, type, mimeType, size, url}`。
- 请求载体：`AgentRequest.attachments: list[AttachmentSchema]`，`AttachmentSchema{id, key, name, type, mime_type(alias mimeType), size, url}`（`agent.py:17-25`）。
- `chat.py`：`attachments_data = [a.model_dump() for a in request.attachments]`（`chat.py:428-429, 521-522`）→ task_context/submit 传入 executor（`chat.py:455, 564, 586`）。

### 6.2 后端 → Agent

- `_execute_agent_stream(... attachments=...)` → `agent.stream(... attachments=...)`（`chat.py:284-312`）。
- `TeamAgent._stream`：`attachments = kwargs.get("attachments", [])` → `initial_state["attachments"]`（`graph.py:184-191`）。
- `team_router_node`：`attachments = state.get("attachments", [])`（`nodes.py:148`）；有 vision → `inline_image_attachments_as_data_urls`（从 `key` 拼 `base_url/api/upload/file/{key}` 或内联 data_url）；无 vision → `describe_image_attachments`（`nodes.py:574-585`）；最后 `build_human_message(user_input, attachments, supports_vision)`（`node_utils.py:279+`，图像以 URL/data_url 多模态形式进 HumanMessage，其余转文本摘要）。

### 6.3 沙箱侧

- `CompositeBackend` 来自 `deepagents.backends`（**不是项目自定义类**）：`src/infra/sandbox/session_manager.py:492-513, 753-970` —— `CompositeBackend(default=<provider backend>, routes={"/skills/": skills_backend})`；`sandbox_backend.default` 是 provider 实例（e2b/daytona/osb），`work_dir` 由 provider 给出。
- 当前链路**没有**把附件预置进沙箱工作目录的代码——附件只在 HumanMessage 里以 URL/data_url 形式出现；撤回实现补了 `materialize_attachments`（下载 storage key → 上传到 `<work_dir>/attachments/<attachment-id>/<safe-name>`），见 §7.3。

---

## 7. 撤回实现（`9c99d480`，可复用概念清单）

> 全部内容可 `git show 9c99d480:<path>` 取回。以下提炼**概念与契约**，不是照抄建议。

### 7.1 `src/agents/team_agent/orchestration.py`（594 行，核心新模块）

- **状态枚举**：`TeamStepStatus{pending,running,succeeded,failed,cancelled,retrying}`、`TeamRunStatus{planning,awaiting_confirmation,approved,rejected,running,partial_failure,cancelled,completed,failed}`、`TeamApprovalState{not_required,pending,approved,rejected,timed_out,cancelled}`（orch:34-58）。
- **`TeamPlanStep`**（frozen）：`step_id/ordinal/objective/subagent_type/member_id/dependencies/attachment_ids/required_artifacts/expected_completion/required/status`（orch:64-76）。
- **`TeamPlan`**（frozen）：`plan_id/team_run_id/version/summary/roster/attachment_manifest/steps/completion_criteria/approval_state/created_at`；`model_validator` 做引用校验：role 名 ∈ roster、attachment_id 引用存在、dependency 引用存在、无自依赖、**DFS 环检测**、`attachment_manifest.successful` 才允许带步骤（orch:85-137）；`requires_approval`（有 delegated step 即 true）、`approved()`（orch:139-143）。
- **`TeamHandoff`**（frozen）：`handoff_id/plan_id/team_run_id/step_id/subagent_type/objective/context/attachment_paths/predecessor_artifacts/expected_artifacts/attempt`；`to_task_description()` 序列化为 `{"team_handoff": ...}` JSON 塞进 DeepAgents `task(description)`，`from_task_description()` 反解（orch:146-176）——**兼容桥**。
- **`TeamRoleResult`**（frozen）：`handoff_id/plan_id/team_run_id/step_id/subagent_type/success/summary/artifacts/error/attempt`（orch:178-193）。
- **`TeamRunMetadata` + `TeamRunStore`**：`team_runs` collection，独立于内层 message checkpoint 的持久化（orch:198-244）。
- **`build_team_plan`**：确定性预检计划（不调 LLM）——每个成员一个 step，objective = "Handle the user's objective as the {role}"，attachment_ids 全量带（orch:246-283）。
- **`validate_handoff_for_plan`**：拒绝未批准计划 / 未知 role/step / 附件路径集合不匹配 / 前置产物缺失（orch:286-316）。
- **`TeamTaskGuardMiddleware(AgentMiddleware)`**：包在 DeepAgents middleware 栈里 `awrap_tool_call` 拦截 `task` 工具——校验 handoff、依赖完整性、`ToolMessage(status="error")` 视为失败、持久化 step 结果、发 `team:step` 事件（orch:349-507）。
- **`request_team_approval`**：`create_approval(approval_type="team_plan", fields=[{"name":"plan","type":"team_plan","value":plan_json}])` → 发 `approval_required(approval_type="team_plan")` + `team:plan(awaiting_confirmation)` 事件 → `wait_for_response` → approved 则 `plan.approved()`，rejected 带 feedback、timeout 抛错、全程发 `team:run` 事件（orch:523-575）。
- `emit_team_event`（orch:512-519）统一事件出口。

### 7.2 `src/agents/team_agent/roster.py`（155 行）

- `TeamRosterMember`（frozen）：`subagent_type/member_id/role_name/position/persona_preset_id/persona_version/persona_snapshot`（rost:16-25）。
- `TeamRoster`（frozen）：`members/default_member_id/include_general_purpose`，`subagent_types`/`default_member` properties（rost:29-46）。
- `compile_team_roster(team, persona_snapshots, include_general_purpose=False)`：按 `(position, member_id)` 稳定排序、拒绝空 member_id / 重复 subagent_type / 保留名 `general-purpose`、显式团队默认不带 general-purpose（rost:55-132）。

### 7.3 `src/agents/team_agent/attachments.py`（278 行）

- `AttachmentManifestItem{attachment_id(alias id), key, name, mime_type(alias mimeType), size, sandbox_path, status(pending/materialized/failed), error}`（att:29-46）；`AttachmentManifest{attachments, work_dir}` + `by_id`/`successful`（att:50-65）。
- `AttachmentMaterializer.materialize(attachments, backend, work_dir, user_id)`：解包 `CompositeBackend`（`backend.default`，att:93-95）→ `_destination` 构造安全 POSIX 路径 `<work_dir>/attachments/<safe_id>/<safe_name>`（防路径穿越，att:78-90）→ **`_validate_record` 校验 FileRecord 所有权**（att:223-232）→ 已存在且 size 相同则复用 → 否则 `storage.download_file(key)`（校验 size）→ `aupload_files` 上传并核对返回 path（att:139-218）。
- 失败分 stage：`validation/ownership/download/upload/verification`，结构化错误进 manifest（att:20-27, 243-253）。

### 7.4 后端小 diff

- `src/api/routes/human.py`（+13）：`respond_to_approval` 对 `type=="team_plan"` 且已处理 → 返回既有决策 `{status, approval_id, approved, idempotent:true}`（幂等，防双击/重连 400）。
- `src/infra/writer/presenter_events.py`（+6）：`present_team_event(event_type, data)` 白名单 `{"team:plan","team:step","team:run","approval_required"}`。
- `src/infra/writer/present.py`（+6）：`emit_team_event(event_type, data)` = present + save_event。

### 7.5 前端 diff

- `frontend/src/types/teamPlan.ts`（290 行）：`TeamPlanStatus/TeamStepStatus/AttachmentMaterializationStatus` 联合类型、`TeamPlanState{plan_id,team_run_id,approval_id,version,summary,status,rejection_feedback,attachments[],steps[]}`、`TeamPlanEvent`；**`normalizeTeamPlanEvent(eventType, payload)`**（容忍后端字段别名：`attachment_manifest.attachments`、`ordinal`、`required_artifacts`、`error.reason`、approval 事件 `id`；未知状态归一为安全默认，`plan.ts:179-245`）；**`reduceTeamPlan(current, event)`**（增量合并，补全字段、按 order 排序 steps，`plan.ts:248-289`）。
- `useAgent.ts`：`teamPlan` state + `setTeamPlan`；历史加载时从 events 里反向折叠重建 `TeamPlanState`（`latestPlanEvent` 判定）；`isTaskRunning` 加入 `awaiting_confirmation`；clear/cancel 时 `setTeamPlan(null)`。
- `eventHandlers.ts`：`approval_required` 若 `data.approval_type==="team_plan" || data.plan` → 只走 `setTeamPlan`（**不落入通用 ApprovalPanel**）；新增 `case "team:plan"/"team:step"/"team:run"` → `reduceTeamPlan`。
- `historyLoader.ts`：`canAttachEventTypeToPreviousAssistant` 排除 4 个 plan 事件；history 里 `approval_type==="team_plan"` 的审批不重建进通用审批列表。
- `useAgent/types.ts`：`EventType` 加 4 事件、`EventData/HistoryEventData` 加 `approval_type/approval_id/plan_id/team_run_id/plan/step/rejection_feedback`、`UseAgentOptions.onApprovalRequired` 加 `approval_type/plan`。
- `TeamPlanPanel.tsx`（147 行）：状态徽章色映射、步骤列表（order/依赖/expected_artifacts/error/attempt）、附件行（materialized/failed/沙箱路径）、reject feedback 展示、approve/reject-with-feedback 按钮（`onRespond(approvalId, {feedback}, approved)`）。
- `ChatAppContent/ChatView`：接线 `teamPlan` → `TeamPlanPanel`。

### 7.6 值得复用的概念（建议，不照抄）

1. **应用层拥有 plan/run 契约，DeepAgents `task` 只做执行通道**——`TeamHandoff` JSON 信封 + `TeamTaskGuardMiddleware` 校验，比纯 prompt 约束可靠。
2. **计划冻结（frozen Pydantic）+ 引用校验 + 环检测**放在模型层，错误确定性可测。
3. **审批幂等**：`team_plan` 类型审批重复响应返回既有决策。
4. **事件白名单 `present_team_event`** + `emit_team_event`，前端 `normalizeTeamPlanEvent + reduceTeamPlan` 增量折叠、plan 事件不混入通用审批/消息路径、历史回放一致重建。
5. **持久化独立于 checkpoint**（`TeamRunStore`/`team_runs`），支持断点恢复。
6. **附件预置沙箱 + 所有权校验 + stage 化错误**，plan 引用 materialized manifest。
7. 前端 `TeamPlanPanel` 的交互范式（附件事物/步骤列表/确认反馈）可演进为 DAG 视图。

> 注意：撤回的 orchestration 是**确定性一成员一步**计划（不调 LLM）；新需求是 **LLM 动态生成 SOP DAG**（依赖/并行/阶段），这是核心差异点，§7 只贡献"契约骨架/校验/事件/持久化"层。仓库根 `research/multi-agent-sop-orchestration.md` §6 已给出 LLM 化 planner 的完整建议。

---

## 8. 现有测试

### 8.1 `tests/agents/`（现存）

- `test_team_agent_sandbox_support.py`：mock 后端测试 `team_router_node`——沙箱开启走 sandbox backend、关闭走 persistent backend、无效 team_id 抛 `ValueError`（`test_team_agent_sandbox_support.py:97-253`）。
- 其他 agent 级测试（persona/skill/goal 等）不直接涉及团队。

### 8.2 `tests/agents/`（撤回删除，可 `git show 9c99d480:` 取回）

- `test_team_agent_orchestration.py`（90 行）：plan 审批门禁 + handoff JSON 往返、required step 失败 → `PARTIAL_FAILURE`/cancel → `CANCELLED`、guard 拒绝未满足依赖（`test-orch.py:36-90`）。
- `test_team_agent_foundations.py`（86 行）：`materialize_attachments` 安全路径 + storage key 下载上传、roster 排序 + persona snapshot、重复 subagent_type 拒绝（`test-found.py:34-86`）。

### 8.3 `tests/api`（现存，team 相关）

- `test_chat_team_validation.py`：`validate_team_agent_request`——无 team_id 允许回退、显式团队剥离 enabled_skills/persona、其他 agent 忽略（`test_chat_team_validation.py:8-82`）。
- `test_team_routes.py`：`/api/teams` 路由冒烟（list/create/preference）（`test_team_routes.py:66-127`）。
- `test_share_team_metadata.py`：共享内容带团队显示元数据。
- `tests/unit/infra/test_team_manager.py`：TeamManager 单测（mock storage/persona_manager）。
- `tests/infra/tool/test_team_tool.py`：team 相关工具测试。
- **缺失**：无 team 路由全量 CRUD 契约测试、无 team_plan 审批幂等测试（原撤回实现里有对应测试但已删）。

---

## Caveats / 未覆盖

- `useAgent.ts` 的 `teamPlan` 接线细节与 `TeamPlanPanel` 挂载位置在撤回 diff 中有 `ChatAppContent.tsx:5 行 / ChatView.tsx:8 行 / ChatViewProps.tsx:2 行` 的删除，具体挂载 JSX 可从 `git show 9c99d480:frontend/src/components/layout/AppContent/ChatView.tsx` 查看。
- 团队前端 `selectTeam` 只在 `useAgent` 内存状态，不持久化到会话（会话的 team_id 由每次请求携带 `conversationConfig`）。
- `role_avatar` 在前端 Avatar 渲染与 `build_team_subagent_avatars`（subagent 头像）中都被使用；`role_name` 由 preset 兜底回填，创建时前端可能不传（`TeamMemberCreateRequest.role_name?`）。
- 本报告未验证 `tests/api` 是否还有 hidden 的 team 路由测试（搜索 `routes\.team|/api/team|TeamManager` 仅命中上述文件）。
- 沙箱 provider 具体实现（e2b/daytona/opensandbox）细节未展开，仅确认 `CompositeBackend(default=provider)` 结构与 `get_or_create(session_id, user_id) -> (backend, work_dir)` 契约。
