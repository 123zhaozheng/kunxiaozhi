# TeamAgent 丝滑 harness：SOP DAG 规划与可视化（v1）

## Goal

团队模式下，主代理对复杂任务主动调用 `update_sop` 工具建立 SOP DAG（步骤 + 依赖 + 角色 + 状态），前端渲染为直观的 DAG 图，用户确认后主代理按图逐步执行、实时更新节点状态，最终交付。DAG 对主代理而言是一个清晰的 CoT 工作流，对用户而言是可见、可确认、可干预的任务全貌。

背景：昨日（08-03）一版 TeamAgent harness 实现（确定性「一成员一步」计划 + 审批 + handoff 契约 + 平铺列表 UI）因体验不佳被整体撤回（`eca662a2` 撤回 `9c99d480`）。v1 吸取教训：**不搞执行引擎/强制守卫/结构化上下文注入，DAG 工具化（复用 `write_todos` 心智 + `ask_human` 审批模式），分波次落地**。

## 已确认的设计决策（grill 结论）

- 触发：团队模式（`agent_id="team"` + `team_id`）下，主代理对**复杂任务主动**调用 `update_sop` 建 DAG；简单问题（无分派步骤）不建/跳过。
- 执行模型：**纯 agent 驱动**——无外部执行引擎、无依赖守卫、无独立调度器；执行顺序靠主代理按 DAG 状态 + prompt 约束自行把控。
- 确认门禁：DAG 含分派步骤时，`update_sop` 创建计划后**工具内阻塞**等用户确认（复用 `create_approval` + `wait_for_response`，同 `ask_human` 模式）；简单问题跳过门禁。
- 子代理上下文：**不强制结构化注入**——主代理知晓当前状态与前驱节点内容，自然撰写 `task` 描述；prompt 软引导「在任务描述里带上本步要求与前驱关键输出」。
- 用户 UI：v1 只读 DAG + 确认执行 + 重新规划（带反馈）；步骤编辑留 v2。
- 交付：一个任务两波次（后端 → 前端），每波独立验证。

## Requirements

### R1 团队模式与 DAG 工具入口
- 团队模式下，主代理对复杂任务主动调用 `update_sop` 工具建立 SOP DAG；工具是主代理可见的「计划 + 进度」载体（类似 `write_todos` 但含依赖/角色/状态）。
- 简单问题：主代理可直接回答，不建 DAG、不触发确认。
- 非团队模式完全不变。

### R2 `update_sop` 工具与 SOP 数据模型
- 工具：`update_sop`（全量替换语义，同 `write_todos`），入参为完整 SOP（或「直接回答」声明）。
- SOP 字段：`plan_id`、`goal`、`summary`、`steps[]`（`step_id/title/description/dependencies[]/assignee/stage/expected_output/acceptance_criteria/status/attempts/output/error`）、`status`（draft/awaiting_confirmation/running/completed/failed/cancelled/rejected）、`user_feedback`。
- `assignee` 用 `build_team_member_subagent_type(member)`（`src/agents/team_agent/prompt.py:158-166`）产生的稳定 subagent_type，保证 DAG 节点 ↔ persona 子代理一一对应。
- 工具调用时做**确定性校验**：步骤数边界、依赖引用存在且无环、`assignee` ∈ 团队花名册、`expected_output` 非空。校验失败 → 返回结构化错误，主代理修正后重调（不引入外部修复循环）。

### R3 步骤粒度管控
- 参数进配置：`TEAM_SOP_MAX_STEPS`（默认 8，上限 12）、`TEAM_SOP_MIN_STEPS`（默认 2）、`TEAM_SOP_MODE`（默认 false，总开关）。
- 工具 schema + system prompt 硬约束：每步单一职责、产出可验证交付物、依赖只引用已存在 step_id、无依赖且独立步骤可并行、不造伪并行、过粗/过细自查。
- 超 `max_steps` → 工具拒绝并提示合并；低于 `min_steps` 且无分派价值 → 提示直接回答。
- 用户在 UI 看到 DAG 粒度不合适 → 「重新规划」带反馈，主代理重写。

### R4 确认门禁
- 含任何分派步骤的 SOP：`update_sop` 创建计划后**阻塞**等待用户确认（`create_approval(approval_type="sop_plan")` + `wait_for_response`）；确认前不 dispatch 任何子代理。
- 确认后：后续 `update_sop` 调用仅更新状态（running/succeeded/failed 等），不重复阻塞。
- 拒绝带反馈：`wait_for_response` 返回 rejected + feedback → 工具返回决策，主代理据此重新规划或向用户说明。
- 超时（默认 300s，可延长）：返回 timed_out → 主代理提示计划已过期，询问重新规划或取消。
- 重复审批响应幂等（防双击/重连 400）。

### R5 状态存储与事件
- SOP 状态持久化到 MongoDB `sop_runs`（应用层 store，独立于内层 message checkpointer），支撑确认暂停、历史回放、跨会话恢复。
- 事件（走 `present_team_event` 白名单 + `emit_team_event` 双写）：
  - `sop:updated` —— 全量快照（steps 全字段 + 状态），前端据此整体替换渲染（同 `todo:updated` 语义）；
  - `approval_required`（`approval_type="sop_plan"`）—— 通知前端弹确认；
  - 复用 `sop:plan_generating`（可选，规划中提示）。
- 历史回放：按 `sop:updated` 事件重建 DAG 卡片与最终状态；plan 事件不进消息正文路径。

### R6 DAG 可视化（前端）
- 团队模式下 SOP 生成后渲染 DAG 卡片（消息 part）：节点 = 步骤（标题 + 角色 + 状态着色），边 = 依赖关系。
- 状态着色：pending 灰 / running 主题色 / succeeded 绿 / failed 红 / cancelled 灰。
- 交互：只读（禁拖拽/连线）；缩放/平移/fitView；卡片头进度条（x/N）；「确认执行 / 重新规划（带反馈）」按钮；点击节点可看详情（v1 可选，简单 tooltip 即可）。
- 技术：复用 `@xyflow/react`（已装）+ 新增 `@dagrejs/dagre` 布局；布局前端算一次写回，`sop:updated` 快照只更新节点 data。
- 移动端可用（React Flow v10+ 原生触控）。

### R7 子代理任务描述（软引导，不强制）
- 主代理 dispatch 子代理时，prompt 引导其在 `task` 描述里包含：本步要求（objective/expected_output）+ 前驱节点关键输出摘要 + 相关 DAG 上下文。
- 不引入结构化 handoff schema、不强制注入机制、不提供 `get_sop_context` 工具（v1）。
- 大产物场景：主代理可提示子代理通过现有 `read_file`/`upload_url_to_sandbox` 等工具按需读取沙箱产物。

### R8 团队模式沙箱工具补齐（upload_url_to_sandbox）
- 现状缺陷：`TeamAgentContext`（`src/agents/team_agent/context.py`）未注册 `upload_url_to_sandbox`，但 `team_agent/prompt.py:71-84` 沙箱提示词却指导模型使用它——模型会被引导调用不存在的工具。SearchAgentContext 已在 `setup()` 注册（`search_agent/context.py:229-234`）。
- 修复：`TeamAgentContext.setup()` override，`super().setup()` 后 `settings.ENABLE_SANDBOX` 时 append `get_upload_url_tool()`。

### R9 兼容性与回滚
- `TEAM_SOP_MODE` 默认关闭，关闭时团队模式完全走旧路由；开启才暴露 `update_sop` 工具与门禁。
- 非团队模式与简单问题路径不变；新增 schema/事件向后兼容。
- 波次 1（后端）完成后可独立验证/回滚；波次 2（前端）依赖事件契约（已在本文档固定）。

## Acceptance Criteria

- [x] 团队模式 + 复杂问题：主代理调用 `update_sop` 产出 DAG → 前端渲染 DAG 卡片（节点/依赖边/状态）→ 含分派步骤时出现确认按钮且确认前零子代理 dispatch → 确认后逐步执行、节点状态实时更新 → 完成交付。
- [x] 简单问题：不建 DAG、不弹确认，直接回答。
- [x] 粒度参数生效：超 `max_steps` 被工具拒绝并提示；`assignee`/依赖/环等非法输入被确定性校验拦截并返回错误让主代理修正。
- [x] 拒绝带反馈 → 主代理重新规划或说明；超时 → 提示过期；重复审批响应幂等。
- [x] 前端 DAG 卡片：状态着色/进度条/确认/重规划按钮/只读；历史回放能重建 DAG。
- [x] `TeamAgentContext` 沙箱模式下注册 `upload_url_to_sandbox`（与 SearchAgentContext 对齐）；非沙箱不注册。
- [x] `TEAM_SOP_MODE` 关闭时团队模式行为与现状完全一致。
- [x] 后端测试：工具校验/门禁（零 dispatch、幂等、拒绝/超时）、事件契约、store 持久化；前端测试：normalize/reduce、DAG 组件渲染与状态更新、确认/重规划交互。

### Final verification note (2026-08-10)

Acceptance is covered by focused backend/frontend tests, broad agent/API regression,
type/lint/build checks, and a browser-mounted real `SopBlock` fixture at desktop and
390px container widths. The fixture verified 3 nodes/3 edges, fit-view framing,
no horizontal overflow, and replan feedback payloads. A deployment-level smoke with
real model, MongoDB, Redis, and SSE remains an operational rollout check rather than
an unimplemented product contract; `TEAM_SOP_MODE` remains default-off.

## Notes（v2 展望，本任务不做）

- 用户 UI 编辑 DAG（增删改步骤/拖依赖）。
- 阶段分组折叠（>12 步）、层级分解。
- 执行守卫（依赖顺序强制）、`get_sop_context` 按需拉取、结构化 handoff。
- 独立执行引擎/并行调度（若 agent 驱动出现乱序问题再评估）。
- 附件物化进沙箱（昨日 `materialize_attachments` 思路，可从 `git show 9c99d480` 取回）。
- 企微渠道不渲染 DAG（保持现状）；团队构建器（TeamBuilder）不在本任务范围。
