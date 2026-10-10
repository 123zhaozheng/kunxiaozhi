# Implement Plan — 定时任务二期

实现顺序按依赖关系排列：P1 的管控基础被 P2–P6 依赖，P5 依赖 P2 和 P3。
每个阶段完成后单独提交一次，提交点就是回滚点。

## P0 准备

- [x] Q1 = A（卡片确认，D8），已同步到 `prd.md` / `design.md`。
- [x] 用户批准最终计划（D1–D8）后，执行 `task.py start`。
- [x] 工作区里遗留的统计 partial 修复（`src/infra/analytics/storage.py`、`tests/api/routes/test_tokens_by_model_hourly.py`）先单独提交，或另开分支提交，不要混进二期。
- [x] 在 `feat/scheduled-tasks` 上继续开发，执行 `task.py start`。

## P1 管控基础（R5.1 / R5.2 / R5.3 / R5.6 + 数据模型）

- [x] 权限
  - [x] `kernel/types.py` 新增两个权限；`schemas/permission.py` 新增「定时任务」分组。
  - [x] `auth/rbac.py` 的默认 user 角色加入 `scheduled_task:use`。
  - [x] 前端 `types/auth.ts` 同步更新。
- [x] 数据模型：`schemas/scheduled_task.py` 按 design §1 增加字段，并收窄 `agent_id`。
- [x] `NonRetryableRunError`：`_monitor` 捕获后不重试、立即暂停（`paused_by="system"`）并通知。
- [x] 运行时复核创建人：`is_active`、`scheduled_task:use`、`chat:write`（design §6.3）。
- [x] 开关端到端
  - [x] 后端：`ScheduledTaskDisabledError` → 400。
  - [x] `load_all` 关闭分支重置 `_marker`。
  - [x] 路由依赖改为 `scheduled_task:use`。
  - [x] 前端：`SettingsContext.enableScheduledTasks`、侧栏入口门控、路由守卫、面板横幅。
- [x] 设置项
  - [x] 新增 `SCHEDULED_TASK_CHAT_TOOL_ENABLED`、`MAX_CONCURRENT_RUNS`、`QUEUE_TIMEOUT_MINUTES`。
  - [x] 已有两项补上 min/max。
  - [x] `base.py` 默认值，以及 zh/en 的 `settingDesc`。
- [x] 测试
  - [x] 新建 `tests/api/test_scheduled_task_routes.py`，补上一期缺失的路由测试：CRUD、按 owner 隔离、无 use 权限返回 403、开关关闭时 400。
  - [x] 在 `tests/infra/scheduler/` 下补充：`load_all` 开→关→开、创建人停用或无权限、不可重试即暂停。

## P2 专家（R1）

- [x] 新增 `src/infra/scheduler/persona.py`：`ensure_persona_usable`、`persona_briefs`。
- [x] service 的 create/update 签名加 `actor` 并校验专家；路由把错误映射为 400；响应补齐专家字段。
- [x] 按 design §2.2 重写 `runner._execute`，步骤与 `chat_stream` 同序：
  - 解析专家 → 校验模型 → 注入知识库 → `agent_id` → 设置 `enabled_skills` → 沙箱准入 → 组装 metadata（`build_conversation_config` + 定时字段）。
- [x] `chat.py` 的 `build_conversation_config` 增加可选参数 `extra`，默认值下行为不变。
- [x] 前端
  - [x] 编辑器中的专家行（`PersonaPresetSelector`）。
  - [x] 保存时提交 `persona_preset_id`。
  - [x] 卡片显示专家头像、名称和「专家已不可用」徽标。
  - [x] 更新类型定义；zh/en i18n。
- [x] 测试
  - [x] `tests/infra/scheduler/conftest.py` 新增 FakePersonaStorage、FakeTaskManager、fake owner、FakeSessionManager。
  - [x] 新增 `test_persona_execution.py`：真实走 `resolve_persona_request`；覆盖 `enabled_skills`、知识库、`agent_id`、metadata 中的 `persona_avatar`；专家失效时不重试且立即暂停；创建或修改时选到不可见专家返回 400；清除专家（null）。
  - [x] 前端：`node:test` 校验编辑器保存的请求体。

## P3 定时运行标记与会话（R2 / R3）——高风险，涉及任务执行链路

- [x] `logging/context.py`：新增 ContextVar `unattended`（定时运行标记）；`set_request_context` 中该参数传 None 表示不修改。
- [x] 透传 `unattended`：`TaskManager.submit` / `submit_arq` → arq payload → `arq_worker.run_agent_task` → `TaskExecutor.run_task`，并在 `run_task` 里显式设置。
- [x] runner 提交时传 `unattended=True`，metadata 写入 `unattended_run_id`。
- [x] 新增 `src/agents/core/scheduled_run.py`：`ScheduledRunMiddleware`（参照 `TeamToolExclusionMiddleware`）+ 排除名单 + 定时说明 `SCHEDULED_RUN_PROMPT`。
  - 模型请求层过滤：`ask_human`、`update_sop`，以及 P5 的两个聊天工具。
  - `awrap_tool_call` 拦截误调用，返回提示，不执行工具。
- [x] fast / search / team 的 nodes：带定时运行标记时，主代理和子代理都挂上该中间件；定时说明加进提示段。不改 context.setup，也不改 `update_sop` 的追加逻辑。
- [x] `startup_cleanup`：`unattended_run_id == run_id` 时不自动恢复，直接标记失败。
- [x] `run_task`：定时运行时不发 `task:complete`。
- [x] 取消隐藏：`chat_stream` 读到 `existing_metadata.hidden_from_conversation_list` 后，通过 `extra` 取消隐藏；核实侧栏的刷新路径，必要时补一次刷新。
- [x] 测试
  - [x] `tests/infra/task/test_unattended_flag.py`：对 local 和 arq 参数化，验证标记能到达 TraceContext、续聊时为 False、标记不进入 `agent_options`。
  - [x] `tests/agents/test_scheduled_run_middleware.py`：
    - 模型请求中去掉了名单里的工具，名单外的工具保留；
    - 误调用名单内的工具时返回提示 ToolMessage，不调用 handler，也不创建审批；
    - nodes 只在带标记时挂载中间件和定时说明（fast / search / team，主代理和子代理都要覆盖）；不带标记时与现状一致。
  - [x] startup_cleanup 不恢复定时运行。
  - [x] `run_task` 定时运行时不发 `task:complete`。
  - [x] 取消隐藏只对 hidden 会话生效。
  - [x] `list_inactive_session_ids` 包含 `sch_*` 会话。
  - [x] 回归整个 `tests/infra/task` 和 `tests/agents` 目录。
- [x] **评审门**：本阶段完成后，派一个独立的 `trellis-check` 代理审查执行链路的改动，审查通过再进入 P4。

## P4 全局并发（R5.5）

- [x] `locks.py`：用 Lua 实现 `acquire_run_slot` / `release_run_slot`。
- [x] runner 每次尝试前先等待名额；排队超时记 SKIPPED；名额在 finally 中释放；任务锁 TTL 改为新公式。
- [x] `record_task_result`：SKIPPED 不改变 `consecutive_failures`。
- [x] 测试 `tests/infra/scheduler/test_run_slots.py`（fake redis）：达到上限时排队、超时跳过、正常释放、到期自动回收、SKIPPED 的计数语义。

## P5 聊天工具（R4，卡片确认 D8）

- [x] 新增 `src/infra/tool/scheduled_task_tool.py`：`list_scheduled_tasks`、`draft_scheduled_task`。全部分支捕获异常并返回中文。
- [x] 新建 `scheduled_task_drafts` 集合及其存储；草稿接口 GET / confirm / cancel（原子状态迁移、`base_updated_at` 冲突返回 409）。
- [x] 注册与排除
  - [x] 在 `build_internal_tools` 中按开关注册。
  - [x] 定时运行：由 `ScheduledRunMiddleware` 的排除名单过滤（P3 已包含这两个工具名），工具内部再兜底检查一次。
  - [x] 加入 `TEAM_ROUTER_EXCLUDED_TOOLS`。
  - [x] 企业微信提交时通过 `disabled_tools` 排除草稿工具。
- [x] `harness_prompt_overrides.py` 登记两个工具和嵌套字段的说明。
- [x] 前端
  - [x] `ScheduledTaskDraftCard` 及其结果解析；接入 `MessagePartRenderer`。
  - [x] 确认后刷新定时任务面板。
  - [x] 事件常量移到公共模块。
  - [x] 「聊天创建」徽标。
- [x] 测试
  - [x] `tests/infra/tool/test_scheduled_task_tool.py`：按 owner 隔离；无权限；校验和配额错误原样透传；异常不外泄；名称歧义返回候选；专家解析；默认专家。
  - [x] `tests/api/test_scheduled_task_draft_routes.py`：确认后才创建；过期、重复确认、他人确认、并发修改冲突。
  - [x] 剔除逻辑：定时运行、team、开关关闭。
  - [x] `localize_tool_for_model` 处理后，嵌套字段仍保留中文说明。
  - [x] 前端：卡片结果解析的 `node:test`。

## P6 管理员总览（R5.4）

- [x] storage
  - [x] 新增 `list_tasks_admin`。
  - [x] `delete_task` / `list_runs` 的 owner 改为可选。
  - [x] 新建索引。
- [x] service：`admin_pause` / `admin_resume` / `admin_delete`，带结构化日志。
  - [x] 用户侧拒绝恢复、立即执行被管理员暂停的任务。
- [x] 路由：新建 `routes/scheduled_task_admin.py`，在 `main.py` 注册。
- [x] 前端：`ScheduledTaskAdminPanel`、路由、UserMenu 入口；暂停原因弹窗；`TaskRunsDrawer` 支持传入 fetcher。
- [x] 测试 `tests/api/test_scheduled_task_admin_routes.py`
  - [x] 只有 use 权限时返回 403。
  - [x] 筛选与分页。
  - [x] 跨用户暂停、恢复、删除。
  - [x] 暂停原因必填。
  - [x] 用户恢复管理员暂停的任务返回 403。

## P7 收尾

- [x] 更新 spec
  - [x] `.trellis/spec/backend/scheduled-tasks.md`：专家执行、定时运行标记（不注入 ask_human/SOP）、草稿确认、权限与开关、并发名额、统计口径。
  - [x] `.trellis/spec/backend/analytics-persona-and-lists.md` 补充 R6 口径。
  - [x] `.trellis/spec/frontend/scheduled-task-panel.md`。
- [x] 全量验证（见下）；最后派一个全新的 `trellis-check` 代理做整体审查。
- [ ] 提交；用 `add_session.py` 记录日志。

## 验证命令（PowerShell，仓库根目录）

```powershell
# 后端定向
.venv\Scripts\python.exe -m pytest tests/infra/scheduler tests/infra/test_scheduler_runtime.py tests/api/test_scheduled_task_routes.py tests/api/test_scheduled_task_draft_routes.py tests/api/test_scheduled_task_admin_routes.py tests/infra/tool/test_scheduled_task_tool.py tests/agents/test_scheduled_run_middleware.py tests/infra/task/test_unattended_flag.py -q -p no:cacheprovider
# 受影响模块回归
.venv\Scripts\python.exe -m pytest tests/infra/task tests/agents tests/infra/tool tests/infra/session -q -p no:cacheprovider
# 统计不受影响（R6）
.venv\Scripts\python.exe -m pytest tests/infra/analytics tests/api/routes -k "analytics or tokens or usage or snapshot" --ignore=tests/api/routes/test_analytics_cross_consistency.py -q -p no:cacheprovider
# 静态检查（只检查改动文件）
.venv\Scripts\python.exe -m ruff check <changed .py files>
# 前端（frontend 目录）
.\node_modules\.bin\tsc.CMD --noEmit -p tsconfig.json
.\node_modules\.bin\eslint.CMD <changed ts/tsx files>
.\node_modules\.bin\tsx.CMD --test <new/changed *.test.ts>
```

## 手测清单

1. 创建一个带专家的任务，点「立即执行」：会话使用该专家的提示词、技能和知识库；卡片显示该专家。
2. 在任务提示词里诱导模型向用户提问：运行不卡住，聊天界面不弹审批，回答末尾列出「需要你确认的事项」。
3. 从运行记录进入会话续聊：上下文连续；发送后会话出现在侧栏。
4. 管理员关闭开关：入口消失，接口返回 400；重新开启后任务恢复调度。
5. 管理员暂停某用户的任务：用户端显示原因，无法恢复。
6. 在网页聊天里说「每个工作日 8:30 帮我整理今日重点」：出现草稿卡片，点确认后定时任务页能看到该任务，并带「聊天创建」标记。

## 上线清单

1. 给试点角色勾选「使用定时任务」，admin 自动拥有两个新权限。
2. 开启 `CHECKPOINT_CLEANUP_ENABLED`，按「每日定时运行数 × 保留天数」估算积压量，据此调大每批数量或缩短间隔。
3. 按副本数和 arq `max_jobs` 核对 `SCHEDULED_TASK_MAX_CONCURRENT_RUNS`，默认 10。
4. 如果想先只开放网页端，关闭 `SCHEDULED_TASK_CHAT_TOOL_ENABLED`。
5. 合并顺序：统计修复 → 一期与二期一起合入 main。

## 回滚点

P1–P6 每个阶段一个提交，可以逐段 revert。
- P3 涉及任务执行链路：`unattended` 参数默认 False，revert 后不残留任何状态。
- P5 新建的 `scheduled_task_drafts` 集合可以保留，不影响回滚。
