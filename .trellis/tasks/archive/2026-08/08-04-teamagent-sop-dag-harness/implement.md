# TeamAgent 丝滑 harness：SOP DAG 规划与可视化 —— 执行计划（v1，两波次）

> 原则（吸取昨日教训）：波次独立验证、功能开关保护、一次只让一个写代理碰一组重叠文件、事件契约先固定再并行。

## 波次 0：Harness 修复（write_todos 泄漏 + 主代理瘦身 + SOP 强制）

> 独立小提交，先于波次 1/2 既有内容。修复自测发现的三个 harness 问题。

### 0.1 write_todos 泄漏修复（profile 级根除）
- [x] `src/agents/team_agent/harness_profile.py`（新）：定义 `_TeamHarnessProfile`，`excluded_middleware=frozenset({TodoListMiddleware, ShortTodoListMiddleware})`，`extra_middleware` 只含 `HarnessLocalizationMiddleware`（不含 ShortTodoList）；`register_harness_profile` 注册到 Team 专用键。
- [x] `src/agents/team_agent/nodes.py`：deep agent 构建前激活 Team profile（model 级覆盖 compact_zh 的加回）；验证 `_harness_profile_for_model` 合并后 `excluded_middleware` 含两个类、`extra_middleware` 无 ShortTodoList。
- [x] `src/agents/team_agent/tool_exclusion.py`：保留作为子代理兜底，更新 docstring 说明主代理已 profile 级根除、此处仅子代理链路保险。
- [x] 测试 `tests/agents/test_team_harness_profile.py`：注册后解析的 profile 排除两个类、extra 无 ShortTodoList；含/不含 Team profile 时 write_todos 是否注入（mock model call 收集 system_message 断言无 `## \`write_todos\``）。

### 0.2 主代理提示词瘦身
- [x] `src/agents/team_agent/nodes.py`：团队模式下主代理 prompt sections 移除 `FILE_REVEAL_GUIDE`/`FILE_WORKSPACE_GUIDE`/`TOOL_DISCOVERY_GUIDE`，保留 `SAFETY_AND_VERIFICATION_GUIDE`+`SUBAGENT_TASK_GUIDE`。
- [x] `src/agents/team_agent/prompt.py`：`TEAM_ROUTER_SYSTEM_PROMPT` 增路由约束——不直接读用户上传文档/不自己做文件交付，一律指派子代理。
- [x] 测试 `tests/agents/test_team_router_prompt.py`：团队模式 system prompt 不含 reveal_file 指引、含路由约束文本。

### 0.3 SOP 节点前强制 update
- [x] `src/agents/team_agent/sop/prompt_section.py`：文案改硬约束措辞（"执行节点前必须先 update_sop 置 running"）。
- [x] `src/agents/team_agent/sop/guard.py`（新）：`SopDispatchGuardMiddleware`，`awrap_tool_call` 拦截 `task`，已确认计划存在且目标步骤未 running/succeeded → 注入 ToolMessage 提醒。
- [x] `src/agents/team_agent/nodes.py`：TEAM_SOP_MODE 开启时把 guard 挂进主代理 user_middleware。
- [x] 测试 `tests/agents/test_team_agent_sop_tool_hook.py`（既有，补充 guard 分支）：dispatch 时未置 running → 收到提醒 ToolMessage；已置 running → 无提醒。

### 波次 0 验收
- `pytest tests/agents/test_team_harness_profile.py tests/agents/test_team_router_prompt.py tests/agents/test_team_agent_sop_tool_hook.py` 全绿。
- `ruff check` + `mypy` 变更模块。
- 手动：Team 模式 system prompt 中 `## \`write_todos\`` 段消失；主代理不再引导 reveal_file；dispatch 子代理前未 update → 收到提醒。

## 波次 1：后端（先决修复 + SOP 工具 + 门禁 + 事件）

### 1.0 沙箱工具补齐（独立小提交）
- [x] `src/agents/team_agent/context.py`：`TeamAgentContext.setup()` override，`super().setup()` 后 `settings.ENABLE_SANDBOX` 时 append `get_upload_url_tool()`（仿 `search_agent/context.py:229-234`）。
- [x] 测试：`tests/agents/test_team_context_sandbox_tools.py`（沙箱开/关 → 工具含/不含 `upload_url_to_sandbox`；既有工具加载不破坏）。

### 1.1 SOP schema + 校验
- [x] `src/agents/team_agent/sop/schemas.py`：`StepStatus/SOPStep/SOPPlan` + `validate_sop_plan()`（步数边界、依赖引用、DFS 环、assignee ∈ 花名册、expected_output 非空，返回结构化错误列表）。
- [x] 测试：`tests/agents/test_sop_schemas.py`。

### 1.2 SOP 存储
- [x] `src/agents/team_agent/sop/store.py`：`SopRunStore`（MongoDB `sop_runs`）：`upsert_plan/get_plan/set_status/set_step_status/set_feedback`，返回完整快照。
- [x] 测试：`tests/infra/test_sop_store.py`（或 `tests/agents/test_sop_store.py`，mock motor collection）。

### 1.3 `update_sop` 工具（含阻塞确认门禁）
- [x] `src/agents/team_agent/sop/tool.py`：`update_sop` StructuredTool——
  - 校验失败 → 返回 `{success:false, errors}`，不落库不发事件；
  - 无分派步骤/`direct_answer` → 落库 + `sop:updated`，不阻塞；
  - 含分派步骤且未确认 → 落库（awaiting_confirmation）+ `create_approval("sop_plan")` + `sop:updated` + `approval_required` + `wait_for_response` 阻塞；approved→running 返回 `{approved:true}`；rejected+feedback→rejected 存反馈返回 `{approved:false, feedback}`；timeout→返回 `{timed_out:true}`；
  - 已确认后的调用 → 只更新状态/输出，不阻塞。
- [x] 测试：`tests/agents/test_sop_tool_gate.py`（零 dispatch、确认前状态、拒绝/超时、幂等、已确认后不阻塞）。

### 1.4 事件 + 审批幂等
- [x] `src/infra/writer/presenter_events.py`：`present_team_event` 白名单加 `"sop:updated"`、`"sop:plan_generating"`；`present.py` 复用 `emit_team_event` 双写。
- [x] `src/api/routes/human.py`：`respond_to_approval` 对 `type=="sop_plan"` 且已处理 → 幂等返回既有决策（参考 `git show 9c99d480:src/api/routes/human.py` diff）。
- [x] 测试：`tests/api/test_sop_approval_idempotent.py`、事件白名单/双写测试。

### 1.5 节点挂接 + 主代理提示
- [x] `src/agents/team_agent/nodes.py`：`TEAM_SOP_MODE` 开启时把 `update_sop` 加入主代理 `filtered_tools`；其余构建不动。
- [x] `src/agents/team_agent/sop/prompt_section.py`：主代理 SOP 使用引导段（何时建/步骤写法/执行/失败/收尾），legacy + compact_zh 双版本对齐 harness 本地化契约（`.trellis/spec/backend/agent-harness.md`）。
- [x] 测试：`tests/agents/test_sop_prompt_section.py`（两版本文本断言）+ 现有 `tests/agents` 回归（开关关闭路径不变）。

### 波次 1 验收
- `pytest tests/agents tests/api`（变更模块）全绿；`ruff check` + `mypy`（变更模块）。
- `TEAM_SOP_MODE=false` 时团队模式单测行为与现状一致。

## 波次 2：前端（DAG 卡片）

### 2.1 类型与布局
- [x] `pnpm add @dagrejs/dagre`。
- [x] `frontend/src/types/sop.ts`：SOP/SopNode/SopStatus 类型 + `normalizeSopEvent`/`reduceSop`（容忍后端别名，未知状态归一安全默认）。
- [x] `frontend/src/components/sop/sopLayout.ts`：dagre 纯函数 `getLayoutedElements(nodes, edges, "TB")`。
- [x] 测试：`frontend/src/types/__tests__/sop.test.ts`、`frontend/src/components/sop/__tests__/sopLayout.test.ts`。

### 2.2 组件
- [x] `SopNode.tsx`：自定义节点（状态着色 + Handle + tooltip），复刻 `MessageOutlinePanel.tsx` 模式（`--theme-primary`/dark mode）。
- [x] `SopFlow.tsx`：`ReactFlowProvider` + ReactFlow（Controls/MiniMap>8/Background/只读/`fitView`）。
- [x] `SopBlock.tsx`：卡片外壳（标题 + 进度条 x/N + 图例 + 「确认执行 / 重新规划（带反馈）」按钮 + 状态徽章）。
- [x] `hooks/useSopStatus.ts`：订阅 `sop:updated` 全量替换节点状态。
- [x] 测试：SopBlock/SopNode 组件测试（渲染/状态着色/确认/重规划交互/进度）。

### 2.3 消息管道接入
- [x] SOP 卡片采用 `ChatView` 单一全局渲染路径；删除未接审批回调的死 `MessagePartRenderer` 分支，避免双重布局与状态源。
- [x] `useAgent/eventHandlers.ts`：处理 `sop:updated`；`approval_required(approval_type="sop_plan")` → 走 SOP 卡片（不落入通用 ApprovalPanel）。
- [x] `useAgent/historyLoader.ts`：排除 `sop:*` 事件进消息正文；历史回放重建 DAG 卡片。
- [x] `useAgent/types.ts`：`EventType`/`EventData` 扩展。
- [x] 测试：`frontend/src/hooks/__tests__/useSopStatus.test.ts`、eventHandlers/historyLoader 相关测试。

## Final verification (2026-08-10)

- Backend/API regression: 426 passed; 4 unrelated pre-existing failures documented in `research/final-audit-tests.md`.
- Focused backend SOP verification: 65 passed after final check fixes.
- Frontend SOP verification: 38 passed; lint, TypeScript and production build passed.
- Browser fixture: desktop and 390px container render all 3 nodes/3 edges, fit view is complete, no horizontal overflow, and replan feedback emits the expected approval payload.
- Rollout safeguard: `TEAM_SOP_MODE` defaults off. Real model + MongoDB + Redis + SSE smoke remains a deployment check before enabling the flag.

### 波次 2 验收
- `pnpm lint`、`pnpm exec tsc -b`、`pnpm test`（targeted）、`pnpm build` 全绿。
- 手动冒烟：团队模式发复杂问题 → DAG 卡片渲染 → 确认 → 节点状态实时更新 → 完成交付；重新规划带反馈生效；历史回放重建。

## 验证命令
- 后端：`pytest tests/agents tests/api`（先跑变更模块）；`ruff check` + `mypy` 变更模块。
- 前端：`pnpm lint`、`pnpm exec tsc -b`、`pnpm test`、`pnpm build`。
- 全链路冒烟：起后端 + 前端，`TEAM_SOP_MODE=true`，团队模式走「建 DAG → 确认 → 执行 → 状态更新 → 交付」。

## Review Gates
- 波次 1 全部通过才进波次 2；波次 1 未验收不写前端代码。
- 每波次结束用 fresh `trellis-check` 全量复查（spec 合规 + lint/type/test + 跨层数据流）。
- 生产代码前，`update_sop` 门禁与事件契约先对照 `research/codebase-current-state.md` §3-4 的现有审批/事件行为评审。

## Rollback Points
- 波次 1 结束即可独立回滚：`TEAM_SOP_MODE=false` 恢复旧路由；`git revert` 仅涉及波次 1 提交。
- 波次 2 回滚保留波次 1（后端工具/门禁仍在，前端退回无卡片状态）。
