# TeamAgent 丝滑 Harness：SOP DAG 规划与可视化 —— 网络调研报告

> 调研日期：2026-08-04
> 目标产品形态：AI 聊天平台（FastAPI + LangChain/LangGraph/DeepAgents 后端，React 19 + TypeScript 前端）。用户构建「团队」（多 persona 角色），选择团队模式后，用户复杂问题先由 LLM 拆解成结构化 SOP 文档，前端渲染为 DAG 图供用户确认，确认后按 DAG 拓扑并行/串行执行，各步骤分派给不同 persona 子代理。
> 约束：本报告只调研与给建议，不动代码。
> 关联文档：仓库根目录另有 `research/dag-visualization-frontend.md`（前端可视化专项调研）与 `research/multi-agent-sop-orchestration.md`（早期版本），本报告为当前任务（08-04-teamagent-sop-dag-harness）的正式调研产物，引用前端结论时沿用前者的验证结果。

---

## 0. 项目现状速览（作为建议依据，已核实代码）

- **外层图**（`src/agents/team_agent/graph.py`）：`START -> team_router_node -> END`，单节点包装，`compile(checkpointer=None)`。注释明确警告：「若外层图扩展为多节点工作流需要 resume，需给外层加独立命名空间的 checkpointer，避免与内层图的 message state 冲突」。
- **内层 Deep Agent**（`src/agents/team_agent/nodes.py`）：`team_router_node` 内用 `create_deep_agent(...)` 构建内层 agent；每个团队成员（`team.active_members`）编译成一个 `SubAgent`（name/description/system_prompt/middleware），含 role 系统提示、技能、记忆、沙箱 middleware；主代理只做路由/综合。内层有自己的 `get_async_checkpointer(thread_id=session_id)` 和 store。
- **事件通道**：`AgentEventProcessor(presenter, ...)` + `astream_events(version="v2")`，已有 SSE/presenter 体系（`presenter.metadata()`、`goal:end` 等事件）。
- **状态**（`src/agents/team_agent/state.py`）：`TeamAgentState = {input, session_id, messages, output, attachments}`，很薄。
- **版本核实**（`uv.lock`）：`deepagents 0.6.7`、`langgraph>=1.0.9`、`langchain>=1.0`；前端 `frontend/package.json`：`@xyflow/react ^12.10.2`（已在生产使用，见 `MessageOutlinePanel.tsx`）、`react 19.2.5`、`mermaid ^11.12.3`。
- 结论：**团队、persona、子代理编译、事件通道、checkpoint 都已就绪**；缺的是「规划层（plan/SOP 生成）+ 确认门禁 + DAG 执行引擎 + DAG 可视化」这一整条链路。

---

## 1. 多智能体编排架构全景

### 1.1 OpenAI Swarm / Agents SDK —— handoff（交接）模式

- **Swarm**（2024-10）：把多智能体编排收敛成两个原语——**routines**（指令+工具，即 system prompt）和 **handoffs**（「返回另一个 Agent」的 tool）。无状态机、无 DSL，LLM 通过调用 handoff 工具自行路由，对话控制权随之移交。官方定位教学/实验，不建议生产（Analytics Vidhya 综述：https://www.analyticsvidhya.com/blog/2024/10/openai-swarm/；Swarm 官方仓库：https://github.com/openai/swarm；InterviewsVector 教程：https://www.interviewsvector.com/academy/roadmap/16-multi-agent-and-swarms/11-handoffs-and-routines）。
- **OpenAI Agents SDK**（2025-03，Swarm 的生产继任）：四个核心原语 `Agent / Runner / Handoff / Guardrail`，handoff 是特殊工具调用，支持 **handoff filters**（限制可交接对象防循环）、**guardrails**（输入/输出校验）、**sessions**（会话状态）、**tracing**（内置可观测）。典型用法是 triage agent 路由到 specialist（Agents SDK 文档：https://openai.github.io/openai-agents-python/；综合评测：https://www.madebyagents.com/frameworks/openai-agents-sdk）。
- **对我们的启发**：handoff 是「运行时动态路由」的轻量模式，但它是会话式、无显式计划的。先规划出显式 DAG 再执行，本质上是对「LLM 临场发挥路由」的升级——把路由决策提前且显式化，减少不确定性；guardrails 的「校验-阻断」思想可用于我们每步的验收。

### 1.2 LangGraph —— StateGraph / 并行 / checkpoint / interrupt

- **StateGraph**：把 agent 建模为状态机——nodes（Python 函数，读 State 返回部分更新）、edges（转移）、显式 TypedDict State（reducer 声明合并规则）。LangChain 官方推荐新 agent 一律用 LangGraph（Use Apify 教程：https://use-apify.com/blog/langgraph-agents-production；GenAI Protos：https://www.genaiprotos.com/technologies/langgraph/）。
- **并行 fan-out/fan-in**：`Send` API 按运行时数据动态生成并行分支（map-reduce），reducer（如 `operator.add`）合并分支更新。注意 **superstep 语义**：同一 superstep 中所有就绪节点一起执行，fan-in 等全部完成；**一个分支抛异常会取消整个 superstep**，开 checkpoint 只重跑失败分支，没开则整批重跑（machinelearningplus：https://machinelearningplus.com/gen-ai/langgraph-map-reduce-parallel-execution/；focused.io：https://focused.io/lab/your-customer-service-bot-is-slow-because-its-single-threaded；markaicode：https://markaicode.com/langgraph-parallel-fan-out-fan-in/）。多个并行分支写同一 state key 必须用 reducer，否则最后一个分支覆盖前面的结果（focused.io 强调这是最常见 bug）。
- **checkpoint**：`MemorySaver` / `SqliteSaver` / `PostgresSaver` / `langgraph-checkpoint-mongodb`，每节点执行后序列化状态，支持断点续跑、time-travel、fork 调试（https://langchain-ai.github.io/langgraph/）。
- **HITL interrupt**：`interrupt_before=["node"]` 编译期挂载 + `update_state()` 预写审批结果，或节点内 `interrupt()` + `Command(resume=...)`；`thread_id` 必须持久化，跨进程恢复用 Postgres/Redis checkpointer 而不用 MemorySaver；暂停中的 run 要设审批超时（后台任务调用 `Command(resume="rejected: timeout")`）；`interrupt_after` 是「动作已发生再暂停」，审批门应挂在副作用节点之前（kalviumlabs：https://www.kalviumlabs.ai/blog/langgraph-in-production-stateful-multi-step-agents/；markaicode：https://markaicode.com/langgraph-human-in-the-loop-approval-gates/；Gheware：https://devops.gheware.com/blog/posts/langgraph-production-state-management-enterprise-2026.html）。
- **对我们的启发**：LangGraph 提供了成熟的「状态+并行+暂停/恢复」原语，但 (a) 我们的内层 deep agent 已占用 `thread_id=session_id` 的 checkpointer，外层再加图有命名空间冲突风险（代码注释已警告）；(b) superstep「all-or-nothing」对 LLM 长任务偏硬，与我们想要的「失败→blocked 传播 + 部分成功保留」冲突。

### 1.3 CrewAI —— sequential / hierarchical process

- 原语：**Agent**（role/goal/backstory）、**Task**（description/expected_output/agent/`context=[上游task]` 声明依赖）、**Crew**（agents+tasks+process）、**Flows**（`@start/@listen/@router` 声明式状态图，用于生产级流程）。
- **Sequential**：按任务列表顺序执行，前一个任务输出自动成为后一个的 context——隐式链式 DAG（PyShine 架构文：https://pyshine.com/CrewAI-Multi-Agent-Orchestration-Framework/）。
- **Hierarchical**：指定 `manager_llm`/`manager_agent` 后，manager 读取目标、动态拆子任务、分派给 worker、review 结果、必要时重新分派——运行时规划而非静态 DAG；代价是每步多一次 manager LLM 调用（markaicode：https://markaicode.com/crewai-hierarchical-process-manager-worker-agents/；reviewarticle：https://reviewarticle.org/reviews/crewai-review-multi-agent-orchestration/）。
- **并行是软肋**：官方 sequential/hierarchical 不支持原生并行/分支，独立子任务吞吐受限（reviewarticle 明确提到这一点）。
- **对我们的启发**：`Task.expected_output` + `Task.context` 的「显式字段」模式、manager review-redelegate 循环，是 SOP step 字段设计与「每步验收」的直接参考；但 CrewAI 不做原生并行，恰好反证我们的 DAG 引擎需要自己实现并行。

### 1.4 MetaGPT —— SOP 驱动的多角色流水线（与本项目最神似）

- 理念：**虚拟软件公司**。一行需求 → 按 SOP 拆成阶段（需求→设计→编码→测试→文档），每个阶段由固定角色 agent（产品经理、架构师、工程师、QA）产出**结构化中间产物**，下游消费上游产物，形成装配线。
- 核心机制：**SOP 编码为 prompt 序列**；agent 间通过**共享消息池（message pool）+ 订阅机制（subscribe）**通信——每个 Role `_watch` 关注的上游 Action 产出才进入其上下文，避免无关消息污染（论文：arXiv:2308.00352 https://arxiv.org/html/2308.00352v6；MIT AI Agent Index：https://aiagentindex.mit.edu/2024/metagpt/；官方文档 MultiAgent 101：https://docs.deepwisdom.ai/v0.5/en/guide/tutorials/multi_agent_101.html）。
- **对我们的启发**：这正是「团队模式」的教科书模型——SOP 步骤≈MetaGPT 的工序，persona 子代理≈角色，步骤产物≈结构化中间产物。区别是 MetaGPT 的 SOP 是**硬编码固定流水线**，而我们要求 **LLM 每次动态生成 SOP** 再按 DAG 执行——更灵活但更需要校验。MetaGPT 的「订阅过滤」思想对应我们的 `context_refs`（只注入本步真正需要的上游产物）。

### 1.5 AutoGen —— 会话式多智能体

- 核心是**对话编排**：Agent 间以多轮自然语言消息协作；`GroupChat` + `GroupChatManager` 决定下一发言人，speaker selection 支持 `auto`（LLM 选人）、`manual`（人工选人）、`random`、`round_robin`、自定义函数、以及**图式发言转移约束**（`allowed_or_disallowed_speaker_transitions`）；支持 `max_round`、`is_termination_msg`（如 `TERMINATE`）终止（ChatForest/AG2 综述：https://chatforest.com/reviews/ag2-autogen-multi-agent-framework/；InterviewsVector：https://www.interviewsvector.com/academy/roadmap/16-multi-agent-and-swarms/10-group-chat-speaker-selection；walkingtree 实例：https://walkingtree.tech/designing-intelligent-agent-behavior-with-autogen/）。
- 演进：v0.4 重构成事件驱动 actor 模型（RoutedAgent、pub/sub）；2026 年微软将 AutoGen 并入 Semantic Kernel 组成 Microsoft Agent Framework（RC 2026-02）（InterviewsVector 上述链接）。
- **对我们的启发**：自由对话编排可读性好但难给「确定性进度条/DAG 状态」；我们选择先规划后执行的显式 DAG，恰好规避 GroupChat 的不可预测性。其「发言转移图」可看作一种「LLM 约束下的执行图」，我们则是把图交给人确认。

### 1.6 Microsoft Magentic-One —— Orchestrator（编排者）模式

- 一个 lead **Orchestrator** agent + 4 个通用专家（WebSurfer/FileSurfer/Coder/ComputerTerminal）。Orchestrator 用**两个台账**控制执行（Microsoft Research：https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/；Foundry Labs：https://labs.ai.azure.com/innovations/magentic-one/；论文 arXiv:2411.04468）：
  - **外循环**维护 **Task Ledger**（已确认事实、假设、推断、整体计划）——计划推不动时修订计划；
  - **内循环**维护 **Progress Ledger**（当前进度、任务分派）——把子任务派给专家、检查完成度、失败时换 agent 重试。
- 基于 AutoGen 实现，在 GAIA/AssistantBench/WebArena 达到与重单体方案相当的成绩。
- **对我们的启发**：「Plan 数据（静态结构）+ Progress 数据（动态执行态）」分离，与我们的「SOP 文档 + 执行状态」完全同构；Orchestrator 的「进度停滞→重规划」对应我们的「失败/计划失效→re-plan（带用户反馈）」能力。

### 1.7 补充：LLMCompiler —— 计划即任务 DAG

- Planner **流式输出一个任务 DAG**（每个任务=工具+参数+依赖列表）；Task Fetching Unit 在依赖满足时调度执行（论文称比 ReAct 快 3.6x）；Joiner 汇总并决定 finish 或回炉重规划（LangChain 官方博客：https://blog.langchain.com/planning-agents/；论文 arXiv:2312.04511）。
- **对我们的启发**：它是「LLM 直接产出 DAG + 引擎按依赖并行调度」的最直接先例，与我们要做的事几乎一样——只是我们把 DAG 先给用户确认。

**本节小结表**

| 框架 | 编排原语 | 计划形态 | HITL | 对本文场景的启发 |
|---|---|---|---|---|
| Swarm / Agents SDK | handoff 工具 + guardrails | 无（运行时路由） | guardrails | 规划前置消除运行时路由不确定性 |
| LangGraph | StateGraph + Send + checkpointer | 可内嵌 plan 节点 | interrupt/Command | 状态、并行、续跑原语成熟；注意嵌套 checkpoint 与 superstep 语义 |
| CrewAI | sequential/hierarchical + Flows | 静态任务表 / manager 动态拆 | human_input/@human_feedback | expected_output + context 依赖声明 + manager review 可借鉴 |
| MetaGPT | SOP→角色流水线 + 消息池订阅 | 固定 SOP | 无 | 「角色×工序×结构化产物」= 我们的 SOP×persona；订阅=context_refs |
| AutoGen | GroupChat 对话 | 无（会话式） | 对话内 | 会话编排难给确定性进度，验证我们选 DAG 的合理性 |
| Magentic-One | Orchestrator + 双 Ledger | Task Ledger（可修订） | 无（自动重试） | Plan 与 Progress 分离、失败重派/重规划 |
| LLMCompiler | Planner 流式 DAG + 调度器 | 任务 DAG（依赖显式） | 无 | 「LLM 产 DAG + 引擎按依赖并行」的直接先例 |

---

## 2. 任务分解/规划（Planner）模式

### 2.1 Plan-and-Execute（先规划再执行）

- LangGraph 官方 plan-and-execute 教程结构：`PlanExecute` state → `planner` 节点（结构化输出 `Plan(steps: List[str])`）→ `agent` 执行单步 → `replan` 按中间结果修订剩余步骤 → 条件边 `should_end`（LangGraph 教程镜像：https://www.baihezi.com/mirrors/langgraph/tutorials/plan-and-execute/plan-and-execute/index.html；Medium 实现：https://medium.com/@okanyenigun/built-with-langgraph-33-plan-execute-ea64377fccb1）。
- 与 ReAct 对比（byaiteam：https://byaiteam.com/blog/2025/12/09/ai-agent-planning-react-vs-plan-and-execute-for-reliability/；laxaar：https://laxaar.com/blog/agent-planning-react-vs-plan-and-execute-1749470001700）：
  - ReAct 每步一条 LLM 调用、可动态应变，但长任务下 prompt 线性膨胀、token 烧得快、难调试；5-6 步以上 Plan-and-Execute 明显更省、更可调试。
  - Plan-and-Execute 把「战略规划」与「战术执行」分离，允许不丢状态地 re-plan；适合成本敏感、需要可预期行为、安全关键场景。
- **对我们的启发**：这是「先规划后执行」模式最标准的参照——我们把「replan」替换为「用户确认门禁 + 失败时的用户参与重规划」。

### 2.2 ReWOO（Reasoning Without Observation，arXiv:2305.18323）

- Planner 一次输出整份「脚本」：`Plan:` 行 + `E#:` 行，用 `#E1`/`#E2` 占位符引用前步结果；Worker 不调 LLM 直接执行工具（可并行）；Solver 最后综合。论文报告 **5x token 效率、HotpotQA +4 准确率**（IBM 解读：https://www.ibm.com/think/topics/rewoo；InterviewsVector 术语表：https://www.interviewsvector.com/academy/roadmap/14-agent-engineering/02-rewoo-plan-and-execute；LangGraph 实现示例：https://slavadubrov.github.io/ru/blog/2026/01/31/ai-agent-reasoning-loops/）。
- 代价：**执行中零自适应**——计划在第一步就锁定，步骤结果异常时 `#E2` 引用 `#E1` 会拿到垃圾输入；实际工程必须加「失败→重规划」兜底（IBM 文章也指出这一点）。
- **对我们的启发**：`#step_id` 占位符引用上游产物就是我们要做的 `context_refs` 机制；ReWOO 的 Worker=纯工具执行、无 LLM 决策，保证 DAG 状态可预期——我们的每步执行器应类似：指令固定、产物可注入、行为可预测。

### 2.3 Tree-of-Thoughts（ToT）等

- ToT（arXiv:2305.10601）：把问题分解成树、多路径搜索与回溯，需要评估多个候选分支；代价显著更高，仅适用于可明确定义「步骤+评估」的任务（EmergentMind：https://www.emergentmind.com/topics/tree-of-thoughts-tot-framework；Medium 综述：https://medium.com/data-science-collective/tree-of-thought-2d61b92ead38）。
- **对我们的启发**：对聊天产品偏重，不作为主模式；只可作规划质量增强——让 planner 产出 2 版候选 SOP 自评择优。

### 2.4 业界 Plan Schema（结构化输出）与 Plan/Execution 分离

汇总调研到的真实 schema 与实践：
1. **LangGraph plan-and-execute**：`Plan { steps: List[str] }`（最简，https://www.baihezi.com/mirrors/langgraph/tutorials/plan-and-execute/plan-and-execute/index.html）。
2. **带依赖的 DAG 计划**：`Task {id, description, dependencies[], status, result}`；planner prompt 明写「把任务按依赖排序、每步独立可执行」（Gheware：https://devops.gheware.com/blog/posts/ai-agent-design-patterns-implementation-guide-2026.html）。
3. **多智能体协调协议**：协调者输出 `{"task_N": {"agent": "researcher", "input": "...", "depends_on": ["task_1"]}}`，独立任务并行、依赖任务等待，结构化 `task_assignment` 消息带 `timeout_ms`/`priority`（QuanTriMang：https://quantrimang.com/prompt-thiet-ke-ai-agent-214969）。
4. **Plan-then-Execute 两阶段 prompt 模板**：Phase 1 分析目标/约束→建带依赖的分步计划→用可用工具校验可行性→估资源；Phase 2 每步执行+校验输出+失败恢复或 re-plan；计划限制 8-10 步以内，每步带显式 success criteria（QuanTriMang 同上）。
5. **提示工程指南**：`Break this down into 3-5 major phases`；few-shot 分解示例控制粒度与格式；先明确 goal 再分解（apxml：https://apxml.com/courses/prompt-engineering-agentic-workflows/chapter-4-prompts-agent-planning-task-management/breaking-down-problems-prompts）。
6. **Plan/Execution 分离的经典框架**：BabyAGI（任务 deque + 执行/创建/优先级三 agent 循环，https://www.ibm.com/think/topics/babyagi）；LangGraph plan-and-execute / ReWOO / LLMCompiler（见 1.7、2.1、2.2）都是「planner 出结构化计划、worker 机械执行、可选 re-plan」三段式。

**综合出的计划字段全集**（即第 6 节 SOP 模型来源）：`id / title / description / dependencies[] / assignee(角色) / expected_output / acceptance_criteria[] / context_refs / tool_hint(可选) / status / result`。

**对我们的启发**：采用「一次性显式计划 + 确定性执行 + 按需 re-plan（用户参与）」；用结构化 JSON schema + 温度≈0 让 planner 稳定输出；执行器不参与决策，保证 DAG 状态可预期。

---

## 3. SOP/计划粒度管控（Granularity Control）

业界具体做法与 prompt 技巧（「每步可执行、可验证」是共识）：

1. **步骤的定义标准**：分解规则明确要求「每个子任务必须在单次动作内完成、必须有可验证的输出、尽量互相独立、依赖前步输出的要显式标记、单个子任务不超过 3 次工具调用」（SurePrompts：https://sureprompts.com/blog/ai-agents-prompting-guide）。粒度要迭代调优——过细则拆、过粗则并（apxml：https://apxml.com/courses/agentic-llm-memory-architectures/chapter-4-complex-planning-tool-integration/task-decomposition-strategies）。
2. **步骤数量上限**：高层面用「3-5 个主要阶段」；工程指南用 **8-10 步上限**（超过则计划失焦/成本失控），计划过长时「再分解或再合并」（QuanTriMang：https://quantrimang.com/prompt-thiet-ke-ai-agent-214969）。按任务类型给步骤预算：客服路由 5 步、分析类 15 步、研究类 30 步（particula.tech：https://particula.tech/blog/ai-agent-loops-reasoning-steps-optimization）。
3. **深度限制**：分解的「可行动性」要求每步对应 agent 实际具备的能力（LLM 推理/工具）；对复杂任务用**层级分解**（主任务→子任务→子子任务），但对大多数场景单层即可（apxml 同上）。
4. **聚合/拆分策略**：质量随步骤数先升后降——**质量通常在 8-12 步左右达到峰值**，再长则输出质量下滑、上下文成本上涨（particula.tech 实测结论）。「步骤越细越稳但协调成本越高」是核心权衡；同时初始分解粒度常是整条链路最大瓶颈，业界建议**按 agent 能力清单对齐粒度**——planner 应知道每个执行者（persona/子代理）有什么技能、能干什么，据此合并/拆分步骤（apxml 的「Linking decomposition to available tools is essential」；早期调研中 SKILLWEAVER/SAD 思路：https://www.alphaxiv.org/overview/2606.22902）。
5. **「每步可验证」约束**：每步带 success criteria / 明确 completion criteria，执行后「确认步骤完成、记录意外结果、决定计划是否需调整」（SurePrompts；QuanTriMang）。粗粒度 heuristic：每个子任务≈专家专注 1-5 分钟的工作量；需 200-300 token 以上才能说清的步骤往往是再拆的候选（AI Wiki：https://artificial-intelligence-wiki.com/agentic-ai/agent-architectures-and-components/agent-task-decomposition/）。
6. **层级分解 vs 平铺**：对聊天产品 MVP，**建议单层平铺 DAG**；层级分解（阶段→步骤两级）只在步骤数超标（>12）时作为 UI 折叠分组手段，不在执行模型里引入子图状态。

**给我们的 prompt 技巧清单**（详见第 6 节）：显式 max_steps/min_steps、few-shot 分解示例、「合并过细步骤/拆分过粗步骤」自查规则、每步必须带 expected_output + acceptance_criteria、步骤粒度对齐团队成员技能清单。

---

## 4. 确认门禁（Human-in-the-Loop）：先展示计划再执行

### 4.1 业界产品的确认 UX / 事件模型

1. **Claude Code Plan Mode**：进入**只读阶段**（系统级禁用写工具，不是靠 prompt 约束），agent 只读代码并产出 Markdown 计划；用户可编辑/删步骤，确认后 agent 才写文件。核心设计：**plan 是可编辑的文件，approval 是执行入口**。配套 **Task 工具**（TaskCreate/TaskUpdate/TaskList）：计划→建任务清单→严格同时只有一个 in_progress→逐个完成（Plan Mode 指南：https://baeseokjae.github.io/posts/claude-code-plan-mode-guide-2026/；Claude Code vs Cursor 对比：https://skillsplayground.com/guides/claude-code-vs-cursor/；Bannerbear 对比：https://www.bannerbear.com/blog/claude-code-vs-cursor-which-ai-coding-tool-is-better-in-2026/）。
2. **Cursor Plan Mode**：agent 先做代码库调研、**问澄清问题**、产出可审阅/可编辑的计划，用户满意后才执行；推荐工作流 "Plan → Ask → Agent"（Bannerbear 同上）。
3. **OpenAI Operator / ChatGPT Agent（CUA）**：对**高风险动作**（付款、表单提交、发邮件）显式请求确认；敏感输入进入 **takeover mode**（用户手动填，期间 agent 不记录）；**Watch Mode** 要求关键任务全程主动监督；拒绝高危任务（银行转账等）；配套 prompt injection 监控（OpenAI 官方：https://openai.com/index/introducing-chatgpt-agent/；Operator 介绍：https://openai.com/index/introducing-operator/；System Card：https://cdn.openai.com/operator_system_card.pdf）。
4. **LangGraph interrupt**：`interrupt_before=[...]` 暂停并持久化 checkpoint → UI 收到 interrupt 事件 → 人工审批 → `update_state()` 或 `Command(resume=...)` 恢复；支持拒绝时改写 state 走其他分支（如 route：approved→执行节点，rejected→replan 节点）；并行双人审批用 `Send` fan-out 两个审批节点再 join（kalviumlabs / markaicode / Gheware 链接见 1.2）。
5. **Dify Human Input 节点（v1.13.0）**：工作流执行到该节点**暂停**，把表单发给指定人（Webapp 或 Email 投递）；审批人审阅 AI 输出、填字段、按自定义按钮（Confirm/Regenerate/Escalate）**按动作路由分支**；支持超时（如 3 天无人响应自动 Forward）；审批意见作为输出变量给下游节点；提供 `GET /console/api/workflow/<run_id>/pause-details` 让外部 app 拿到暂停详情与 form token 集成（Dify 官方博客：https://dify.ai/ko/blog/the-human-input-node-bringing-human-judgment-into-automated-workflows；Release 1.13.0：https://github.com/langgenius/dify/releases/tag/1.13.0；论坛公告：https://forum.dify.ai/t/1-13-0-human-in-the-loop-and-workflow-execution-upgrades-latest-community-version/1085；讨论 #32364：https://github.com/langgenius/dify/discussions/32364）。另有「Review & Edit」能力：生成 UI 让人审阅/修改 AI 输出变量再继续。
6. **Coze**：工作流可作为函数被 agent 调用，支持节点级暂停确认（与 Dify 对标，见早期调研的 Dify 讨论引用：https://github.com/langgenius/dify/issues/3126）。
7. **CrewAI**：Task `human_input=True` 暂停等待；Flows `@human_feedback` 装饰器暂停流程并持久化状态；企业版有「Pending Human Input」状态 + webhook 通知（CrewAI HITL 文档：https://docs.crewai.com/en/learn/human-in-the-loop）。
8. **AWS Step Functions**：内置 `Wait for Task Token`（callback）模式——工作流暂停并发出 task token，外部人工审批完成后用 token 恢复；重试配置每状态独立、指数退避 + jitter（AWS Marketplace 教程：https://aws.amazon.com/marketplace/build-learn/ai-agent-learning-series/agent-orchestration）。

### 4.2 确认门禁事件模型共识

- 状态机普遍为：`pending → (展示计划) awaiting_approval → approved / rejected(+feedback) / modified → executing → done`；
- 确认 UI 三层：**概览（计划文档/图表）→ 逐项编辑/增删（可选项）→ 一键 approve / regenerate**；
- 拒绝语义通常包含「重新规划（带用户反馈）」，而不只是「停止」（Dify 的 Regenerate 按钮、LangGraph 的 rejected→replan 分支）；
- 审批要有超时/过期策略（Dify 3 天超时示例、LangGraph 官方建议后台 job 处理过期 run）；
- 审批动作本身落审计日志（AWS Step Functions 的执行历史即审计轨迹）。
- 对我们的映射：SOP 生成后置 `awaiting_confirmation`；前端 DAG 卡片给「确认执行 / 重新规划（附意见） / 编辑步骤」三动作。

---

## 5. DAG 驱动执行引擎

### 5.1 拓扑排序与并行调度

- **Kahn 算法** O(V+E)：维护入度表，入度 0 的节点进入就绪集，执行完一个节点对其后继减入度、归零即就绪——天然支持「依赖满足即并行」的调度；图必须无环，Kahn 自带环检测（处理节点数 < 总节点数即存在环）——这是 LLM 产出计划的**确定性校验基线**（Arpit Bhayani：https://arpitbhayani.me/blogs/ai-topological-sort；TaskFlow 实现：https://dev.to/hajirufai/building-a-dag-workflow-orchestration-engine-from-scratch-in-python-3j9a）。

### 5.2 工业级 DAG 执行语义参考

| 系统 | 状态机 | 失败/重试语义 | 数据传递 |
|---|---|---|---|
| **Airflow** | `SCHEDULED→QUEUED→RUNNING→SUCCESS`；`FAILED→RETRYING`；`UPSTREAM_FAILED`（依赖失败，自身不跑）；`SKIPPED` | trigger rules：`all_success`（默认）/`all_done`/`one_success`/`one_failed`/`none_failed`；任务级 `retries` + `retry_delay` + `retry_exponential_backoff` + `max_retry_delay`；I/O 类默认 `retries=3, retry_delay=5min, exponential_backoff=True` | **XCom**（小数据）+ 对象存储（大数据，DB 只存引用）；所有可重入步骤幂等（UPSERT/幂等键） |
| **Prefect** | Flows/Tasks；Scheduled/Pending/Running/Completed/Failed/Cancelled/Cached | 任务级重试、缓存、超时；Prefect 3.0 事务语义让工作流自动幂等（同上下文重跑不重复执行） | 结果序列化到后端 |
| **Temporal** | **持久执行**（workflow-as-code 确定性重放） | Workflow 代码必须确定性（同输入必同指令序列），非确定性副作用（时间/随机数/I/O）必须放 Activity；Activity 可重试但**有「执行两次」的极小窗口**→ Activity 必须幂等（幂等键）；事件历史 append-only 即审计日志；历史过长用 Continue-As-New | 事件历史（Event History）即状态 |
| **n8n** | 节点执行栈 + 等待队列 | 单节点 `Retry On Fail`（Max Tries/Wait Between Tries）；`continueOnFail`（失败不阻断，输出 error 对象）；全局 Error Trigger 工作流 | `$json` 上下文节点间传递 |
| **LangGraph** | superstep（一组并行分支）**all-or-nothing**；checkpointer 保存每 superstep 后状态 | 一个分支异常取消整个 superstep；开 checkpoint 只重试失败分支；并行写共享 key 需 reducer | State（TypedDict + reducer） |
| **AWS Step Functions** | 标准状态机持久化 | 每状态独立 retry 配置：退避倍数、最大尝试、**jitter**；超限进入错误处理状态或补偿流程 | JSON payload 状态间传递；Task Token 支持人工审批 callback |

来源：Airflow（https://dev.to/beefedai/building-atomic-multi-step-batch-workflows-with-airflow-2ce1；https://sparkcodehub.com/airflow/advanced/error-handling）；Prefect（https://ai.pydantic.dev/durable_execution/prefect/）；Temporal（https://thenewstack.io/temporal-durable-execution-platform/；https://zylos.ai/research/2026-04-27-durable-execution-agent-runtimes/）；AWS（https://aws.amazon.com/marketplace/build-learn/ai-agent-learning-series/agent-orchestration）；重试策略总纲（https://www.prismocode.io/ai-agent-retry-policies/）。

**关键语义启示（给我们的引擎）**：
- 状态机用 Airflow 命名：`pending / ready / running / succeeded / failed / blocked / skipped / cancelled`（`blocked`≈`UPSTREAM_FAILED`）；
- 重试用**指数退避+jitter**（应对 LLM 429/临时性错误），上限 2-3 次；**区分错误类型**——瞬时错误可重试，数据/校验类永久错误不重试直接失败上报（Airflow 分类法：transient vs permanent vs partial-side-effect）；
- **部分失败语义**：默认 `stop`（依赖失败→下游 `blocked`）；可选 `continue_best_effort`（同 n8n `continueOnFail`）；
- **幂等/副作用**：Temporal 的教训——「超时 ≠ 没发生」，外部副作用（发送消息、写文件）要幂等键或核对；
- **产物传递**：小产物随 DB/state 传递，大产物写对象存储传引用（Airflow「XCom 只传小数据」模式）；
- **限流**：并行 LLM 调用会烧 TPM 配额，fan-out 宽度必须用信号量/预算约束（focused.io、AWS 教程均强调）。

### 5.3 推荐状态机

```
run 级: draft → awaiting_confirmation → running → succeeded
                                        → failed (含 failed/blocked 列表) / cancelled
step 级: pending → ready ──→ running ──→ succeeded
                           │    ├─(retry, 退避+jitter)──→ ready
                           │    ├─→ failed(超出重试) → 依赖下游标 blocked
                           │    ├─→ cancelled（用户取消/run 终止）
                           └─→ skipped（条件不满足）
```

---

## 6. 对本项目的具体建议（可落地）

### 6.0 总体推荐方案（先说结论）

**推荐「应用层自研 DAG 执行引擎 + LangGraph/DeepAgents 只做单步执行器」的混合架构**，不把 SOP 编译成 LangGraph 大图。理由（每条都有依据）：

1. **SOP 是用户可编辑的数据，不是静态代码图**。用户要确认、改步骤、regenerate——数据模型存 MongoDB（`sop_runs`）最顺，应用层状态机可直接改/恢复（对应 Dify Human Input 把「等人」做成工作流一等公民、Claude Code 把 plan 做成可编辑文件的共同做法）。
2. **现有内层 deep agent 已占用 LangGraph checkpointer/thread_id**（`nodes.py` 用 `get_async_checkpointer(thread_id=session_id)`），`graph.py` 注释已明确警告嵌套 checkpoint 冲突；外层再加 checkpoint 图会引入命名空间复杂度。
3. **LangGraph superstep「all-or-nothing」**（§1.2/§5.2）对 LLM 长任务过硬：一个并行分支失败整批状态丢弃，与我们想要的「失败→blocked 传播 + 部分成功保留」冲突；业界也普遍给 fan-out 加 try/except + sentinel 处理部分失败（focused.io / markaicode 建议）。
4. **应用层能精确控制**：限流（Semaphore 对抗 LLM 429）、每步重试、取消、blocked/skipped 传播、审计——这些 Airflow/n8n 语义在应用层一个就绪集循环就能实现，不需要像 Temporal 那样引入确定性重放（Temporal 重放约束对「LLM 每步决策」是负担，重放的收益不匹配）。
5. 现有代码模式（`create_deep_agent` 构建 + `AgentEventProcessor` + SSE）可**原样复用**，改动集中在新增几个模块。

执行引擎形态：`SOP 服务（orchestrator）`用 `asyncio` + `Semaphore(max_parallel)` + 就绪集（Kahn 入度）驱动；每一步通过 `create_deep_agent`（或直接复用已编译的成员子代理）跑一个「单步 deep agent」，把步骤指令 + 上游产物注入 prompt。**注意 DeepAgents 0.6.7 已支持 `interrupt_on` 参数与 `AsyncSubAgent`（后台执行 + launch/check/update/cancel/list 工具）**——若单步内部需要暂停（如工具级确认），可优先用这两个原生机制（reference.langchain.com：https://reference.langchain.com/python/deepagents/graph/create_deep_agent；sandbox 教程：https://github.com/ArunJ95/deepagents-sandbox）。

### 6.1 SOP 数据模型（字段清单，Pydantic）

```python
class StepStatus(str, Enum):
    pending = "pending"       # 计划已生成，未开始
    ready = "ready"           # 依赖已满足，可调度
    running = "running"
    succeeded = "succeeded"
    failed = "failed"         # 超出重试次数
    blocked = "blocked"       # 依赖失败导致无法执行（≈Airflow UPSTREAM_FAILED）
    skipped = "skipped"       # 条件不满足/上游被取消
    cancelled = "cancelled"   # 用户取消/run 终止

class SOPStep(BaseModel):
    id: str                       # "1","2",... 稳定标识（供依赖引用）
    title: str
    description: str
    dependencies: list[str] = []  # 上游 step id（Kahn 建图）
    assignee: str                 # team member_id / subagent_type（必须 ∈ 团队花名册）
    expected_output: str          # 期望交付物（提示词+验收双用）
    acceptance_criteria: list[str] = []   # 可选，guardrail 校验/提示词用
    context_refs: list[str] = []  # 需读取的上游产物 key（默认取其直接依赖；MetaGPT 订阅过滤思想）
    status: StepStatus = StepStatus.pending
    attempts: int = 0
    max_retries: int = 2          # 每步重试上限（瞬时错误才重试）
    output: str | None = None     # 执行结果摘要/正文
    artifact_refs: list[str] = [] # 大产物存储引用（minio/oss/mongo gridfs）
    error: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

class SOPPlan(BaseModel):         # 持久化为 sop_runs 文档
    id: str
    session_id: str
    team_id: str
    goal: str
    user_feedback: str | None = None      # 确认/重规划时用户意见
    steps: list[SOPStep]
    status: Literal["draft","awaiting_confirmation","running","succeeded","failed","cancelled"]
    max_parallel: int = 3         # 并发上限（LLM 限流）
    on_failure: Literal["stop","continue_best_effort"] = "stop"
    created_at / updated_at
```

`assignee` 直接复用现有 `build_team_member_subagent_type(member)`（`nodes.py` 已有），保证 DAG 节点 ↔ persona 子代理一一对应。

### 6.2 规划器（Planner）设计

- **产出**：`SOPPlan` 的 JSON（`with_structured_output` 或 JSON mode + 温度≈0 + 输出 schema 传进 prompt，参考 ReWOO 的 structured LLM planner 实践）。
- **输入**：用户问题 + 团队花名册（member_id、角色名、职责摘要、可用技能/工具——即 §3.4 的「能力 hint」，planner 按成员能力对齐步骤粒度）+ 粒度参数 + 2-3 个 few-shot 示例。
- **System prompt 要点**：
  1. 身份：团队的项目经理/规划师，产出「可确认、可执行」的 SOP；
  2. 步骤约束：每步单一职责、必须产出一个**可验证交付物**（expected_output 具体到可直接验收，参考 SurePrompts「单动作+可验证输出」规则）；
  3. 依赖约束：`dependencies` 只引用已存在的 step id；无依赖且语义独立的步骤应并行；不要造「伪并行」（实质串行却拆开）；依赖要显式声明（QuanTriMang 两阶段模板）；
  4. 角色约束：`assignee` 必须来自花名册，且与步骤职责匹配；
  5. 粒度约束：步数 ∈ [min_steps, max_steps]；过粗/过细自查（合并无独立产出的步骤；拆分含多个交付物的步骤）；步骤预算按任务类型（简单 5 步 / 中等 15 步 / 复杂 30 步上限，particula.tech）；
  6. 输出：纯 JSON，字段如 schema；附 1 个 3-4 步的 few-shot 示例。
- **确定性校验层**（不依赖 LLM，Planner 之后必跑）：
  - Kahn 环检测（有环 → 报错触发修复）；孤儿步骤（无依赖无后继）警告；
  - `assignee` 是否都在团队内；`dependencies` 引用是否存在；
  - 步数边界；`expected_output` 非空且非敷衍（长度阈值）。
  - 校验失败 → **一次修复循环**：把校验错误喂回 LLM 重新生成（最多 2 轮），仍失败则降级为「串行 3 步兜底计划」。
- **可选增强**：planner 先输出 2 个候选 SOP 自评择优（ToT 简化版）；ReWOO 式 `#step_id` 占位符引用上游产物（即 context_refs 的自动推导）。

### 6.3 粒度控制参数（直接进配置/设置项）

| 参数 | 默认 | 说明 |
|---|---|---|
| `max_steps` | 8（上限 12） | 超上限强制合并或 UI 折叠分组（§3：8-10 步共识；质量峰值在 8-12 步） |
| `min_steps` | 2 | 低于下限视为「无需团队分解」，可回退单 agent |
| `max_depth` | 1（扁平 DAG） | 层级分解留 v2（§3.6） |
| `max_parallel` | 3 | 就绪集并发上限，防 429（§5.2） |
| `step_max_retries` | 2 | 指数退避+jitter（1s/2s/4s），仅瞬时错误重试（Airflow 分类法） |
| `require_expected_output` | true | 每步必须可验证（§3.5） |
| 步骤预算 hint | 按任务类型 | 简单 5 / 分析 15 / 研究 30（particula.tech） |
| 合并/拆分规则 | — | prompt 自查规则 + 修复循环（按成员技能粒度对齐） |

### 6.4 执行引擎（应用层自研）

1. **启动**：用户确认后 `POST /sop/{id}/confirm {action: approve|regenerate|edit_steps}`；`approve` → 状态置 running，启动后台 asyncio 任务（复用现有 `_stream_tasks` 模式登记可取消任务）。
2. **调度循环**：就绪集 = `status==ready 且所有依赖 succeeded`；用 `asyncio.Semaphore(max_parallel)` 控制并发；每步一个 task：
   - 构建单步执行器：复用 `team_router_node` 里 `create_deep_agent(...)` 的构建逻辑（建议抽成 `build_step_executor(step, persona, artifacts, model, config)`），把「步骤指令 + 上游产物（context_refs）+ 团队规则」拼进 system prompt，指定该 persona 的 subagent；
   - 结果：文本/工具产物 → `output` 落库；大产物写对象存储存 `artifact_refs`（Airflow「小数据 XCom / 大数据对象存储」模式）；
   - 失败：`attempts+1`，≤`max_retries` 且为瞬时错误 → `ready` 重新入队（退避）；否则 `failed`，下游按 `on_failure`：`stop` 标 `blocked` 并终止 run；`continue_best_effort` 则跳过其产物继续（n8n `continueOnFail` 语义）。
3. **取消/恢复**：run 级 `cancelled` 通过 asyncio 任务取消（现有 `TaskInterruptedError` 通道已存在）；DB 步骤状态驱动「从断点续跑」（已 `succeeded` 的步骤不重跑）。
4. **产物传递**：`artifacts: dict[step_id, {content|artifact_ref}]` 保存在 run 文档；下游注入其直接依赖的产物（截断 token 上限，大产物给引用让 agent 按需读取——现有 `ToolResultBinaryMiddleware`/`read_file` 模式可复用；MetaGPT 订阅过滤 = 只注入 context_refs）。
5. **最终综合**：全部 steps 结束后，可选「综合节点」让主 agent 汇总结论（Magentic-One Orchestrator / LLMCompiler Joiner 的角色）。

### 6.5 事件契约（复用 presenter/SSE，事件名带 `sop:` 前缀）

```
sop:plan_generating        # 规划中（可带流式 token）
sop:plan_ready             # data: 完整 SOPPlan（steps 全字段 + 隐含 edges），UI 渲染 DAG 并进入确认态
sop:plan_rejected          # 用户选择重新规划（带 feedback）
sop:started                # 确认后开始执行
sop:step_started           # data: {step_id}
sop:step_output            # data: {step_id, delta}（可选流式）
sop:step_succeeded         # data: {step_id, output_preview, artifact_refs}
sop:step_retrying          # data: {step_id, attempts, max_retries, error}
sop:step_failed            # data: {step_id, error, attempts}
sop:step_blocked           # data: {step_id, reason}
sop:step_cancelled         # data: {step_id}
sop:progress               # data: {done, failed, total, percent}（节流，如 ≥1s 一次）
sop:completed              # data: {final_report}
sop:failed                 # data: {failed: [id], blocked: [id]}
sop:cancelled
```

断线/历史回放：仿 Dify `include_state_snapshot` 思路，恢复时补发快照事件（前端据快照重建节点状态）。审批动作本身落审计日志（参考 AWS Step Functions 执行历史即审计）。

### 6.6 前端 DAG 可视化（结论引用 `research/dag-visualization-frontend.md` 的验证）

- **首选 `@xyflow/react`（已装 ^12.10.2，React 19.2.5 兼容，已有 `MessageOutlinePanel.tsx` 生产先例）**：自定义节点 = JSX + Tailwind + 主题 CSS 变量；`Controls`/`MiniMap`/`ReactFlowProvider` 内置；MIT 无功能锁定。
- **布局器**：`@dagrejs/dagre`（新增依赖，体积小，官方示例可抄）；若未来做阶段折叠分组踩中 dagre #238（子图布局缺陷）再换 elkjs（接口隔离在 `sopLayout.ts` 纯函数内，切换成本低）。
- 布局计算放**前端**：SOP 生成时 `getLayoutedElements()` 算一次并写回消息数据，执行期只改 `data.status` 不动坐标，天然防抖动。
- 交互：对话内**默认只读**（nodesDraggable/Connectable 关）；执行中节点状态着色（pending 灰 / running 主题色脉冲 / succeeded 绿 / failed 红 / blocked 暗灰虚线边）+ 卡片头部进度条 x/N；点击节点弹详情（输入/输出/负责人/耗时/错误与重试）；右上角「确认执行 / 重新规划 / 编辑」按钮；>12 步自动进入「概览+分组」模式。
- 备选：mermaid（已装，仅静态概览/导出用）；antv X6/G6、cytoscape、vis-network 均不推荐（非 React 惯用或与现有 React Flow 面板割裂）。

### 6.7 实施路线（简要）

1. **M1（规划+确认）**：planner 节点 + SOPPlan schema + 校验/修复循环 + `sop:plan_ready` 事件 + 前端 DAG 只读渲染 + 确认/重新规划按钮；执行先退化回现有 `team_router_node` 单 agent（「计划了但按老路径执行」）。
2. **M2（DAG 执行）**：SOP 服务 + 就绪集调度 + 每步 deep agent 执行器 + 全部 `sop:step_*` 事件 + 前端状态着色 + 失败/重试/blocked 语义。
3. **M3（打磨）**：`continue_best_effort`、编辑步骤、断点恢复、re-plan（带用户反馈）、并发限流调优、`interrupt_on`/`AsyncSubAgent` 原生 HITL 接入、LangSmith/OpenInference 追踪打点。

---

## 7. 参考资料（主要来源）

**多智能体编排**
- OpenAI Swarm：https://github.com/openai/swarm ；https://www.analyticsvidhya.com/blog/2024/10/openai-swarm/ ；https://www.interviewsvector.com/academy/roadmap/16-multi-agent-and-swarms/11-handoffs-and-routines
- OpenAI Agents SDK：https://openai.github.io/openai-agents-python/ ；https://www.madebyagents.com/frameworks/openai-agents-sdk
- LangGraph：https://langchain-ai.github.io/langgraph/ ；https://use-apify.com/blog/langgraph-agents-production ；https://www.genaiprotos.com/technologies/langgraph/ ；Map-Reduce/Send：https://machinelearningplus.com/gen-ai/langgraph-map-reduce-parallel-execution/ ；并行 fan-out：https://markaicode.com/langgraph-parallel-fan-out-fan-in/ ；HITL：https://www.kalviumlabs.ai/blog/langgraph-in-production-stateful-multi-step-agents/ 、https://markaicode.com/langgraph-human-in-the-loop-approval-gates/ 、https://devops.gheware.com/blog/posts/langgraph-production-state-management-enterprise-2026.html
- CrewAI：https://pyshine.com/CrewAI-Multi-Agent-Orchestration-Framework/ ；https://reviewarticle.org/reviews/crewai-review-multi-agent-orchestration/ ；https://markaicode.com/crewai-hierarchical-process-manager-worker-agents/ ；HITL：https://docs.crewai.com/en/learn/human-in-the-loop
- MetaGPT：arXiv:2308.00352（https://arxiv.org/html/2308.00352v6）；https://aiagentindex.mit.edu/2024/metagpt/ ；https://docs.deepwisdom.ai/v0.5/en/guide/tutorials/multi_agent_101.html
- AutoGen/AG2：https://chatforest.com/reviews/ag2-autogen-multi-agent-framework/ ；https://www.interviewsvector.com/academy/roadmap/16-multi-agent-and-swarms/10-group-chat-speaker-selection
- Magentic-One：https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/ ；arXiv:2411.04468 ；https://labs.ai.azure.com/innovations/magentic-one/
- LLMCompiler：https://blog.langchain.com/planning-agents/ ；arXiv:2312.04511

**Planner 模式**
- Plan-and-Execute：https://www.baihezi.com/mirrors/langgraph/tutorials/plan-and-execute/plan-and-execute/index.html ；https://medium.com/@okanyenigun/built-with-langgraph-33-plan-execute-ea64377fccb1 ；ReAct vs P&E：https://byaiteam.com/blog/2025/12/09/ai-agent-planning-react-vs-plan-and-execute-for-reliability/ 、https://laxaar.com/blog/agent-planning-react-vs-plan-and-execute-1749470001700
- ReWOO：arXiv:2305.18323 ；https://www.ibm.com/think/topics/rewoo ；https://www.interviewsvector.com/academy/roadmap/14-agent-engineering/02-rewoo-plan-and-execute
- ToT：https://www.emergentmind.com/topics/tree-of-thoughts-tot-framework
- Plan schema / 两阶段模板：https://quantrimang.com/prompt-thiet-ke-ai-agent-214969 ；https://sureprompts.com/blog/ai-agents-prompting-guide ；https://devops.gheware.com/blog/posts/ai-agent-design-patterns-implementation-guide-2026.html

**粒度管控**
- https://sureprompts.com/blog/ai-agents-prompting-guide ；https://quantrimang.com/prompt-thiet-ke-ai-agent-214969 ；https://particula.tech/blog/ai-agent-loops-reasoning-steps-optimization ；https://artificial-intelligence-wiki.com/agentic-ai/agent-architectures-and-components/agent-task-decomposition/ ；https://apxml.com/courses/agentic-llm-memory-architectures/chapter-4-complex-planning-tool-integration/task-decomposition-strategies ；https://apxml.com/courses/prompt-engineering-agentic-workflows/chapter-4-prompts-agent-planning-task-management/breaking-down-problems-prompts

**HITL 产品**
- Claude Code Plan Mode：https://baeseokjae.github.io/posts/claude-code-plan-mode-guide-2026/ ；对比：https://skillsplayground.com/guides/claude-code-vs-cursor/ 、https://www.bannerbear.com/blog/claude-code-vs-cursor-which-ai-coding-tool-is-better-in-2026/
- OpenAI Operator/ChatGPT Agent：https://openai.com/index/introducing-operator/ ；https://openai.com/index/introducing-chatgpt-agent/ ；System Card：https://cdn.openai.com/operator_system_card.pdf
- Dify Human Input：https://dify.ai/ko/blog/the-human-input-node-bringing-human-judgment-into-automated-workflows ；Release 1.13.0：https://github.com/langgenius/dify/releases/tag/1.13.0 ；https://forum.dify.ai/t/1-13-0-human-in-the-loop-and-workflow-execution-upgrades-latest-community-version/1085 ；https://github.com/langgenius/dify/discussions/32364
- AWS Step Functions HITL/重试：https://aws.amazon.com/marketplace/build-learn/ai-agent-learning-series/agent-orchestration

**DAG 执行引擎**
- Airflow：https://dev.to/beefedai/building-atomic-multi-step-batch-workflows-with-airflow-2ce1 ；https://sparkcodehub.com/airflow/advanced/error-handling
- Prefect：https://ai.pydantic.dev/durable_execution/prefect/
- Temporal：https://thenewstack.io/temporal-durable-execution-platform/ ；https://zylos.ai/research/2026-04-27-durable-execution-agent-runtimes/
- 重试策略：https://www.prismocode.io/ai-agent-retry-policies/
- 拓扑排序：https://arpitbhayani.me/blogs/ai-topological-sort ；https://dev.to/hajirufai/building-a-dag-workflow-orchestration-engine-from-scratch-in-python-3j9a

**DeepAgents / 前端**
- create_deep_agent API（含 subagents/interrupt_on/state_schema）：https://reference.langchain.com/python/deepagents/graph/create_deep_agent ；https://github.com/ArunJ95/deepagents-sandbox
- 前端可视化（本任务配套调研）：`research/dag-visualization-frontend.md`（React Flow 选型、dagre/elkjs、Dify SSE 事件流对标、状态着色与交互）
- Anthropic Building Effective Agents（workflow vs agent 决策框架）：https://www.anthropic.com/research/building-effective-agents ；解读：https://mer.vin/2026/05/when-not-to-build-ai-agents-anthropics-workflow-vs-agent-playbook/
