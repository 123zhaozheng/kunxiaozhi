# 前端 DAG 可视化调研：SOP 计划图（React Flow v12）

> 调研时间：2026-08-04
> 目标：在 React 19 + TypeScript + Vite 的聊天应用里，把 LLM 生成的 SOP（有序步骤 + 依赖关系）渲染成可交互 DAG 图，节点带执行状态（pending / running / succeeded / failed / blocked），支持查看/确认（approve/reject）。
> 已确认前提：`frontend/package.json` 已有 `@xyflow/react ^12.10.2`、`mermaid ^11.12.3`、`@excalidraw/excalidraw`；`frontend/src/components/layout/AppContent/MessageOutlinePanel.tsx` 已有成熟的 React Flow 用法。因此本文不讨论"选不选 React Flow"，而是"基于现有模式怎么设计 SOP DAG 面板"。

---

## 1. React Flow v12 能力确认

### 1.1 当前版本与升级

- 当前最新版本为 **`@xyflow/react 12.11.2`**（研究时点约 13 天前发布；2026-06-01 发布 12.11.0 加入 `autoPanOnSelection`，并在 12.11.x 系列持续做性能与类型修正）。项目锁 `^12.10.2`，可直接升级到 `12.11.x`，API 兼容。
  - 出处：[npmjs.com/package/@xyflow/react](https://www.npmjs.com/package/@xyflow/react)、[reactflow.dev/whats-new/2026-06-01](https://reactflow.dev/whats-new/2026-06-01)
- 近期与性能/本任务相关的 patch：`#5530` 提升 add nodes/edges 性能；`#5497` 预定义 node 尺寸与 handles 时跳过 eager render；`#5444` 导出 `MiniMapNode` 支持自定义小地图节点。
  - 出处：[reactflow.dev/whats-new](https://reactflow.dev/whats-new)

### 1.2 License：MIT，无"社区版阉割"

- React Flow 核心库 **MIT 授权**，可用于商业产品；**库内没有 gate 掉的 pro 功能**。React Flow Pro 是付费订阅，但内容仅为 pro 示例代码、模板与支持，不锁定功能。
  - 出处：[github.com/xyflow/xyflow/discussions/3397](https://github.com/xyflow/xyflow/discussions/3397)（维护者 moklick 明言 "There are also no pro features"）
- 项目已有的 `proOptions={{ hideAttribution: true }}` 即可隐藏右下角署名，合法合规。
- v12 附带能力（全部 MIT）：`MiniMap`、`Controls`、`Background`、`Panel`、`NodeToolbar`、`NodeResizer`、自定义节点/边、SSR 支持、`colorMode="system"` 跟随深浅色。
  - 出处：[reactflow.dev](https://reactflow.dev/)、[reactflow.dev/examples/overview](https://reactflow.dev/examples/overview)

### 1.3 自定义节点/边

- 自定义节点就是普通 React 组件，通过 `nodeTypes` prop 注册，用 `Handle` 声明连接点。项目 `MessageOutlinePanel.tsx` 已是标准示范：`OutlineFlowNode` + `Handle`（target Top / source Bottom）+ smoothstep 边 + `BackgroundVariant.Dots` + `Controls`。
- 自定义边用 `BaseEdge` + `getSmoothStepPath` 或 `getBezierPath` 实现；`EdgeProps` 泛型带 `data` 与 `type` 字面量约束。
  - 出处：[reactflow.dev/learn/customization/custom-nodes](https://reactflow.dev/learn/customization/custom-nodes)、[AnimatedNodeEdge 示例](https://reactflow.dev/whats-new)
- **`nodeTypes` / `edgeTypes` 必须在组件渲染之外定义（模块级常量）**，或 `useMemo` 缓存；否则产生 "node type X could not be found" 的 dev 警告、导致每次渲染重建并可能丢节点交互。项目现状已在模块级定义 `const nodeTypes = { outline: OutlineFlowNode }`，SOP 面板沿用。
  - 出处：[reactflow.dev 官方示例结构](https://reactflow.dev/examples/overview)、[explainx.ai react-flow-implementation](https://www.explainx.ai/skills/existential-birds/beagle/react-flow-implementation)

### 1.4 受控组件

- 受控：传 `nodes` / `edges` + `onNodesChange` / `onEdgesChange`（用 `applyNodeChanges` 处理）；非受控：`defaultNodes` / `defaultEdges`（库内部管 state）。
- 本任务中节点位置/状态由后端事件 payload 推导，**最自然的是受控但只读**：nodes/edges 用 `useMemo` 从 payload 构建，不接 `onNodesChange`（或仅接 `applyNodeChanges` 丢弃拖拽），配合 `nodesDraggable={false}`、`nodesConnectable={false}`。
  - 出处：[github.com/xyflow/xyflow/discussions/960](https://github.com/xyflow/xyflow/discussions/960)、[synergycodes.com/state-management-in-react-flow](https://www.synergycodes.com/blog/state-management-in-react-flow)
- 视口控制用 `useReactFlow()` 的 `setViewport` / `fitView`（`MessageOutlinePanel` 已用此做"跳转到当前节点 + 300ms 动画"），SOP 面板可复用同样手法做"自动跟随当前执行步骤"。

### 1.5 布局辅助：官方无内置布局，需 dagre/elkjs

- React Flow **没有内置布局算法**，官方文档明确 "We have not implemented our own layouting solution yet"，推荐第三方：dagre（简单、同步、drop-in）、elkjs（功能全、异步、配置复杂）、d3-hierarchy、d3-force。
  - 出处：[reactflow.dev/learn/layouting/layouting](https://reactflow.dev/learn/layouting/layouting)（2026-07-23 更新）
- 验证过 **`@xyflow/layout` 包在 npm 上不存在**（`registry.npmjs.org/@xyflow/layout/latest` 返回 404），市面上同名社区包仅 `@jalez/react-flow-automated-layout`（基于 dagre，1.2.6，采用率极低，不推荐引入）。
- 官方 dagre 示例 `getLayoutedElements(nodes, edges, direction)` 模式即业界标准用法（见 §2）。

---

## 2. DAG 自动布局：dagre vs elkjs

### 2.1 对比（React Flow 官方文档口径）

| 维度 | dagre（@dagrejs/dagre） | elkjs |
|---|---|---|
| 分层布局 | ✅ 分层 rank 布局，`rankdir: TB/BT/LR/RL` | ✅ `elk.algorithm: layered` 等大量算法 |
| 动态节点尺寸 | ✅ 支持（需把节点宽高喂给它） | ✅ 支持 |
| 子流程（sub-flow） | ❌ 不支持（已知 issue dagre#238：跨子流连线布局错乱） | ✅ 支持，但需嵌套 children 结构，数据要转换 |
| 边路由 | 无（边交给 React Flow 渲染） | ✅ 支持正交/平滑路由 |
| 同步/异步 | ✅ 同步，即调即得 | ⚠️ 异步（返回 Promise） |
| 复杂度 | 极简，配置少 | 复杂，官方直言 "We don't often recommend elkjs because its complexity" |
| 使用热度 | 官方示例主推，社区量大 | 官方有示例，社区量小 |

- 出处：[reactflow.dev/learn/layouting/layouting](https://reactflow.dev/learn/layouting/layouting)、[github.com/xyflow/xyflow/discussions/3495](https://github.com/xyflow/xyflow/discussions/3495)、[reactflow.dev/examples/layout/dagre](https://reactflow.dev/examples/layout/dagre)

### 2.2 标准 dagre 接入模式（官方示例）

```ts
import dagre from "@dagrejs/dagre";
import { Position, type Node, type Edge } from "@xyflow/react";

const dagreGraph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
const nodeWidth = 172; // 或从 node.measured?.width 读取

function getLayoutedElements(nodes, edges, direction = "TB") {
  const isHorizontal = direction === "LR";
  dagreGraph.setGraph({ rankdir: direction, nodesep: 50, ranksep: 80 });
  nodes.forEach((n) => dagreGraph.setNode(n.id, { width: nodeWidth, height: nodeHeight }));
  edges.forEach((e) => dagreGraph.setEdge(e.source, e.target));
  dagre.layout(dagreGraph);
  const layouted = nodes.map((n) => {
    const pos = dagreGraph.node(n.id);
    return {
      ...n,
      targetPosition: isHorizontal ? Position.Left : Position.Top,
      sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
      position: { x: pos.x - nodeWidth / 2, y: pos.y - nodeHeight / 2 }, // dagre 中心点 → React Flow 左上角
    };
  });
  return { nodes: layouted, edges };
}
```

- 关键点：dagre 以"节点中心"为锚，返回位置要减去宽/高的一半换算成 React Flow 的左上角锚；`rankdir` 决定横向(LR)还是纵向(TB)；`nodesep`/`ranksep` 控制间距。`@dagrejs/dagre` 最新版 **3.0.0**（npm，研究时点 3 个月前发布）。
  - 出处：[reactflow.dev/examples/layout/dagre](https://reactflow.dev/examples/layout/dagre)、[npmjs.com/package/@dagrejs/dagre](https://www.npmjs.com/package/@dagrejs/dagre)

### 2.3 SOP 步骤图选型结论

- SOP 的结构通常是"长串行主链 + 少量并行分支（fan-out/fan-in）+ 一个确认门禁"，节点数一般在 5–30。这是 **dagre 的主场**：同步、分层清晰、边交叉在可控范围，接 React Flow 零摩擦。
- elkjs 的价值（子流程分组、正交边路由）只有当出现"步骤太多需要折叠成分组节点"且**要求分组内部也自动布局**时才体现，但代价是异步 + 嵌套数据结构转换。一期不建议。
- 若将来要折叠分组，更轻的做法是前端自己把一组步骤包成一个"阶段节点"（collapsed），用 dagre 只布局顶层节点——不需要 elkjs（dagre 官方不支持 sub-flow，见 2.1）。

### 2.4 布局放前端还是后端

- **推荐：LLM/后端只输出逻辑 DAG（步骤 + depends_on），前端跑 dagre 布局。**
  - 理由 1：LLM 直接生成坐标不可靠（无法稳定保证不重叠、不交叉），业界（含 mermaid 自身）都是"生成逻辑图 → 客户端/渲染器自动布局"。mermaid 的 flowchart 底层就是 dagre，而项目已有 `MermaidDiagram.tsx` 处理 LLM 输出的 mermaid 文本。
  - 理由 2：dagre 对 <100 节点是毫秒级同步计算，前端承担毫无压力，还能用节点真实 `measured` 尺寸参与布局。
  - 理由 3：后端不需要管布局细节，事件 payload 保持纯净（step id / 依赖 / 状态 / 详情），前端构建 nodes/edges 更符合项目「组件 → services/api」的数据流约定。
- 例外：若后续要在大屏/只读端复用同一 DAG 且不想引入 JS 布局依赖，可把 dagre 布局结果缓存下发；一期不做。

---

## 3. 聊天场景嵌入范式（业界如何给非技术用户展示步骤流）

### 3.1 GitHub Actions workflow 图 —— 最贴合的参照系

- 每次 workflow run 生成一张**实时图**：每个 job 一个节点，左侧图标表达 job 状态（成功/进行中/失败），连线表达 `needs:` 依赖，失败会跳过下游（对应我们的 blocked）。
- 状态**色码**（GitHub 官方博客口径）：绿色=成功、黄色/进行中、红色=失败，一眼可扫。
- "默认并行、`needs:` 才串行"的模型与 SOP 的"步骤 + 依赖"完全同构：SOP 步骤就是 job，`depends_on` 就是 `needs`。
  - 出处：[dev.to/github/setup-continuous-delivery-with-github-actions-4pea](https://dev.to/github/setup-continuous-delivery-with-github-actions-4pea)、[github.blog/7-advanced-workflow-automation-features-with-github-actions](https://github.blog/developer-skills/github/7-advanced-workflow-automation-features-with-github-actions/)

### 3.2 LLM 可观测性平台：trace tree / agent graph

- **Langfuse**：一条 trace = 一个完整单元（一次 agent run 正好符合 SOP 执行）；UI 提供 **trace tree** 与 **agent graph** 双视图；每个 observation 有类型（generation/tool），点击看该步的 input/output；命名建议用动词开头的动作名（`classify-intent`），**动态值放 metadata 不进名字**——这直接指导我们的 SOP 节点命名与详情面板设计。
  - 出处：[langfuse.com/docs/observability/best-practices](https://langfuse.com/docs/observability/best-practices)
- **LangSmith**：traces → runs（带类型的观测），有 agent graph 视图；LangGraph Studio 是其可视化 IDE。
  - 出处：[laminar.sh/blog/2026-01-29-laminar-vs-langfuse-vs-langsmith-llm-observability-compared](https://laminar.sh/blog/2026-01-29-laminar-vs-langfuse-vs-langsmith-llm-observability-compared)

### 3.3 工作流平台（n8n / Dify / Coze）对"执行状态"的展示

- **n8n**：canvas 上每个 node 在执行后上色（成功/失败/跳过），点节点看该次运行的 input/output；Executions 视图回放历史。数据流默认左→右。
  - 出处：[docs.n8n.io/courses/level-one/chapter-1](https://docs.n8n.io/courses/level-one/chapter-1/)、[aiworkflowsautomation.com](https://aiworkflowsautomation.com/understanding-the-n8n-interface-canvas-nodes-and-executions/)
- **Dify / Coze / FastGPT**：工作流 canvas 面向搭建者；对最终用户则是在**对话流里内嵌"执行轨迹卡片"**（LLM/工具/条件分支逐步展示输入输出），这是"聊天内嵌 DAG 卡片"的直接范式。
  - 出处：[jimmysong.io/blog/open-source-ai-agent-workflow-comparison](https://jimmysong.io/blog/open-source-ai-agent-workflow-comparison/)

### 3.4 Human-in-the-loop：plan → approve → execute（对应确认门禁）

- **LangGraph interrupt** 是标准模式：`plan` 节点产计划 → `interrupt(payload)` 暂停 → 用户 approve/reject/edit → `Command(resume=decision)` 恢复；approve 节点三件事：冻结状态、收集反馈（reject 带修改意见）、路由（approved→执行 / rejected→重新规划）。
  - 出处：[marktechpost.com plan-approve-execute](https://www.marktechpost.com/2026/02/16/how-to-build-human-in-the-loop-plan-and-execute-ai-agents-with-explicit-user-approval-using-langgraph-and-streamlit/)、[growwstacks.com/human-in-the-loop-ai-agents-langgraph](https://growwstacks.com/blog/human-in-the-loop-ai-agents-langgraph)
- 项目内已有对应物：`ApprovalPanel.tsx`（`PendingApproval`：message + fields + 倒计时/续期 + approve/cancel）与 `TodoBlock.tsx`（进度条 + 状态图标行）。SOP 门禁应**复用审批机制**，只是把 message 区域替换/叠加成 DAG 卡片。

### 3.5 项目内已有范式总结（可直接复用的模式）

| 项目文件 | 可复用点 |
|---|---|
| `MessageOutlinePanel.tsx` | ReactFlowProvider 包装、模块级 nodeTypes、Handle 双端、smoothstep 边 + `--theme-primary` 主题化、Dots Background、Controls、`setViewport` 定位动画、`fitView`、`proOptions.hideAttribution`、`minZoom/maxZoom` |
| `outlineFlow.css` | `.dark .react-flow` 画布底色、Controls 按钮主题化（`--theme-bg-card` / `--theme-border` / `--theme-primary-light`） |
| `TodoBlock.tsx` | 状态图标 + 色配置表（pending=灰、in_progress=蓝+spin、completed=绿），进度条 —— 语义可扩展为 pending/running/succeeded/failed/blocked |
| `MermaidDiagram.tsx` | LLM 生成图的静态渲染、下载/全屏/缩放，可作 SOP 图的"只读快照"备选 |
| `ApprovalPanel.tsx` | 审批门禁交互（倒计时、字段表单、approve/reject 提交） |

---

## 4. 交互设计

### 4.1 只读 vs 可编辑

- **一期推荐只读**：`nodesDraggable={false}`、`nodesConnectable={false}`、`elementsSelectable` 保留（选中高亮），缩放/平移开启。原因：SOP 执行图是"事实呈现"，拖拽/连线对非技术用户反而制造误操作；GitHub Actions / Langfuse 的只读图即此范式。
- 可编辑的需求若来自"确认门禁允许用户改计划"，**更自然的 UI 是审批面板里列可勾选/可排序的步骤清单**（复用 ApprovalPanel 表单），而不是拖 DAG。DAG 保持只读可视化。

### 4.2 缩放 / 平移 / 小地图

- `Controls`（放大/缩小/fit）社区版免费；`MiniMap` 社区版免费，**在桌面端节点多时很值**（v12.11 起可自定义 `MiniMapNode`）。面板窄/移动端可隐藏 MiniMap（按容器宽度或 UA 决定）。
- 自动跟随：执行中把当前 running 步骤滚到视野内（复用 MessageOutlinePanel 的 `setViewport` 动画手法，或对 running 节点调 `fitView({ nodes, duration })`）。

### 4.3 移动端适配

- React Flow 原生支持触摸：单指拖拽平移、双指捏合缩放（官方 touch-device 示例）；`panOnScroll` 与触摸共存有已知边界（[xyflow#5341](https://github.com/xyflow/xyflow/issues/5341)：`panOnDrag={false}` 时触摸设备无法滚动平移），所以不要禁用 drag。
  - 出处：[github.com/xyflow/xyflow/discussions/2403](https://github.com/xyflow/xyflow/discussions/2403)、[reactflow.dev/examples/interaction/touch-device](https://reactflow.dev/examples/interaction/touch-device)
- 遵循项目惯例：节点按钮 `min-h-[32px] min-w-[32px] touch-manipulation`，容器用 `safe-area-*`；节点宽度在窄屏可用 `w-[180px]` 左右的紧凑尺寸。
- 兜底：移动端卡片内嵌图 + 点节点弹详情抽屉（bottom sheet），避免在窄屏上做复杂画布交互。

### 4.4 长步骤序列：方向、折叠、分页

- **纵向（TB）vs 横向（LR）**：
  - TB 与聊天面板的阅读流一致（`MessageOutlinePanel` 就是纵向），**默认 TB**。
  - 但 TB 遇到宽 fan-out（一个节点分叉出 8 个并行步骤）会非常"高而宽"，需要横向滚动；LR 则把主链拉长、适合"步骤多、分支少"。
  - 建议：默认 TB，提供方向切换（dagre 重跑一次毫秒级，官方示例就有 TB/LR 切换按钮），面板宽度窄时自动 LR 或缩小。
- **步骤过多（>15~20）**：
  - 方案 A（推荐起步）：**折叠分组**——把满足条件的连续步骤包成"阶段节点"（dagre 只布局顶层；子步骤点开展开子图或弹详情）。dagre 官方不支持 sub-flow，前端自己折叠是低成本的替代。
  - 方案 B：**只渲染当前/最近相关片段**，其余步骤收进底部清单（类似消息大纲），跟随执行逐步展开。
  - 方案 C：MiniMap + fitView 让用户在大图上导航（GitHub Actions 大 workflow 的做法）。
- 执行中自动收拢：已完成的上游步骤默认不展开详情，减少视觉噪音（Langfuse 也在强调"滤掉噪音 span"）。

### 4.5 "粒度管控"在 UI 的体现

- 语义上：harness 的粒度管控 = 每个步骤展示多细（一句话标题 / 标题+输入输出 / 含子步骤明细）。
- UI 落地：
  - 节点常态只显示标题（一行截断）+ 状态徽标；
  - **点击节点 → 详情面板/抽屉**：输入、输出、负责 agent/角色、耗时、失败原因（对应 Langfuse 每步 input/output、GitHub Actions 的 job 展开日志）；
  - 全局"展开/收起"切换：展开=所有节点带详情摘要（面板变大、节点变高，dagre 重排）；收起=紧凑图。
  - 粒度不进入 DAG 本身，而是"图 + 详情"两层，让图永远保持可扫性。

---

## 5. 推荐方案（基于现有 MessageOutlinePanel 模式）

### 5.1 组件结构

仿照 `MessageOutlinePanel`（`ReactFlowProvider` 外层 + Inner 实现 + 模块级 `nodeTypes` + 独立 CSS）：

```
frontend/src/components/chat/ChatMessage/   (或 layout/AppContent/，跟随实现时的目录约定)
  SopPlanPanel.tsx      // 导出包装：ReactFlowProvider + props.items 为空则 null
  sopPlan.ts            // SopPlanStep 类型、buildNodes/buildEdges、dagre 布局封装 getSopLayout()
  sopPlanFlow.css       // .dark 画布底色 + Controls 主题化（拷贝 outlineFlow.css 模式）
  SopPlanNode.tsx       // 自定义节点（可选拆文件，或与面板同文件，同 MessageOutlinePanel 习惯）
```

`SopPlanPanel` props（建议）：

```ts
interface SopPlanPanelProps {
  steps: SopPlanStep[];            // 后端/LLM 产物：{ id, title, dependsOn: string[], status, detail? }
  activeStepId?: string | null;    // 自动跟随
  onStepClick?: (stepId: string) => void;
  onApprove?: () => void;          // 确认门禁（或走审批消息机制，见 5.4）
  onReject?: () => void;
  personaAvatar?: string | null;   // 可选，保持消息风格一致
}
```

节点数据模型（对齐 `OutlineNodeData` 的 `[key: string]: unknown` 宽松索引写法，便于扩展）：

```ts
interface SopNodeData {
  label: string;
  status: "pending" | "running" | "succeeded" | "failed" | "blocked";
  stepIndex: number;
  detail?: { input?: string; output?: string; assignee?: string; error?: string };
  [key: string]: unknown;
}
```

### 5.2 布局方案

- **默认 dagre 纵向（TB）**，节点 `w-[200px]` 上下 `Handle`（Top target / Bottom source，与 MessageOutlinePanel 完全一致）；`ranksep` 取 70~90，`nodesep` 取 40~60。
- `getSopLayout(steps)` 在 `useMemo` 里同步调用：把 `steps` + `dependsOn` 转 dagre 图 → 布局 → 输出 `{ nodes, edges }`。`edges` 用 `smoothstep`，样式沿用 `--theme-primary` 半透明 stroke。
- 增加 `direction: "TB" | "LR"` prop 或面板内切换按钮（dagre 重排毫秒级）。
- 新增依赖：**`@dagrejs/dagre@^3`**（npm 最新 3.0.0）。不引入 elkjs。`@xyflow/layout` 不存在，勿装。
- 依赖来源结论：**前端布局**。LLM 只给逻辑 DAG。

### 5.3 状态流（nodes/edges 从事件 payload 构建）

- 每个事件/状态更新 → `steps` 数组新引用 → `useMemo` 重算 nodes/edges → React Flow 受控渲染。**节点位置在重算时不变**（dagre 结果稳定），只有 `data.status` / `data.detail` 变化，视觉上仅状态色/徽标变化，无跳动。
- 若后端按"步粒度"推送（推荐），普通 props 足够；若未来出现 token 级高频流，遵循项目 `state-management.md` 的 **createSingletonStore + useSyncExternalStore + 每帧合并**约定接入。
- 节点状态视觉（沿用 TodoBlock 色系语义 + 扩展）：
  - `pending`：灰（`stone-400/500`）空圆
  - `running`：蓝 + `Loader2 animate-spin`，节点 ring 高亮 `--theme-primary`（或蓝）
  - `succeeded`：绿（`emerald-500/400`）`CheckCircle2`
  - `failed`：红 + 边框/底色警示
  - `blocked`：琥珀/橙，图标如 `Ban`/`ShieldOff`，可加"被 X 跳过"角标
- 边状态：`running` 节点的出边可用 `animated: true`（React Flow 内置 CSS 流动动画，MIT 免费）表达"流动中"；被 block 的路径边降透明度。
- 自动跟随：`activeStepId` 变化 → 对目标节点 `fitView({ nodes: [target], duration: 300 })` 或 `setViewport`（复制 MessageOutlinePanel 的 useEffect 手法）。

### 5.4 与确认门禁（approve/reject）的整合

- 推荐：**审批仍走现有 `PendingApproval` / ApprovalPanel 机制**，新增一个审批类型（如 `type: "sop_plan"`），其 message 区域渲染 `<SopPlanPanel steps={...} onStepClick />`，底部沿用 Submit/Cancel（即 approve/reject）按钮与倒计时。
- 备选：若 SOP 面板独立于审批弹层存在（如常驻右侧栏），则在面板头部放 Approve/Reject 两个按钮，直接调用现有 `onRespond(id, {}, approved)` 路径；两种都复用 `ApprovalPanel` 的倒计时/续期逻辑，避免重复造轮子。
- 交互顺序（对应 LangGraph plan→interrupt→execute）：`pending` 全灰 → 用户 approve → 首个步骤转 `running` → 逐个推进 → 失败/拒绝路径染红/琥珀。面板本身**不持有执行状态**，只是 payload 的投影。

### 5.5 备选与边界

- **备选 A：mermaid 渲染**（项目已有 `MermaidDiagram.tsx`）。适合"LLM 顺手输出 mermaid 流程图"的只读快照/分享；但状态着色、点击详情、自动跟随、方向切换全部要手搓 SVG hack，**不做为执行期主视图**，可作为导出/降级兜底。
- **备选 B：自研 canvas/SVG**。无必要，React Flow 已覆盖。
- **边界**：节点 >100 或需要真正的 sub-flow 布局时再评估 elkjs；移动端主交互降级为"点节点看详情抽屉"，不做完整画布。

### 5.6 理由一句话总结

SOP DAG 与项目已落地的 `MessageOutlinePanel` 是同一个问题的两种数据形态（对话大纲 vs 执行计划）：同样的 ReactFlowProvider/自定义节点/smoothstep/主题化骨架 + 一个 dagre 布局函数 + 状态着色 = 最小成本拿到 GitHub Actions 级别的执行图体验；审批复用现有 `PendingApproval` 通道，后端只输出逻辑 DAG，前端专管布局与呈现。

---

## 附：关键出处处

| 主题 | 出处 |
|---|---|
| @xyflow/react 12.11.2 / MIT / 无 pro 功能 | npmjs.com/package/@xyflow/react · reactflow.dev/whats-new · github.com/xyflow/xyflow/discussions/3397 |
| 布局文档（dagre/elkjs 对比表、无内置布局） | reactflow.dev/learn/layouting/layouting（2026-07-23） |
| dagre 官方示例（getLayoutedElements） | reactflow.dev/examples/layout/dagre |
| @dagrejs/dagre 3.0.0 | npmjs.com/package/@dagrejs/dagre |
| @xyflow/layout 不存在 | registry.npmjs.org/@xyflow/layout/latest → 404 |
| dagre 不支持 sub-flow | github.com/xyflow/xyflow/discussions/3495 · dagrejs/dagre#238 |
| 受控/非受控、nodeTypes 定义位置 | github.com/xyflow/xyflow/discussions/960 · reactflow.dev/examples/overview |
| GitHub Actions 实时图 + 状态色码 | dev.to/github/setup-continuous-delivery-with-github-actions-4pea · github.blog |
| Langfuse trace tree / agent graph / IO 详情 | langfuse.com/docs/observability/best-practices |
| n8n / Dify / Coze 执行展示 | docs.n8n.io · jimmysong.io/blog/open-source-ai-agent-workflow-comparison |
| LangGraph plan→approve→execute | marktechpost.com · growwstacks.com |
| React Flow 触摸支持 | github.com/xyflow/xyflow/discussions/2403 · issues/5341 · reactflow.dev/examples/interaction/touch-device |
| 项目内部范式 | frontend/src/components/layout/AppContent/MessageOutlinePanel.tsx · outlineFlow.css · ChatMessage/TodoBlock.tsx · ChatMessage/MermaidDiagram.tsx · panels/ApprovalPanel.tsx |
