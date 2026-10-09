# Design — 定时任务二期

需求编号对应 `prd.md`。调研证据见 `research/*.md`。

## 0. 边界与原则

- **任务形式**：单任务、分阶段交付（见 `implement.md`）。各阶段都要改同一批核心文件（`runner.py`、`service.py`、`schemas/scheduled_task.py`），拆成子任务并行会冲突，所以不拆。
- **不重构聊天主链路 `chat_stream`**：runner 按 `chat_stream` 的顺序调用同一组函数。`chat.py` 只改两处：
  - `build_conversation_config` 新增可选参数 `extra`；
  - 定时会话首次续聊时取消隐藏（§4）。
- **开关与回滚**：全部新行为由 `SCHEDULED_TASK_ENABLED` 和新增的 `SCHEDULED_TASK_CHAT_TOOL_ENABLED` 控制。每个阶段单独提交，可按阶段 revert。
- **分支**：一期尚未合入 main。二期在 `feat/scheduled-tasks` 上继续开发，两期一起合入。

## 1. 数据模型（`src/kernel/schemas/scheduled_task.py`）

| 模型 | 变更 |
|---|---|
| `ScheduledTaskCreate` / `Update` | `agent_id: Literal["fast","search"]`（收窄入参）。`persona_preset_id` 传空串视为 None；Update 时显式传 null 表示清除专家（沿用 `exclude_unset` 语义）。 |
| `ScheduledTask` | 新增 `paused_by: Literal["user","admin","system"] \| None`、`created_via: Literal["web","chat"] = "web"`。`agent_id` 保持 `str`，兼容旧文档；runner 对 fast/search 以外的值一律按 `fast` 处理。 |
| `ScheduledTaskResponse` | 新增 `persona_name`、`persona_avatar`、`persona_available`、`paused_by`、`created_via`。 |

新字段都有默认值，旧文档可以直接读，不需要数据迁移。

**索引**（`storage.ensure_indexes`）：
- tasks 新增 `(status, updated_at↓)`、`(last_run_status, updated_at↓)`，供管理员筛选。
- 新集合 `scheduled_task_drafts`（见 §5）：`(owner_id, created_at↓)`，并在 `purge_at` 上建 TTL 索引。

**不可重试失败**：runner 内部新增 `NonRetryableRunError(message, pause_reason)`。`_monitor` 捕获后：
- 立即结束本次运行，不再重试；
- 任务立即暂停，写入 `paused_by="system"` 和 `pause_reason`；
- 推送通知。

适用场景：专家失效、模型被停用或用户无权使用该模型、创建人被停用或失去权限。这三类失败重试也不会成功。

## 2. R1 专家

### 2.1 校验与展示（新增 `src/infra/scheduler/persona.py`）

- `ensure_persona_usable(preset_id, actor) -> PersonaPreset`
  - 调用 `PersonaPresetManager().get_preset(id, user_id=actor.sub, is_admin="persona_preset:admin" in actor.permissions)`（`manager.py:213`），可见性规则与聊天一致（`manager.py:129-142`）。
  - `NotFoundError` / `AuthorizationError` 转为 `ScheduledTaskPersonaError("所选专家不存在或你无权使用")`。
  - 不使用 `use_preset`，避免累加使用次数。
- `persona_briefs(ids, *, user_id, is_admin) -> {id: {name, avatar, available}}`
  - 去重后用 `asyncio.gather` 并发查询；查询异常时 `available=False`。
  - 用户接口按任务创建人的身份计算；管理员接口按管理员身份计算，只用于显示名称。
- `service.create_task(..., actor)` / `update_task(..., actor)`：签名新增 `actor: TokenPayload`。仅当 `persona_preset_id` 非空时才校验。路由把 `ScheduledTaskPersonaError` 映射为 400。

### 2.2 执行（`runner._execute` 重写）

按 `chat_stream` 的顺序执行（`chat.py:398-441`）：

1. 解析创建人 owner（§6.3）。
2. `request = AgentRequest(message=task.prompt, persona_preset_id=..., agent_options={"model_id": task.model_id} if task.model_id else {})`。
3. `await resolve_persona_request(request, owner)`。`NotFoundError` / `AuthorizationError` → `NonRetryableRunError("所选专家已被删除、下架或你已无权使用，请编辑任务更换专家")`。
4. `await validate_agent_model_access(request.agent_options, owner)`。`model_disabled` / `model_not_allowed` → `NonRetryableRunError`，沿用 `_friendly_error` 的中文文案。
5. `apply_dify_kb_dataset_ids_to_agent_options(request.agent_options, persona_snapshot=request.persona_snapshot)`。没有专家时也要调用，与聊天一致。
6. 计算 `agent_id = resolve_persona_agent_id(normalize(task.agent_id), snapshot.preferred_agent_id)`，结果只可能是 fast 或 search。
7. 如果 snapshot 有 `skill_names`，则 `enabled_skills = list(snapshot.skill_names)`，否则为 None。对齐前端 `useAgent.ts` 与 `personaRequestConfig.ts` 的行为。
8. 当 agent 支持沙箱且 `SANDBOX_PLATFORM == "opensandbox"` 时，调用 `get_session_sandbox_manager().admit(owner.sub)`。容量不足时为普通 FAILED，文案「沙箱资源暂时不足」，可重试。
9. 组装会话 metadata：
   - 基础部分：`build_conversation_config(run_id=agent_run_id, agent_id, request, language=owner.metadata.language or "zh")`。这一步补齐了 `persona_avatar`、`enabled_skills` 等字段，保证前端续聊时能还原。
   - 叠加定时任务字段：`source`、`scheduled_task_id`、`scheduled_task_run_id`、`scheduled_task_name`、`hidden_from_conversation_list=True`、`unattended_run_id=agent_run_id`（§3.3）。
10. 调用 `submit(..., unattended=True)`（§3.1）。

### 2.3 前端

- **`TaskEditorDialog.tsx`**：
  - 在第 1 步下方新增一行「执行专家（可选）」，包含头像、名称、「更换 / 清除」。
  - 选择时打开 `PersonaPresetSelector`，用法参照 `ChatInputSelectors.tsx:159`，数据来自 `usePersonaPresets`。`onUsePreset` 只记录所选专家，不调用 `/use`。
  - 用户没有 `persona_preset:read` 权限时隐藏该行。
  - 保存时提交 `persona_preset_id`，清除时提交 null。
- **任务卡片**：用 `PersonaAvatarWithLoading` 显示专家头像和名称；`persona_available=false` 时显示徽标「专家已不可用」。
- **类型与文案**：更新 `types/scheduledTask.ts`；在 i18n 的 zh/en 中新增 `scheduledTask.persona.*`。ja/ko/ru 沿用一期做法，回落到英文。

## 3. R2 定时运行标记与工具注入

### 3.1 按次运行的标记

- **`src/infra/logging/context.py`**：
  - `RequestContext` 新增 `unattended: bool = False`，`TraceContext` 新增 ContextVar `_unattended`，默认 False。
  - `set_request_context(..., unattended: bool | None = None)`：传 None 表示不修改。这样 `agents/core/base.py:359` 再次调用该方法时不会把标记覆盖掉。
- **传递链**：
  - local 路径：`TaskManager.submit(..., unattended=False)` → `TaskExecutor.run_task(..., unattended)`。
  - arq 路径：`submit_arq(..., unattended=False)` 写入 `payload["unattended"]`，`arq_worker.run_agent_task` 取出后传给 `run_task`。
  - `run_task` 每次都显式调用 `set_request_context(..., unattended=bool(unattended))`，防止复制的上下文带出上一次运行的值。
- **其他入口都不传，即为 False**：`chat_stream`、企业微信、启动恢复、排队派发（`concurrency.py:615`）。所以用户在定时会话里续聊时，这个标记天然是 False。
- **标记只存在于运行时**：不放进 `agent_options`，也不放进前端会回传的 metadata 字段。原因是 `restoreAgentOptions` 会把 `agent_options` 原样带回续聊请求。
- **读取方式**：fast / search / team 的 nodes 调用 `TraceContext.get_request_context().unattended`，据此决定是否挂载 `ScheduledRunMiddleware`（§3.2）。nodes 运行在 `run_task` 的协程里，复制出的上下文同样能读到这个标记。执行器（`_execute_agent_stream`、企业微信的 executor）的签名都不用改。

### 3.2 定时运行中间件（R2.2 / R2.3 / R2.4）

沿用现成做法：团队模式的 `TeamToolExclusionMiddleware`（`team_agent/tool_exclusion.py`）在模型请求层按名称过滤工具，主代理和子代理都挂了它（`team_agent/nodes.py:407,600`）；拦截工具调用、直接返回 `ToolMessage` 的写法参照 `MCPQuotaMiddleware.awrap_tool_call`（`tool_interception.py:232`）。

新增 `ScheduledRunMiddleware`（`src/agents/core/scheduled_run.py`），只在定时运行时挂载。
- **排除名单** `SCHEDULED_RUN_EXCLUDED_TOOLS = {"ask_human", "update_sop", "list_scheduled_tasks", "draft_scheduled_task"}`。后两个是 R4 的聊天工具，放进名单是为了防止定时任务在运行中再去创建定时任务。
- **`awrap_model_call` / `wrap_model_call`**：`request.override(tools=[名单外的工具])`，模型看不到这些工具。
- **`awrap_tool_call`**：模型仍然调用了名单内的工具时（比如子代理提示词 `subagent_prompts.py:37` 提到了 ask_human），不执行工具（不调 handler，因此不会创建审批、也不会卡住），直接返回 `ToolMessage`：「定时任务自动执行中，用户不在场，不能提问或请求确认。请按保守方式继续，把需要用户确认的事项写在回答末尾。」
- **挂载位置**：在 fast / search / team 的 nodes 中，读取 `TraceContext.get_request_context().unattended`。为 True 时：
  - 把中间件同时加进主代理的 `user_middleware` 和子代理的 `subagent_middleware`，位置与 `TeamToolExclusionMiddleware` 一致（`PromptCachingMiddleware` 之后）；
  - 把定时说明 `SCHEDULED_RUN_PROMPT` 加进 `_prompt_sections` 和 `subagent_prompt_sections`，由现有的 `SectionPromptMiddleware` 注入。
- **定时说明内容**：「这是定时任务自动执行，用户不在场，不能向用户提问；不执行不可逆或对外有副作用的操作；需要用户确认的事项，在回答末尾以「需要你确认的事项」列出。」系统提示词每轮重建，这段话不会进入 checkpoint，也不会出现在消息里。
- **不改**：`ask_human` 工具本身、审批流程、工具组装代码（`fast_agent/context.py`、`search_agent/context.py`）、`update_sop` 的追加逻辑。续聊运行不挂这个中间件，一切照旧。
- **结果预览**：沿用一期的 `_load_preview`。回答末尾的「需要你确认的事项」会体现在预览和会话里。

### 3.3 中断不自动恢复（R2.5）

- `task/startup_cleanup.py` 会扫描三类候选会话：RUNNING、PENDING/QUEUED、可恢复的 FAILED。遇到 `metadata.unattended_run_id == run_id` 时：
  - 标记为 FAILED，并设置 `task_recoverable=False`、`task_error_code="unattended_interrupted"`；
  - 不调用 `_resume_interrupted_run`。
- 后续处理：
  - 如果 runner 的监控还活在其他副本上，它会读到 FAILED，然后按重试策略处理；
  - 如果监控已经随进程退出，由现有的过期清理收尾（`mark_stale_runs_failed`，阈值 3 小时）。
- 续聊产生的 run_id 与 `unattended_run_id` 不同，按普通会话恢复。

### 3.4 通知去重（R2.6）

- `TaskExecutor.run_task`：`unattended=True` 时不调用 `_send_task_notification`，也就不再发出 `task:complete`（`executor.py:397-430`）。

## 4. R3 续聊与清理

- **续聊**：沿用现有链路。thread_id 等于 session_id；metadata 在 §2.2 补齐后，前端会还原 agent、专家、`agent_options` 和 `enabled_skills`。
- **取消隐藏**：
  - `chat_stream` 读到 `existing_metadata.hidden_from_conversation_list is True` 时，调用 `_update_session_config(..., extra={"hidden_from_conversation_list": False})`。
  - 为此 `build_conversation_config` 新增可选参数 `extra: dict | None`，在返回前合并进结果。
  - 前端：首次续聊后侧栏要能出现该会话。实现时先核实新会话出现在侧栏的刷新路径，必要时在续聊发送成功后触发一次会话列表刷新。
- **清理**：不改代码。
  - `list_inactive_session_ids`（`session/storage.py:721`）本来就包含定时会话，加测试锁定这一点。
  - 清理之后，只读体验（输入框禁用、禁止分叉）沿用现有逻辑。
  - 上线清单见 `implement.md`。

## 5. R4 聊天工具（卡片确认，D8）

### 5.1 工具（新增 `src/infra/tool/scheduled_task_tool.py`）

- **`list_scheduled_tasks()`**：只读。
  - 返回 `{entity_type: "scheduled_task_list", now, total, limit, items: [{id, name, schedule_text, status, enabled, next_run_at, last_run_status, last_run_at, persona_name, paused_by}]}`。
  - 工具描述中写明：「创建或修改请在网页端对话中进行」，供企业微信场景引导用户。
- **`draft_scheduled_task(action, task_id?, task_name?, name?, prompt?, schedule?: ScheduleSpec, persona_name?, clear_persona?, enabled?)`**：只生成草稿，不改任务。
  - 用 `service.validate` / `preview` 计算 `schedule_text` 和接下来 5 次执行时间。
  - create：预检配额；`persona_name` 为空时，默认取当前会话 metadata 中的 `persona_preset_id`（session_id 从 runtime context 获取）。
  - update：先用 `task_id` 定位，否则按 `task_name` 先精确匹配、再包含匹配；匹配到多个时返回候选，由模型追问。`enabled` 用于暂停或恢复。
  - 按名称找专家：`PersonaPresetManager.list_presets(user_id, is_admin, q=name)`，同名唯一时选中，否则返回候选；选中后再走 `ensure_persona_usable` 校验。
  - 返回 `{entity_type: "scheduled_task_draft", draft_id, action, summary: {name, prompt, schedule_text, next_runs[5], persona: {id, name, avatar}, enabled}, before?: {...}, now, message: "草稿已生成，请用户在卡片上点击确认后生效"}`。
- **两个工具的共同要求**：
  - 解析当前用户（包含 permissions），要求具备 `scheduled_task:use`。
  - 所有分支都用 `try/except` 包住。`ScheduleValidationError`、`ScheduledTaskQuotaError`、`ScheduledTaskPersonaError` 原样返回中文 `{"error": ...}`；其他异常返回「操作失败，请稍后重试或到定时任务页面操作」。
  - 工具绝不抛出异常，避免 `MCPToolWithRetry` 重试（`mcp_client.py:27,62`）。

### 5.2 注册与排除

- `build_internal_tools()`（`internal_registry.py:35`）：在 `SCHEDULED_TASK_ENABLED and SCHEDULED_TASK_CHAT_TOOL_ENABLED` 时追加两个工具。按角色限制或配额沿用 /mcp 页面上的内置工具策略。
- 定时运行：这两个工具已经在 `ScheduledRunMiddleware` 的排除名单里（§3.2）：模型看不到，误调用也会被拦截。这样可以防止定时任务自己去建定时任务。工具内部会再检查一次标记，作为兜底。
- 团队：在 `TEAM_ROUTER_EXCLUDED_TOOLS`（`team_agent/context.py:11`）中加入这两个工具名。该裁剪同时覆盖团队成员子代理。
- 企业微信：`handler.py` 提交任务时传 `disabled_tools=["draft_scheduled_task"]`，只保留查询工具。
- 工具描述：在 `harness_prompt_overrides.py` 的 `_DEFAULT_TOOLS` 和 `_DEFAULT_FIELDS` 中登记两个工具，包括嵌套字段 `kind/time/weekdays/month_day/run_at/every_hours` 的中文说明。不登记的话，这些说明会被 `_compact_schema` 清空。

### 5.3 草稿与确认

- **集合 `scheduled_task_drafts`**，字段：
  - 标识与归属：`_id`、`owner_id`、`session_id`；
  - 内容：`action`、`task_id?`、`payload`（Create 或 Update 的 dict）、`base_updated_at?`、`summary`、`before?`；
  - 状态：`status`（pending / confirming / confirmed / cancelled / failed）、`error?`、`result_task_id?`；
  - 时间：`created_at`、`expires_at`（创建后 30 分钟）、`purge_at`（创建后 30 天，TTL）。
- **REST**，按创建人隔离，需要 `scheduled_task:use`：
  - `GET /api/scheduled-tasks/drafts/{id}` 返回状态。pending 且已过 `expires_at` 时计算为 `expired`。
  - `POST /api/scheduled-tasks/drafts/{id}/confirm`：
    1. 原子执行 pending → confirming（条件：owner 匹配且未过期）。
    2. create 调用 `create_task(actor, created_via="chat")`；update 先比对 `base_updated_at`，不一致返回 409「任务已在别处修改，请让助手重新生成草稿」，一致再调用 `update_task(actor)`。
    3. 成功后置为 confirmed，并写入 `result_task_id`。
    4. 校验、配额或专家错误：置为 failed 并记录 `error`，返回 400。
    5. 未知异常：回到 pending，返回 500，附中文提示。
    6. 重复点击确认时，状态已是 confirming，返回 409。
  - `POST /api/scheduled-tasks/drafts/{id}/cancel`：pending → cancelled。
- **前端**：
  - 新增 `chat/ChatMessage/items/ScheduledTaskDraftCard.tsx` 和 `scheduledTaskDraftResult.ts`，在 `MessagePartRenderer.tsx` 中为 `draft_scheduled_task` 增加分支。
  - 卡片首次挂载时拉取草稿状态，展示摘要；update 草稿展示改动前后对照。
  - pending 状态显示「确认 / 取消」按钮。确认成功后 toast，派发 `scheduled-task:refresh`，并提供「查看定时任务」链接。
  - 其余状态显示徽标；GET 返回 404 时（例如分享页）只显示摘要、不显示按钮。
  - `list_scheduled_tasks` 使用通用的工具结果展示。
  - 事件常量 `SCHEDULED_TASK_REFRESH_EVENT` 移到公共模块，避免聊天代码引用面板文件。

### 5.4 未采用的方案

B「对话确认」（Redis 草稿 + `confirm_scheduled_task` 工具 + run_id 门禁）没有采用。原因是这种方案由模型判断用户是否同意，模型可能误读，也可能被注入的指令诱导。以后如果企业微信要支持创建，可以在 A 的基础上为企业微信单独补一条确认路径；卡片接口保持不变。

## 6. R5 管控

### 6.1 权限

- `kernel/types.py` 新增：`SCHEDULED_TASK_USE = "scheduled_task:use"`、`SCHEDULED_TASK_ADMIN = "scheduled_task:admin"`。
- `kernel/schemas/permission.py` 新增「定时任务」分组，两项标签分别为「使用定时任务」「管理所有定时任务」。
- `auth/rbac.py`：默认 `user` 角色加入 use 权限，只对新安装的环境生效。admin 是 `is_system` 角色，启动时会自动获得全部权限。
- 前端 `types/auth.ts` 同步更新 `Permission` 枚举。
- 存量角色不补授予（D4）。
- 用户路由的依赖从 `chat:write` 改为 `scheduled_task:use`。

### 6.2 开关端到端

- **后端**：`ScheduledTaskDisabledError` 统一返回 400「定时任务功能已被管理员关闭」。
  - 拦截：create、update、恢复（`set_enabled(True)`）、`run_now`、草稿确认。
  - 放行：list、get、delete、pause、runs。
  - `load_all` 的关闭分支补上 `self._marker = None`，修复「开→关→开」后任务不重新注册的问题。
- **前端**：
  - `SettingsContext` 新增 `enableScheduledTasks = getBooleanSetting("SCHEDULED_TASK_ENABLED")`。
  - `SessionSidebar.tsx:118-123` 改为 `show: enableScheduledTasks && hasPermission(SCHEDULED_TASK_USE)`。
  - `App.tsx` 中 `/scheduled-tasks` 路由加权限守卫。
  - 面板在开关关闭时显示横幅，并禁用新建按钮。

### 6.3 运行时复核创建人

- `_resolve_owner` 在用户不存在时返回 None。`_execute` 依次校验：
  - 用户不存在，或 `is_active=False`；
  - 缺少 `scheduled_task:use` 或 `chat:write`。
- 任一项不满足都抛出 `NonRetryableRunError("任务创建人账号已停用或已没有定时任务权限，任务已暂停")`。

### 6.4 管理员总览

- **后端路由**：新增 `src/api/routes/scheduled_task_admin.py`，前缀 `/api/admin/scheduled-tasks`，依赖 `scheduled_task:admin`，在 `main.py` 注册。

  | 接口 | 参数与行为 |
  |---|---|
  | `GET ""` | 参数 `skip`、`limit`（≤100）、`q`（任务名，正则转义后不区分大小写）、`owner`（用户名，解析成 id）、`status`、`last_run_status`；返回 `{items: [Response + owner_id + owner_name], total, skip, limit}`，沿用 `opensandbox_admin` 的分页约定 |
  | `POST /{id}/pause` | body `{reason: 1..200}`；写入 `status=paused, enabled=False, paused_by="admin", pause_reason="管理员已暂停：{reason}"` |
  | `POST /{id}/resume` | 恢复任务 |
  | `DELETE /{id}` | 删除任务 |
  | `GET /{id}/runs` | 运行记录，`skip` / `limit` 分页 |

- **storage**：新增 `list_tasks_admin(filters, skip, limit)`；`delete_task` 和 `list_runs` 的 `owner_id` 改为可选参数。
- **service**：新增 `admin_pause`、`admin_resume`、`admin_delete`。每次操作打一条结构化 `logger.info`，内容为 actor、action、task、owner、reason，作为最小留痕。
- **用户侧限制**：任务的 `paused_by == "admin"` 时，用户恢复或立即执行都返回 403「该任务已被管理员暂停（原因：…），如需恢复请联系管理员」。
- **前端**：
  - 新增 `panels/ScheduledTaskAdminPanel/*`、路由 `/admin/scheduled-tasks`，以及 `UserMenu` 管理组入口（需要 admin 权限）。
  - 表格列：任务名、创建人、频率、专家、状态、最近结果、下次执行、操作。
  - 支持筛选与分页；暂停时必须填写原因；删除前二次确认。
  - 运行记录复用 `TaskRunsDrawer`，通过 fetcher prop 区分用户接口和管理员接口。

### 6.5 全局并发与排队

- **新增设置**：`SCHEDULED_TASK_MAX_CONCURRENT_RUNS`（默认 10，范围 1..100）、`SCHEDULED_TASK_QUEUE_TIMEOUT_MINUTES`（默认 20，范围 1..120）。
- **`locks.py`**：
  - `acquire_run_slot(member, limit, ttl)`：基于 ZSET `scheduler:running`，用一段 Lua 依次执行 `ZREMRANGEBYSCORE(-inf, now)`、检查 `ZCARD < limit`、再 `ZADD now+ttl member`，保证原子性。
  - `release_run_slot(member)`：执行 `ZREM`。
  - 进程崩溃时，名额到期后自动回收。
- **`runner._monitor`**：
  - 每次尝试之前都要先拿到名额：每 5 秒加抖动轮询一次，最多等待 `queue_timeout`。名额 ttl 为 `timeout_seconds + 120`，在 finally 中释放。
  - 等待超时：本次记为 SKIPPED，文案「系统繁忙，本次执行已跳过」，不再重试。
  - 执行超时从提交之后开始计时，排队时间不计入。
- **任务锁 TTL**：改为 `(timeout + queue_timeout) × attempts + 120`。
- **`record_task_result`**：
  - SUCCESS：`consecutive_failures` 清零；
  - FAILED / TIMEOUT：`consecutive_failures + 1`；
  - SKIPPED：不改变 `consecutive_failures`；
  - 所有状态都会更新 `last_run_*` 和 `total_runs`。

### 6.6 设置项

在 `_definitions_extra.py` 中：
- 新增三项：`SCHEDULED_TASK_CHAT_TOOL_ENABLED`（bool，默认 True）和 §6.5 的两项。
- 已有两项补充取值范围：`MAX_PER_USER` 为 1..100，`MIN_INTERVAL_HOURS` 为 1..24。
- `base.py` 增加对应默认值；zh/en 补充 `settingDesc` 文案。

## 7. R6 统计

不改代码。在 `.trellis/spec/backend/scheduled-tasks.md` 和 `analytics-persona-and-lists.md` 中写明口径：定时会话按普通会话计入；统计查询不得过滤定时会话（不能按 `hidden_from_conversation_list` 或 `source` 过滤）；重试产生的会话和消息同样计入。

## 8. 兼容、上线与回滚

- **兼容**：
  - `agent_id` 只在入参处收窄；新字段都有默认值；旧任务 `paused_by=None` 时用户可以自行恢复。
  - 已部署环境的普通角色没有 use 权限，上线时需要授予给试点角色。admin 不受影响。
- **开关**：`SCHEDULED_TASK_ENABLED` 控制整个功能；`SCHEDULED_TASK_CHAT_TOOL_ENABLED` 只控制聊天工具。
- **回滚**：按阶段 revert。`scheduled_task_drafts` 集合残留无害，可保留。

## 9. 风险

| 风险 | 缓解 |
|---|---|
| 定时运行标记漏传，定时运行仍然注入了 ask_human 并卡住等待 | 对 local 与 arq 两条路径做参数化测试；`run_task` 每次显式设置标记；为启动恢复路径写单测 |
| 子代理按提示词去调用 ask_human | `ScheduledRunMiddleware` 同时挂在子代理上，`awrap_tool_call` 拦截后直接返回提示，不会创建审批 |
| 模型在定时运行时误执行危险操作 | 系统提示词约束模型保守执行；高风险工具的权限仍按角色控制 |
| 全局名额泄漏 | 名额按 TTL 自动回收；在 finally 中释放 |
| 卡片确认被绕过 | 确认只能通过 REST，并且需要登录用户本人；模型没有确认工具 |
| 取消隐藏后，侧栏出现「[定时] …」会话 | 属于预期行为（D2） |
| 专家每次执行都会累加 `usage_count` | 已接受（D3） |
