# TeamAgent 丝滑 harness：SOP DAG 规划与可视化 —— 技术设计（v1）

> 依据：`research/codebase-current-state.md`、`research/multi-agent-sop-orchestration.md`、`research/dag-visualization-frontend.md`。
> v1 设计原则：**DAG = 主代理的 CoT 工作流可视化**。复用 `write_todos`（工具+状态+事件+UI 块）与 `ask_human`（工具内阻塞审批）两种既有模式，不引入执行引擎/守卫/结构化注入。

## 1. 架构总览

```text
团队模式请求 (agent_id=team + team_id, TEAM_SOP_MODE=on)
  -> team_router_node 构建内层 deep agent（含 update_sop 工具 + ask_human 审批通道）
  -> 主代理对复杂任务调用 update_sop 建 DAG（全量替换语义）
       ├─ 工具内确定性校验（步数/依赖环/角色/expected_output）→ 失败返回错误让主代理修正
       └─ 计划含分派步骤 → create_approval("sop_plan") + emit sop:updated + approval_required
                              -> wait_for_response 阻塞（SSE 流保持，前端 DAG 卡片 + 确认按钮）
  -> 用户确认 → wait_for_response 返回 → 工具返回 {approved} → 主代理逐步执行
       每步：dispatch 对应 persona 子代理（task 描述软引导含本步要求+前驱输出）
             -> 完成后调 update_sop 更新节点状态（不阻塞）-> emit sop:updated 全量快照
  -> 全部完成 → 主代理综合交付；用户可随时「重新规划」（拒绝+反馈 → 主代理重写 DAG）
```

关键点：
- **无外部执行引擎**：主代理（deepagents 主循环）就是执行者，`update_sop` 只是它的计划/进度载体。
- **无守卫**：依赖顺序靠主代理按 DAG 状态自控（v1 接受 LLM 顺序执行，不追求强制并行）。
- **门禁内嵌工具**：`update_sop` 首次创建含分派步骤的计划时阻塞（同 `ask_human` 的 `create_approval` + `wait_for_response`），确认后后续调用不阻塞。
- **状态独立持久化**：`sop_runs`（MongoDB）支撑确认暂停/历史回放/恢复，独立于内层 message checkpointer。

## 2. 后端设计

### 2.0 沙箱工具补齐（先决修复，独立小提交）

- `src/agents/team_agent/context.py`：`TeamAgentContext.setup()` override——`await super().setup()` 后，`settings.ENABLE_SANDBOX` 时 `self.tools.append(get_upload_url_tool())`（仿 `search_agent/context.py:229-234`）。
- 测试：`tests/agents/test_team_context_sandbox_tools.py`——沙箱开/关时工具列表含/不含 `upload_url_to_sandbox`，且不破坏既有工具加载。

### 2.1 `src/agents/team_agent/sop/schemas.py`（新）—— SOP 数据模型

```python
class StepStatus(str, Enum):
    pending / running / succeeded / failed / cancelled

class SOPStep(BaseModel):
    step_id: str                    # "s1","s2"... 稳定标识
    title: str
    description: str = ""
    dependencies: list[str] = []    # 上游 step_id（前端建边）
    assignee: str                   # subagent_type（build_team_member_subagent_type 产出）
    stage: str | None = None        # 阶段分组（v1 仅展示，不做折叠）
    expected_output: str
    acceptance_criteria: list[str] = []
    status: StepStatus = StepStatus.pending
    attempts: int = 0
    output: str | None = None       # 步骤完成摘要（主代理回填）
    error: str | None = None

class SOPPlan(BaseModel):
    plan_id: str
    session_id: str
    team_id: str
    goal: str
    summary: str = ""
    steps: list[SOPStep]
    status: Literal["draft","awaiting_confirmation","running","completed","failed","cancelled","rejected"]
    user_feedback: str | None = None
    created_at / updated_at
```

- 校验函数 `validate_sop_plan(plan, roster_subagent_types, max_steps, min_steps)`：步数边界、依赖引用存在、DFS 环检测、`assignee` ∈ 花名册、`expected_output` 非空。返回结构化错误列表（供工具返回给主代理修正）。

### 2.2 `src/agents/team_agent/sop/store.py`（新）—— 持久化

- `SopRunStore`：MongoDB collection `sop_runs`，按 `(session_id, team_id)` 定位当前 plan；`upsert_plan / get_plan / set_status / set_step_status / set_feedback`。
- 每次更新返回完整 `SOPPlan` 快照 → 工具/事件用它发 `sop:updated`。
- 独立于内层 checkpointer（确认暂停跨进程/跨请求仍可恢复）。

### 2.3 `src/agents/team_agent/sop/tool.py`（新）—— `update_sop` 工具

- 形态：StructuredTool `update_sop`，入参 `UpdateSopInput{plan: SOPPlan | None, action: Literal["create","update"], direct_answer: bool = False}`（全量替换语义，同 `write_todos`）。
- 行为：
  1. `direct_answer=true` 或 `plan` 无分派步骤 → 存储 + 发 `sop:updated`（status=draft/completed），**不阻塞**，返回成功；
  2. 含分派步骤且该计划未确认 → 落库（status=awaiting_confirmation）+ `create_approval("sop_plan", fields=[{name:"plan", value: plan_json}])` + `emit sop:updated` + `approval_required` → `wait_for_response(timeout=300)` **阻塞**：
     - approved → status=running，返回 `{approved: true}`；
     - rejected + feedback → status=rejected，存 `user_feedback`，返回 `{approved: false, feedback}`（主代理据此重新规划）；
     - timeout → status 保留 awaiting_confirmation，返回 `{timed_out: true}`（主代理提示过期）；
  3. 已确认（status=running）后的调用 → 只更新步骤状态/输出，发 `sop:updated`，**不阻塞**。
- 校验失败：返回 `{success: false, errors: [...]}`，不落库、不发事件，主代理修正后重调。
- 注册进主代理工具列表（`filtered_tools` 追加）；子代理**不**需要此工具（v1）。

### 2.4 `src/agents/team_agent/nodes.py`（改）—— 挂接

- `TEAM_SOP_MODE` 开启时：把 `update_sop` 工具加入主代理 `tools`；system prompt 追加「SOP 使用引导」（见 2.5）。
- 其余构建逻辑（角色子代理、middleware、事件处理）不动；`ask_human` 审批通道已由 `FastAgentContext` 提供（`get_human_tool`），`update_sop` 直接复用 `create_approval`/`wait_for_response`。
- 简单问题：主代理不调 `update_sop` 即可，无需代码分支。

### 2.5 `src/agents/team_agent/sop/prompt_section.py`（新）—— 主代理提示

- system prompt 追加段（legacy/compact_zh 双版本，对齐 `agent-harness.md` 的 harness 本地化契约）：
  - 何时用：复杂/多角色任务先 `update_sop` 建 DAG；简单问题直接答；
  - 步骤写法：单一职责、可验证产出、依赖显式、独立步骤可并行、步数 ∈ [min,max]、`assignee` 选最匹配角色；
  - 执行：确认后逐步完成，每步 dispatch 对应角色子代理，**任务描述里带上本步要求与前驱关键输出**；完成后 `update_sop` 更新节点状态；
  - 失败：步骤失败 → 更新状态 + 说明原因，必要时调整后续步骤；
  - 结束：全部完成后总结交付，`update_sop` 置 completed。

### 2.6 事件（`src/infra/writer/presenter_events.py` + `present.py` 改）

- `present_team_event` 白名单加入：`"sop:updated"`、`"sop:plan_generating"`（可选）；`"approval_required"` 已存在。
- `emit_team_event(event_type, data)` 双写（复用昨日实现思路，`git show 9c99d480:src/infra/writer/presenter_events.py`）。
- `sop:updated` payload：完整 `SOPPlan`（steps 全字段 + status）；前端全量替换渲染（同 `todo:updated`）。
- `approval_required`（`approval_type="sop_plan"`）payload：`id/message/type/plan_id/plan`。
- `src/api/routes/human.py`：`respond_to_approval` 对 `type=="sop_plan"` 且已处理 → 幂等返回既有决策（参考昨日 diff）。

## 3. 前端设计

### 3.1 组件（`frontend/src/components/sop/`，新）

```
types/sop.ts            # SOP/SopNode/SopStatus 类型 + normalizeSopEvent/reduceSop（容忍后端别名）
sopLayout.ts            # dagre 纯函数 getLayoutedElements(nodes, edges, "TB")
SopBlock.tsx            # 消息 part：卡片外壳（标题 + 进度条 x/N + 图例 + 确认/重规划按钮）
SopFlow.tsx             # <ReactFlowProvider> 内层 ReactFlow（Controls/MiniMap/Background/只读）
SopNode.tsx             # 自定义节点：状态着色 + Handle + tooltip（复刻 MessageOutlinePanel 模式）
hooks/useSopStatus.ts   # 订阅 sop:updated，全量替换 nodes data.status
```

- 完全复刻 `MessageOutlinePanel.tsx` 工程模式：`nodeTypes` 模块级、`--theme-primary` 主题变量、Dots Background、`Controls`、`minZoom 0.6 / maxZoom 2`、`proOptions.hideAttribution`、dark mode。
- 布局：dagre 算一次写回消息数据；`sop:updated` 只改 `data.status` 与进度条，坐标不动。
- 确认/重规划按钮：确认 → `POST /human/{approval_id}/respond?approved=true`；重规划 → `respond?approved=false&response={"feedback": "..."}`（带反馈输入框）。
- 新依赖：`@dagrejs/dagre@^3`（唯一新增）。

### 3.2 消息管道接入

- `MessagePartRenderer.tsx` 注册 `SopBlock` 为新 part（参考 `TodoBlock.tsx` 先例）。
- `useAgent/eventHandlers.ts`：`sop:updated` → 更新 SOP 卡片状态；`approval_required(approval_type="sop_plan")` → **不落入通用 ApprovalPanel**，由 SOP 卡片处理确认。
- `historyLoader.ts`：`canAttachEventTypeToPreviousAssistant` 排除 `sop:*`/team-plan 事件，历史回放重建 DAG 卡片。
- `types.ts`：`EventType`/`EventData` 加 `sop:updated`/`approval_required(sop_plan)` payload 字段。

## 4. 粒度管控参数（配置，`src/kernel/config`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `TEAM_SOP_MODE` | false | 功能总开关（false 团队模式走旧路由，不暴露工具） |
| `TEAM_SOP_MAX_STEPS` | 8（上限 12） | 超限工具拒绝并提示合并 |
| `TEAM_SOP_MIN_STEPS` | 2 | 低于提示直接回答 |

## 5. 里程碑（本任务两波次）

| 波次 | 内容 | 验证锚点 |
|---|---|---|
| **波次 1（后端）** | 2.0 沙箱工具补齐；2.1 schemas + 校验；2.2 store；2.3 update_sop 工具（含阻塞门禁）；2.4 nodes 挂接；2.5 prompt 段；2.6 事件 + 审批幂等；全部后端测试 | 单测：校验/门禁（零 dispatch、幂等、拒绝/超时）、事件、store；开关关闭团队模式行为不变 |
| **波次 2（前端）** | 3.1 组件 + 布局；3.2 消息管道接入；前端测试 | DAG 卡片渲染/状态着色/确认/重规划/历史重建；ESLint + tsc + build 通过 |

## 8. Harness 修复（对齐用户三要求，后端补丁波次）

> 起因：自测发现 `write_todos` 引导文案仍泄漏进 Team 主代理 system prompt；主代理 prompt 不够 harness；SOP 节点执行前未强制 update。

### 8.1 write_todos 泄漏根因（已实证）

- deepagents `create_deep_agent` 组装主代理栈顺序（`graph.py:712-760`）：`[TodoListMiddleware]` → 内层工具中间件 → `[user_middleware]` → `[profile.extra_middleware]` → 缓存。
- langchain 契约 "first in list as outermost"（`types.py:502`）：列表前者先执行。
- `persona.py:85-99` 注册的 compact_zh profile 把基类 `TodoListMiddleware` 剔进 `excluded_middleware`，**却又**经 `build_harness_extra_middleware()` 把子类 `ShortTodoListMiddleware` 加回 `extra_middleware`。子类继承 `awrap_model_call`，注入行为与基类一致——自相矛盾。
- `TeamToolExclusionMiddleware` 位于 user_middleware 层，执行**早于** `extra_middleware` 里的 `ShortTodoListMiddleware`：裁剪跑完时 write_todos 文案尚未注入 → 裁了个空。已实证 `_strip_system_message`/`_strip_write_todos_section` 函数本身逻辑正确（单独喂含 `` ## `write_todos` `` 的 SystemMessage 能正确删）。

### 8.2 修法：Team 模式注册独立 HarnessProfile（profile 级根除）

- 新增 `src/agents/team_agent/harness_profile.py`：定义并注册一个 Team 专属 HarnessProfile，键名 `"team"`（与现有 `anthropic`/`openai`/`google_genai` 同级，但仅在 Team Agent 节点构建时激活）。
- profile 内容：`excluded_middleware = frozenset({TodoListMiddleware, ShortTodoListMiddleware})`；`extra_middleware_factory = None`（不再挂回 ShortTodoList）；保留 `HarnessLocalizationMiddleware`（本地化仍需要）。
- 激活方式：`team_router_node` 构建 deep agent 前临时把该 profile 注册到当前模型的解析键；deepagents model 级 profile 优先于 provider 级合并，Team 的剔除覆盖 compact_zh 的加回。
- 时序问题从根上消失：deepagents 内层根本不注入 write_todos，无需事后裁剪。
- `tool_exclusion.py` 的 `TeamToolExclusionMiddleware` **保留**作为子代理链路兜底（子代理栈不经过主 agent profile，仍可能有注入风险）。

### 8.3 主代理提示词瘦身（纯路由 harness）

团队模式下主代理 `user_middleware` 的 prompt sections 只保留**路由职责**：
- 移除 `MAIN_AGENT_PROMPT_SECTIONS` 里的 `FILE_REVEAL_GUIDE`/`FILE_WORKSPACE_GUIDE`/`TOOL_DISCOVERY_GUIDE`（文件操作/交付/沙箱路由——这些是子代理的活）。
- 保留 `SAFETY_AND_VERIFICATION_GUIDE`（通用安全栅栏，主代理也需要）与 `SUBAGENT_TASK_GUIDE`（task 工具使用契约）。
- 附件/文档阅读显式约束：主代理不直接读用户上传文档，一律指派子代理（在 `TEAM_ROUTER_SYSTEM_PROMPT` 增一条路由约束）。

### 8.4 SOP 节点前强制 update（prompt + 运行时守卫）

- **Prompt（`sop/prompt_section.py`）**：把软引导改为硬约束措辞——"执行对应节点前，必须先调 `update_sop` 把该步骤 status 置 `running`；节点完成后置 `succeeded`/`failed` 并回填 output"。
- **运行时守卫（`SubagentActivityMiddleware` 子类 / team 专属中间件）**：`awrap_tool_call` 拦截 `task` 工具调用，若存在已确认（status=running）的 SOP 计划，且被 dispatch 的 `subagent_type` 对应的步骤状态不是 `running`/`succeeded` → 注入 ToolMessage 提醒"执行节点前请先 `update_sop` 置 running"，让主代理自我修正。不硬阻断（保持 agent 驱动），仅兜底提示。

## 6. 兼容性与回滚

- `TEAM_SOP_MODE` 默认关闭；开启才暴露 `update_sop` 工具与门禁，关闭即旧路由。
- 非团队模式完全不变；团队简单问题不建 DAG。
- schema/事件新增字段向后兼容；`sop:updated` 全量快照让前端无状态累积错误。
- 波次 1 独立提交可回滚；波次 2 依赖波次 1 事件契约（本文档已固定）。

## 7. 关键风险与对策

| 风险 | 对策 |
|---|---|
| 主代理乱序执行/漏步（无守卫） | v1 接受（agent 驱动本质）；prompt 引导 + DAG 状态可见；用户可「重新规划」；若实测乱序严重，v2 加轻量守卫（计划中） |
| LLM 建 DAG 有环/非法引用 | 工具内确定性校验 + 错误反馈（tool.py 2.3） |
| 步骤过多失焦 | `TEAM_SOP_MAX_STEPS` 硬校验 + prompt 自查 + UI 重新规划兜底 |
| 审批阻塞与 SSE 流 | 复用 `ask_human` 已验证的 `create_approval`+`wait_for_response` 模式；前端按 `approval_required(sop_plan)` 渲染卡片按钮 |
| 历史回放重复渲染 | plan 事件不进消息正文路径；`historyLoader` 排除 `sop:*` 并重建卡片 |
| 工具与 UI 状态不一致 | `sop:updated` 全量快照 + `sop_runs` 持久化，单数据源 |
