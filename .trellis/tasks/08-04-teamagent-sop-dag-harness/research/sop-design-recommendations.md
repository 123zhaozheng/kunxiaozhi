# SOP 设计推荐方案摘要（来自 multi-agent-sop-orchestration.md §6，完整报告见同目录同名文件）

> 本文是子代理可直接注入的浓缩版。完整论证/出处见 `research/multi-agent-sop-orchestration.md`。

## 总体架构（推荐）

**应用层自研 DAG 执行引擎 + DeepAgents 只做单步执行器**，不把 SOP 编译成 LangGraph 大图。理由：
1. SOP 是用户可确认/可编辑的数据（MongoDB `sop_runs`），应用层状态机可直接改/恢复；
2. 内层 deep agent 已占用 `checkpointer(thread_id=session_id)`（`nodes.py:540-550`），外层加 checkpoint 图有命名空间冲突（`graph.py:96-104` 注释）；
3. LangGraph superstep "all-or-nothing" 对 LLM 长任务过硬，与「失败→blocked 传播 + 部分成功保留」冲突；
4. 应用层能精确控制限流（Semaphore 防 429）、重试、取消、blocked/skipped 传播、审计；
5. 现有 `create_deep_agent` 构建 + `AgentEventProcessor` + SSE 可原样复用。

执行引擎形态：asyncio + `Semaphore(max_parallel)` + 就绪集（Kahn 入度）驱动；每步跑一个「单步 deep agent」，注入步骤指令 + 上游产物。
注意：DeepAgents 0.6.7 支持 `interrupt_on` 参数与 `AsyncSubAgent`，单步内 HITL 可优先用原生机制。

## SOP 数据模型（Pydantic）

```python
class StepStatus(str, Enum):
    pending / ready / running / succeeded / failed / blocked / skipped / cancelled

class SOPStep(BaseModel):
    id: str; title: str; description: str
    dependencies: list[str] = []      # 上游 step id（Kahn 建图）
    assignee: str                     # subagent_type（build_team_member_subagent_type 产出）
    expected_output: str              # 期望交付物（提示词+验收双用）
    acceptance_criteria: list[str] = []
    context_refs: list[str] = []      # 需读取的上游产物 key
    status: StepStatus = pending; attempts: int = 0; max_retries: int = 2
    output: str | None = None; artifact_refs: list[str] = []; error: str | None = None
    started_at / ended_at

class SOPPlan(BaseModel):             # 持久化 sop_runs
    id; session_id; team_id; goal; user_feedback: str | None
    steps: list[SOPStep]
    status: Literal["draft","awaiting_confirmation","running","succeeded","failed","cancelled"]
    max_parallel: int = 3; on_failure: Literal["stop","continue_best_effort"] = "stop"
    created_at / updated_at
```

`assignee` 直接复用 `build_team_member_subagent_type(member)`（`prompt.py:158-166`），保证 DAG 节点 ↔ persona 子代理一一对应。

## 规划器（Planner）

- 产出：`SOPPlan` JSON（`with_structured_output` / JSON mode + temperature≈0 + schema 注入 prompt）。
- 输入：用户问题 + 团队花名册（member_id/角色名/职责摘要/技能=能力 hint）+ 粒度参数 + 2-3 个 few-shot。
- System prompt 要点：每步单一职责、产出可验证交付物；`dependencies` 只引用已存在 step_id；无依赖且语义独立→并行、不造伪并行；`assignee` 来自花名册且职责匹配；步数 ∈ [min,max]，过粗/过细自查；纯 JSON 输出。
- 确定性校验层（不依赖 LLM）：Kahn 环检测；assignee ∈ roster；dependencies 引用存在；步数边界；expected_output 非空。失败→修复循环（错误喂回 LLM ≤2 轮）→串行 3 步兜底。

## 粒度控制参数（进配置）

| 参数 | 默认 | 说明 |
|---|---|---|
| max_steps | 8（上限 12） | 超限合并或拒绝 |
| min_steps | 2 | 低于回退单 agent |
| max_depth | 1 | 单层平铺 DAG（层级 v2） |
| max_parallel | 3 | 就绪集并发上限防 429 |
| step_max_retries | 2 | 指数退避+jitter（1s/2s/4s），仅瞬时错误重试 |

## 执行引擎（应用层自研）

1. 确认后 `POST /sop/{id}/confirm {action: approve|regenerate|edit_steps}`；approve → running，后台 asyncio 任务（复用 `_stream_tasks` 登记可取消）。
2. 调度循环：就绪集 = 依赖全部 succeeded；`Semaphore(max_parallel)` 并发；每步 `build_step_executor(step, artifacts, model, config)`（复用 `create_deep_agent` 构建逻辑，拼步骤指令+上游产物进 prompt）。
3. 失败：attempts+1 ≤ max_retries 且瞬时错误 → 退避重试；否则 failed → 下游 blocked；`on_failure=stop` 终止 run / `continue_best_effort` 跳过继续。
4. 取消/恢复：run 级 cancelled 经 asyncio 取消（`TaskInterruptedError` 通道）；DB 步骤状态驱动断点续跑（已 succeeded 不重跑）。
5. 产物传递：`artifacts: dict[step_id, {content|artifact_ref}]` 存 run 文档；下游注入直接依赖产物（截断 token，大产物给引用按需读取）。
6. 综合节点：全部结束后主 agent 汇总（Magentic-One/LLMCompiler Joiner 角色）。

## 事件契约（`sop:` 前缀，走 presenter/SSE 双写）

`sop:plan_generating`、`sop:plan_ready`（data=完整 SOPPlan，UI 渲染 DAG 进确认态）、`sop:plan_rejected`（带 feedback）、`sop:started`、`sop:step_started`、`sop:step_output`、`sop:step_succeeded`、`sop:step_retrying`、`sop:step_failed`、`sop:step_blocked`、`sop:step_cancelled`、`sop:progress`（节流 ≥1s）、`sop:completed`、`sop:failed`、`sop:cancelled`。

断线/历史回放：仿 Dify `include_state_snapshot`，恢复时补发快照事件重建节点状态；审批动作落审计日志。

## 前端 DAG 可视化

- `@xyflow/react`（已装 ^12.10.2，`MessageOutlinePanel.tsx` 生产先例）+ `@dagrejs/dagre`（唯一新依赖）。
- 布局前端算一次写回消息数据；执行期只改 `data.status` 不动坐标。
- 对话内默认只读；状态着色（pending 灰 / running 主题色脉冲 / succeeded 绿 / failed 红 / blocked 暗灰虚线边）；卡片头进度条 x/N；点击节点弹详情（输入/输出/负责人/耗时/错误/重试）；「确认执行 / 重新规划 / 编辑」按钮；>12 步概览+分组。
- 阶段折叠分组若踩 dagre #238（子图布局缺陷）换 elkjs（接口隔离在 `sopLayout.ts`，切换成本低）。

## 实施路线

- **M1 规划+确认**：planner + SOPPlan schema + 校验/修复 + `sop:plan_ready` + 前端 DAG 只读渲染 + 确认/重规划；执行先退化回现有 router（计划指导执行）。
- **M2 DAG 执行**：SOP 服务 + 就绪集调度 + 每步 deep agent 执行器 + 全部 `sop:step_*` + 前端状态着色 + 失败/重试/blocked。
- **M3 打磨**：continue_best_effort、编辑步骤、断点恢复、re-plan 增强、`interrupt_on`/`AsyncSubAgent`、追踪打点。
