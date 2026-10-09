# R3 调研：聊天中查询 / 创建 / 修改定时任务（智能体工具）

## 结论摘要

1. 内置工具链路清晰：新增 `src/infra/tool/scheduled_task_tool.py`，在 `build_internal_tools()` 注册即可同时覆盖 fast / search / team 三种智能体。管理员可在 /mcp 内置服务器页面按角色禁用、限额，无需新代码。
2. 确认方案推荐 **D：两阶段工具（先预览、后提交）+ 对话内确认**，不是 A/B/C 中任何一个。理由：A 依赖模型自觉且 team / 企业微信下不可用；B 有提示词注入后静默持久化的风险；C 工作量最大且企业微信无法渲染。
3. 工具能拿到 user_id、session_id，拿不到 persona_preset_id。`persona_preset_id` 要从会话 metadata 读取（已验证落库）。
4. 防递归只靠「按次运行的无人值守标记」：在 context.setup() 里剔除工具，并在工具体内再检查一次。
5. 前端：工具结果渲染按工具名定制，有成熟先例，做卡片约 1.5 人日。因为面板在 Tab 切换时才挂载，聊天中创建后不需要实时刷新。

## 现状（证据）

**工具装配（已验证）**
- `internal_registry.py:35 build_internal_tools()` 按 settings 开关追加工具（如 `:47 ENABLE_DOCUMENT_PARSE`），`:73-74` 追加 persona/team 工具。
- `get_internal_tools_for_user()`（`:189`）按 `MCPToolPolicy`（角色白名单、禁用、配额）过滤，再用 `MCPToolWithRetry` 包装。fast 和 search 在 `fast_agent/context.py:188`、`search_agent/context.py:205` 调用它；`TeamAgentContext(FastAgentContext)` 继承，并通过 `TEAM_ROUTER_EXCLUDED_TOOLS`（`team_agent/context.py:11`）剔除管理类工具。
- 工具过滤：`tool_filter.py:15 BUILTIN_TOOLS`（ask_human 等永不被禁用）；用户级 `disabled_tools` 精确匹配。专家（persona）没有工具白名单，只有 `skill_names` 和 `preferred_agent_id`（`schemas/persona_preset.py:104-106`），因此专家会话里同样会有此工具。
- 延迟加载只作用于 MCP 工具（`fast_agent/context.py:128`），内置工具始终在提示词里，并计入 `DEFERRED_TOOL_THRESHOLD`。
- 工具描述覆盖：`harness_prompt_overrides.py` 的 `_DEFAULT_TOOLS` / `_DEFAULT_FIELDS`（`:53`、`:85`）。`localize_tool_for_model` 只改写名字在 `schema_fields` 中的工具，并且 `_compact_schema` 会删掉所有 `description` / `title`。**探针已验证**：嵌套的 ScheduleSpec 字段（kind、time 等）描述会被清空，除非在该工具的 fields 映射中列出同名字段（该映射对任意嵌套层级生效）。
- 取用户/会话：所有 agent 都把 `context` 放入内层 `configurable`（`fast_agent/nodes.py:265`，search `:332`，team `:634`），工具通过 `get_user_id_from_runtime(runtime)`（`backend_utils.py:16`）读取。`context.session_id` 也可读。另有 `TraceContext.get_request_context()`（`agents/core/base.py:359`、`task/executor.py:121` 写入 session_id、run_id、user_id），ask_human 即用此法（`human_tool/tool.py:151`）。
- 专家：`persona_preset_id` 只在会话 metadata 中（`chat.py:239`、定时任务 `runner.py:273`）。`_execute_agent_stream`（`chat.py:~250`）的参数里没有它。本地后端 `_update_session_config` 在 submit 之后才写（`chat.py:628`），首轮理论上有竞态，实际 LLM 延迟远大于写入。

**参考工具（已验证）**
- `persona_preset_tool.py` / `team_tool.py`：`@tool` + `runtime: Annotated[ToolRuntime, InjectedToolArg]`，`_resolve_user` 现查权限，所有分支返回 `json.dumps(..., ensure_ascii=False)`，经 `run_blocking_io`。
- 缺陷：错误是英文，且 `f"Failed to ...: {e}"` 会泄露原始异常。`memory_retain`（`memory/tools.py:186`）同样返回英文。新工具必须自行捕获所有异常并返回中文。
- 原因：`MCPToolWithRetry._arun`（`mcp_client.py:~175`）遇到异常会按文本里的 timeout/connection 等关键词重试最多 3 次（`:27`）。写操作若抛异常会被重试，造成重复创建。单次调用上限 300 秒（`:29`）。
- 结果协议：`detect_tool_error`（`tool_outputs.py:116`）把带 `error` 键的字典判为失败；JSON 字符串会被解析成字典发给前端（`tool_events.py:132-142`），上限 8000 字符。

**前端（已验证）**
- 渲染按工具名分派：`MessagePartRenderer.tsx:91-217` 对 reveal_file、reveal_project、edit_file 等写了 `if (part.name === ...)`，其余走通用 `ToolCallItem`（JSON 折叠面板）。
- persona 工具有「结果驱动刷新」先例：`personaPresetToolResult.ts` + `McpBlockPreview.tsx:276`。**注意**：前端要求 `entity_type: "persona_preset"`，但后端 `persona_preset_tool.py` 不返回该字段（`grep entity_type src` 为空），这个联动实际是失效的。
- 面板刷新：`ScheduledTaskPanel/index.tsx:28,59` 已监听 `scheduled-task:refresh`，`useWebSocketNotifications.tsx:~52` 在运行结束时派发。`TabContent.tsx:104-106` 说明非当前 Tab 不渲染面板，切到面板时会自动 `load()`。
- ApprovalPanel（`ApprovalPanel.tsx`）：字段类型 text/textarea/number/checkbox/select/multi_select；message 用 ReactMarkdown 渲染（支持 GFM）；按钮固定为「提交」「取消」（i18n `approvals.submit/cancel`），不可自定义；显示在输入框上方（`ChatView.tsx:531`），不在消息流内，带倒计时。
- `useApprovals({sessionId: null})`（`ChatAppContent.tsx:81`）拉取 `/human/pending`，不按会话过滤（见跨课题发现）。
- 时间：普通聊天消息带前缀 `[User message sent at: 2026-10-09 14:05:39 +08:00 Asia/Shanghai]`（`chat.py:391`、`user_message_timestamp.py:33`，前端 `session.ts:199` 传 user_timezone）。企业微信 handler 未见该前缀（grep 无命中，**推断**模型不知道当前时间）。

## 缺口

- 无任何定时任务工具；无「复述确认」机制；persona 默认值无法从 runtime 直取。
- 企业微信 handler 无审批 UI，ask_human 在那里会一直等到超时（`wecom/handler.py` 无 approval 相关代码）。
- 工具描述要登记到 `harness_prompt_overrides.py`，否则嵌套字段说明丢失。

## 推荐方案

**工具（4 个，不含删除）**：`list_scheduled_tasks`、`preview_scheduled_task`（创建/修改的第一阶段）、`save_scheduled_task`（第二阶段提交）、`set_scheduled_task_enabled`（暂停/恢复）。分开注册是为了让管理员能给「只读」和「写入」分别配角色策略。

**两阶段契约**
- `preview_scheduled_task(task_id|task_name?, name, prompt, schedule: ScheduleSpec, expert_name?, timezone="Asia/Shanghai")`：只校验、不落库。调用 `ScheduledTaskService.preview()`（`service.py:69`）得到 `schedule_text` 和接下来 5 次时间。返回 `{entity_type:"scheduled_task", action:"preview_create|preview_update", draft_id, summary:{name, schedule_text, next_runs[5], prompt, expert, timezone}, now, message:"请向用户复述并等待确认"}`。draft 写入 Redis（owner、session_id、issuing run_id、payload，TTL 15 分钟）。
- `save_scheduled_task(draft_id)`：校验 owner 匹配，且**当前 run_id ≠ draft 的 run_id**（保证中间经过了一轮用户消息，防止同一轮内自问自答）。通过后调用 `create_task` / `update_task`（`service.py:77`、`:105`），一次性消费 draft。返回 `{entity_type, action:"created|updated", task:to_response(...), schedule_text, next_runs}`。
- 幂等：draft 消费用 `GETDEL`，重试不会重复创建。
- 全部分支 `try/except`，捕获 `ScheduleValidationError`、`ScheduledTaskQuotaError`（均为中文信息，直接透传），其他异常返回「操作失败，请稍后重试或到定时任务页面操作」。失败结果带 `now`（含时区），让模型自纠错。

**参数与定位**
- schedule 复用 `ScheduleSpec`（`schemas/scheduled_task.py:48`），在 `_DEFAULT_FIELDS` 登记 `kind/time/weekdays/month_day/run_at/every_hours` 描述。保持嵌套（`PersonaStarterPrompt` 已有先例），工具内用 pydantic 构造 `ScheduledTaskCreate/Update`。
- 修改按 `task_id` 优先，其次 `task_name`：对 `storage.list_tasks(owner)`（`storage.py:86`，上限 20 条）做精确匹配，再做包含匹配；多于一个则返回候选列表（id + 名称）让模型追问。`update_task(task_id, owner_id, ...)` 本身带 owner 隔离。
- 时区默认 `Asia/Shanghai`（`schemas:16`），模型一般不用填。

**专家**
- 默认值：`SessionManager().get_session(context.session_id).metadata["persona_preset_id"]`（`session/manager.py:80`）。只在新建且用户未指定时使用，并在预览的 `summary.expert` 中显示，由用户确认。
- 按名称解析：`PersonaPresetManager.list_presets(user_id, is_admin, q=name, status="published")`（`manager.py:218`）。精确同名唯一则取之，否则返回候选。校验应复用 R1 将提供的 service 级校验，使 REST 和聊天共用。

**可用性与防递归**
- 注册条件：`settings.SCHEDULED_TASK_ENABLED and settings.SCHEDULED_TASK_CHAT_TOOL_ENABLED`（新增管理员开关）。工具体内再用 `_resolve_user` 检查 `chat:write`（与 REST 的 `scheduled_task.py:29` 一致）。
- 无人值守：假设 R2 新增按次标记 `FastAgentContext.unattended`（来源于本次运行的 kwargs，**不能**写入会话 metadata，否则用户继续对话时会误判）。在 `FastAgentContext.setup()`（fast 与 team 共用）和 `SearchAgentContext.setup()` 中，于 `get_internal_tools_for_user` 之后按名称剔除这 4 个工具（照搬 `TEAM_ROUTER_EXCLUDED_TOOLS` 做法）；工具体内读 `runtime.config["configurable"]["context"].unattended` 兜底拒绝。
- team：默认加入 `TEAM_ROUTER_EXCLUDED_TOOLS`（路由者不该改任务；ask_human 也被同样排除）。

**前端**
- 新增 `items/ScheduledTaskToolItem.tsx` + `scheduledTaskToolResult.ts`，在 `MessagePartRenderer` 增加 `scheduled_task` 工具分支（4 个名称）。预览态显示「待确认」卡片，已保存态显示名称、频率、下 5 次、「查看定时任务」按钮（`navigate('/scheduled-tasks')`）。保存成功时派发 `SCHEDULED_TASK_REFRESH_EVENT`（事件常量需提到公共模块，避免聊天代码引用面板文件）。
- 卡片无确认按钮，确认靠用户在输入框回复。i18n：locales 有 zh/en/ja/ko/ru 五种，但 `"scheduledTask"` 键只在 zh、en 命中（grep 已验证），ja/ko/ru 靠 `defaultValue` 兜底；新卡片沿用该做法，或一并补齐。
- 同时在 `_DEFAULT_TOOLS` 登记 4 个工具的中文描述。

**改动文件**：`tool/scheduled_task_tool.py`（新）、`internal_registry.py`、`fast_agent/context.py`、`search_agent/context.py`、`team_agent/context.py`、`harness_prompt_overrides.py`、`config/base.py` + `_definitions_extra.py`（开关）、`service.py`（persona 校验，由 R1 提供）、前端 3-4 个文件。

## 备选方案与权衡

| 方案 | 证据 | 体验 | 工作量 |
|---|---|---|---|
| A 模型调 ask_human | 面板在输入框上方，Markdown 可用，按钮「提交/取消」 | 会阻塞本轮；超时 300 秒；team 和企业微信不可用；模型可能不调用 | 0.5 人日，但不可强制 |
| A' 工具内部发审批 | 工具被 `MCPToolWithRetry` 包装，300 秒超时会触发最多 3 次重试，反复弹窗 | 差 | 不可取 |
| B 直接创建 + 撤销 | service 已有 pause、delete | 最顺滑；但被注入的文档可让模型静默建立持久任务，错误时间会真实运行并消耗模型额度 | 最低，风险最高 |
| C 草稿 + 卡片按钮调 REST | 需新增草稿存储、确认端点、卡片状态 | 强保证（人点击而非模型），但企业微信无法渲染，历史回放需状态 | 后端 1.5 + 前端 2 人日 |
| **D 两阶段 + 对话确认（推荐）** | 无需新 UI 即可用；run_id 门禁 | 多一轮对话；任意渠道可用 | 后端 1.5 + 前端 1.5 人日 |

C 可以作为 D 的升级：卡片按钮直接调用 draft 确认 REST，`save_scheduled_task` 保留作为对话渠道的兜底。

## 风险

- 提示词注入：D 的 run_id 门禁只保证「隔了一轮」，不保证用户真的同意。管理员关闭开关或按角色禁用是兜底手段；高敏场景可升级到 C。
- `run_id` 来源依赖 `TraceContext`，arq worker 下需用测试确认（`executor.py:121` 看起来已设置，**推断**）。
- 首轮 persona 读取竞态（见上）：读不到则不带默认专家，预览里会显示「默认助手」。
- 内置工具数量增加会占用提示词并推高延迟加载阈值，4 个工具约增加几百 token。
- 配额检查与创建非原子（`service.py:81`），并发下可能略超限，一期已有，不在此处解决。
- 子代理（`general-purpose`）与主代理共享工具，因此也能调用；可接受。

## 测试建议

- 工具层：参照 `tests/infra/tool/test_persona_preset_tool.py` 的 `_Runtime` 假对象，monkeypatch service 与 Redis。覆盖：owner 隔离、无 user 返回中文错误、配额和校验错误透传、过去时间的 once、同轮 save 被拒、draft 过期、draft 只能消费一次、名称多义返回候选、专家解析（唯一、多义、不可见）、异常不外泄。
- 注册与过滤：仿 `tests/test_mcp_tool_policies.py:258-275` 测开关；仿 `tests/agents/test_team_context_sandbox_tools.py` 测 unattended 和 team 的剔除；测 `localize_tool_for_model` 后嵌套字段仍有中文描述。
- 前端：`node:test` 单测 `scheduledTaskToolResult.ts` 解析。
- 端到端：用假 LLM 脚本化「preview → 下一轮 save」，对真实 service + `tests/infra/scheduler/conftest.py` 的假存储，断言任务落库、已注册到 RuntimeScheduler、仅 owner 可见。

## 需要产品决策的问题

1. 确认方式：推荐 D（对话内两阶段确认）。理由：全渠道可用、无阻塞、成本小，并为后续升级 C 留口。
2. 工具是否对企业微信会话开放：推荐开放。D 不依赖 UI；风险是企业微信缺少时间戳，所以工具结果带 `now`。
3. team 智能体是否提供：推荐不提供。路由者职责不含此项。
4. 工具是否允许「暂停/恢复」：推荐允许（可逆），不提供删除。
5. 管理员管控项：推荐新增 `SCHEDULED_TASK_CHAT_TOOL_ENABLED`；每用户上限继续沿用 `SCHEDULED_TASK_MAX_PER_USER`；按角色限制走 MCP 工具策略。

## 跨课题发现

- R2：`useApprovals({sessionId: null})` 与 `/human/pending` 不按会话过滤（`ChatAppContent.tsx:81`），是 ask_human 串界面的根源。
- R2：定时运行的 session metadata 在 submit 之后才写入（`runner.py:~290` 先 submit 后 `update_session_metadata`），无人值守标记不能靠 metadata 判断。
- R2：`TEAM_ROUTER_EXCLUDED_TOOLS` 的做法可复用为无人值守剔除工具的模板。
- 既有缺陷：persona 工具结果缺 `entity_type`，前端联动失效；三处重复的 `_resolve_user`。
- R1：`ScheduledTaskCreate.persona_preset_id` 目前无存在性或可见性校验，建议放到 service 层。
