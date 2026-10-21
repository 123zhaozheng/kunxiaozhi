# Design: Agent 执行过程统一收纳分组

> 任务 10-16-agent-execution-collapse · 配套 prd.md；事实依据 research/ 六份（render-pipeline / icons-style-scroll / state-history / start-check-{compact-steps,stream-split,failed-locate}）。
> 用户定稿：简洁步骤模式；执行中=当前动作+已完成摘要；失败=分组展开并标失败。

## 1. 结论前置

- **v1 纯前端实现，后端零改动。** 分组层只消费 `Message.parts`（实时与历史同构），不碰 SSE 事件与后端。
- 新增代码集中在三处：①纯函数选择器 `executionProcess.ts`（分区 + 步数/耗时/能力图标 + 简洁行摘要）②分组组件 `ExecutionProcessGroup.tsx` ③轻量内存覆盖态 singleton，均落在 `frontend/src/components/chat/ChatMessage/` 同目录，复用既有 `CollapsiblePill` / `openPersistentToolPanel` / 状态色 / 计时原语。
- 不做：Todo 收纳、审批/SOP 搬移、子代理面板改造、失败重试入口、失败程序化滚动、跨刷新持久化、暖色 token。

## 2. 落点与数据流

```
ChatMessage (index.tsx:540-572)
  └─ parts 容器
       ├─ partitionMessageParts(message.parts)              [新，纯函数，保留原始 partIndex]
       │     ├─ processParts[]   过程类（保原序 + 原始下标）
       │     └─ bodyParts[]      正文/外置类（保原序 + 原始下标）
       ├─ <ExecutionProcessGroup ... />                      [新，分组容器]
       │     ├─ 分组头：能力图标组 + 状态文案 + chevron      [新渲染，复用五态色]
       │     └─ 展开区：processParts.map(简洁行)              [新渲染；详情经 openPersistentToolPanel 进已有侧栏]
       ├─ bodyParts.map(<MessagePartRenderer/>)              [原路径不动，传原始 partIndex/isLast]
       └─ RevealArtifactsSummary（567-571）                  [不动，仍消费全量 parts]
```

- 插入点：`index.tsx:540-572`（research/render-pipeline.md 结论）。parts 容器此前是 `parts.map(MessagePartRenderer)` 直铺；改为「分区 → 分组 + 正文」后，正文/产物相对顺序不变，过程行从平铺变一行分组头 + 可展开简洁行。
- 实时与历史自动一致：只消费 `message.parts`，`historyLoader.ts:310` 重放同一 `processMessageEvent`，无需分叉。
- 严格边界：`scheduled_task_draft` 工具的 `ScheduledTaskDraftCard` 虽属 `tool` part 分发，但属「待确认」——见 §3 分区表排除处理。

## 3. 分区规则（`executionProcess.ts`，纯函数，单测锁定）

按 `MessagePart.type`（`frontend/src/types/message.ts:34-44`）把一条消息的 parts 分为两类，各自保留原顺序与**原始下标**：

| part 类型 | 归类 | 理由（实证） |
|---|---|---|
| `thinking` | 过程 | ThinkingBlock 现为一行 pill（SubagentBlocks.tsx:578） |
| `tool`（一般） | 过程 | 各 Item 均为一行 pill / 卡 |
| `tool` 且 `isScheduledTaskDraftToolName(tool.name)` 且成功完成 | **body（外置）** | ScheduledTaskDraftCard 是确认/取消卡（MessagePartRenderer.tsx:225-234），确认卡不能藏（prd R7.2）。分区把它排除出过程集，渲染路径不变。 |
| `subagent` | 过程（计 1 步，内部不展开计数） | SubagentBlock 卡片自有面板收纳 |
| `sandbox` | 过程（error 状态计入失败集） | SandboxItem pill |
| `summary` | 过程 | SummaryItem pill |
| `text` | body | MarkdownContent 全宽 |
| `cancelled` | body（外显，唯一重试入口） | 中断条 + 重试（MessagePartRenderer.tsx:339-374） |
| `recommend_questions` | body（现状流式 null、末条底部渲染） | 现有挂载点 index.tsx:705-726 不动，仅归类防误收 |
| `todo` | body 原位保留（不进分组） | TodoBlock 是常驻展开进度卡（非 pill），prd Out of Scope |
| `token_usage` | 不渲染（现状即挂 message 上） | 仅供耗时数据源 |
| `reveal_file` / `reveal_project` 工具 | body（产物外置） | 媒体卡片 + 锚点（MessagePartRenderer.tsx:98-138），prd R4.2 |

实现形态：`partitionMessageParts(parts) → { processEntries, bodyEntries }`，条目携带 `{ part, partIndex }`（原始下标，见 §7 硬约束）。分区在 `ChatMessage` render 时以 `useMemo([parts])` 计算；parts 引用在流式中每次事件都会变，useMemo 依赖 parts 本身即可，不要自造深比较。

**穿插顺序的诚实处理**：过程 part 与 text part 在原始 parts 里是穿插的（research/start-check-stream-split.md §Q1 已证实）。分组聚合后，执行过程呈现为一个整块、正文按其相对顺序成块——这正是「正文主体化」的目标形态（参考图即如此），中间穿插的 text 会被整体归入正文区。不做「按 text 切分多段分组」的复杂化（见 §11 不采取的方案）。最常见形态（过程在前、结论在后）视觉与现状一致。

## 4. 分组组件 `ExecutionProcessGroup.tsx`

### 4.1 Props（消费已分区数据，不自己读 SSE/store）

```ts
interface ExecutionProcessGroupProps {
  messageId: string;
  processEntries: ProcessPartEntry[];  // 已分区保序（含原始 partIndex）
  isStreaming: boolean;                // message.isStreaming（sessionState 口径）
  durationMs?: number;                 // message.duration（token:usage）
  // —— 以下为组内渲染 MessagePartRenderer 必须全量透传的既有 props（见 §7）
  messagePartRendererProps: ...;       // 以 MessagePartRenderer.tsx:31-56 签名为准，执行时核对
}
```

组件内部从 processEntries 推导一切展示态（§5 选择器），不接收外部文案。展开区渲染分两条路径：

- **简洁行路径（默认）**：每个过程 part 渲染为一行简洁行（§4.3）。点击简洁行经 `openPersistentToolPanel` 打开已有侧栏（payload 复用各 Item 现成传参，见 §4.3 复用说明）；不可展开的步骤（无内容门控，如空目录）简洁行不可点。
- **既有渲染路径（需要完整交互的 part）**：`subagent` part 仍走 `MessagePartRenderer` 原样渲染（其卡片自带开面板逻辑，不做简洁行重写）；其余 part 类型按 §4.3 简洁行。组内经 `MessagePartRenderer` 渲染时必须逐一对齐透传其入参现状（`activePreview/onOpenPreview/allowAutoPreview/onRecommendQuestionClick/onRetryCancelled` 等），避免引入 undefined 行为——执行时核对该组件 props 签名（`MessagePartRenderer.tsx:31-56`）。

### 4.2 展开状态机（用户定稿）

```
effectiveExpanded(message) =
    userOverride[message.id]                     // 用户点过，以用户为准（含失败自动展开写入，见下）
  ?? (failedCount > 0 && !isStreaming)           // 完成且含失败 → 默认展开（R7.1）
  ?? isStreaming                                  // 流式默认展开，完成后默认收起
```

- 展开态读取顺序：覆盖态优先；无覆盖时，失败消息默认展开；否则跟随流式态。
- **失败自动展开写回覆盖态**：完成且含失败时把 `expanded=true` 写入同一覆盖态通道（不是另设机制），这样自动展开与用户选择同源，虚拟列表重挂后不丢（research/start-check-failed-locate.md §4 已证实局部 state 会丢）。用户随后手动收起即覆盖该值。
- 覆盖态实现：`createSingletonStore` 模式（state-management spec 的既有单例模式，参照 `persistentToolPanelState.tsx`），key 为 message.id，值为 `true/false`。会话内内存有效，不落 localStorage（prd Out of Scope）。
- 分组头点击：切换 effectiveExpanded 并把结果写入覆盖态。
- 自动收起（完成无失败）：不动覆盖态，只改默认推导，用户下次点开即被覆盖态接管。**不发起任何滚动**（prd R3.3 靠既有 `userScrolledUp` 四态机 + Virtuoso followOutput 自然行为；高度变化与既有折叠 pill 同类，research/start-check-stream-split.md §Q3 已证实）。

### 4.3 简洁行（compact row，用户定稿）

每行 = 能力图标 + 简洁文案 + 状态呈现（状态点/图标复用 `CollapsiblePill` 五态色形，不自创）。

- 文案来源（research/start-check-compact-steps.md §1 复用表，一步可达直接复用）：
  - read/write/edit：`toolRead/toolWrite/toolEdit + filePath`（各 Item pill 现成拼装）
  - grep：`toolSearch + pattern`；ls：`toolLs + basename`（取面板 title 形态，比 pill 全路径更短）；glob：`toolGlob + pattern`
  - execute：`toolExecute + command`（80 字符截断现成）
  - thinking：`thinking/thought`；summary：`summary`；sandbox：`chat.sandbox.*`
  - subagent：不做简洁行，走 §4.1 既有卡片路径
- **两处适配（已证实缺口，属本任务实现）**：
  1. 通用/MCP 工具简洁文案：label 现只有工具名，需新增 args 摘要规则——取首个有效字符串参数截断（冒号切分复用 `ToolCallItem.tsx:114-117` 逻辑）。
  2. sandbox 详情入口：现唯一无 `onPanelOpen`，简洁行点击需补 `openPersistentToolPanel`（title/children/footer 复用其现有三块文案）。
- 详情 payload：各 Item 现有的 `openPersistentToolPanel({...})` 传参（title/icon/status/subtitle/children/footer）已全部现成，简洁行透传同一 payload；其 `detailContent` 目前是各 Item 内部 const，简洁行组装时复用同一拼装（实现时以最小改动抽取或前移，不重写内容）。`canExpand=false` 时行不可点（沿用各 Item 门控）。

### 4.4 分组头

- 能力图标组（§5.3 选择器产出，上限 5 + `+N`）、标题文案（§5.4）、失败标识（§5.5）、chevron。
- 标题文案（i18n 新键，挂 `chat.message.*`，zh/en 同步）：
  - 执行中：`执行过程` + `已完成 X/Y 步`
  - 已完成：`查看执行过程` + `N 个步骤 · Xs`（耗时缺省则只显示步数）
  - 失败附加：` · F 步失败`（红色，F≥1 时）
- 样式：复用 `CollapsiblePill` 的圆角全圆 `h-7` 基调或 Todo 卡式边框二选一，实现时与现有消息区视觉对齐（纯白面/stone 边框/`--theme-*` 变量 + `dark:` 变体）；不新增暖色 token。

## 5. 选择器（`executionProcess.ts` 纯函数，单测锁定）

### 5.1 分区 `partitionMessageParts(parts)`

按 §3 表分两类，条目保留原始下标。草稿工具判定复用 `isScheduledTaskDraftToolName`（`items/scheduledTaskDraftResult.ts`）。

### 5.2 步数 `countProcessSteps(processEntries)`

- N = 过程条目数（thinking/tool/subagent/sandbox/summary 各 1）。子代理内部不递归。
- 执行中：X = 已结束条目数（非 pending 且非 streaming），Y = 当前过程条目总数。

### 5.3 能力图标 `summarizeCapabilities(processEntries)`

- 按顺序取 kind 去重，上限 5，超出计数 `+N`；kind 推导以 `MessagePartRenderer.tsx:80-247` 的 tool 名 if 链为事实依据。
- kind→图标映射（§2 复用表）：thinking `Brain`；read `FileText`；write `FilePlus`；edit `Pencil`；grep `Search`；glob `FolderSearch`；ls `FolderOpen`；execute `Terminal`；MCP（名含 `:`）`Globe`；其它工具 `Wrench`；subagent 用角色图标（单算，不递归）；sandbox `Box`；summary `FileText`。
- 图标映射表与文案拼装分离：纯函数只返 kind + 摘要参数，组件层 `t()` 落文案（便于 node:test 源码断言风格）。

### 5.4 当前动作 `getCurrentAction(processEntries)`

- 取最后一个未完成过程条目的简洁文案（简洁行同一套摘要规则）；无未完成条目时回退分组标题本身（不另造文案）。执行中分组展开区即呈现该行，无需猜「当前动作」新字段。

### 5.5 失败 `countFailedSteps(processEntries)`

- F = `success===false` 的 tool 条目数 + error 状态的 sandbox 条目数。供分组头失败标识与 §4.2 默认展开判定。

### 5.6 耗时 `resolveProcessDurationMs(message)`

- 优先 `message.duration`；缺失时回退 `max(completedAt)-min(startedAt)`（tool 条目有 ISO 时间戳，thinking/summary 无时间戳则忽略这些条目）；皆无返回 `undefined`。

## 6. 展开覆盖态

- 新建 `executionProcessPanelState.ts`（命名执行时以同目录既有 store 风格为准）：`createSingletonStore<Map<messageId, boolean>>` 或等效轻量模块，提供 `getProcessGroupOverride/setProcessGroupExpanded` + 订阅。
- 分组组件以 `useSyncExternalStore` 或既有订阅方式读覆盖态（参照 `persistentToolPanelState.tsx` 的订阅形态）。
- 失败自动展开：`useEffect` 在「完成且含失败且无用户覆盖」时写入 `expanded=true`（幂等，已写不重写，用户手动收起后不得被 effect 重新展开——写前判覆盖态是否已由用户设置；用独立的用户干预标记或以「仅当从未写入」为条件实现，执行时锁定一种并单测）。

## 7. 硬约束（核查衍生，implement 必须遵守）

1. 分区输入只能是顶层 `message.parts`；`subagent.parts[]` 不透明透传，**禁止展平**（research/start-check-stream-split.md §Q2）。
2. 组内/正文渲染 `MessagePartRenderer` 时必须传**原始 partIndex 与原始 isLast**（锚点 `createToolPartAnchorId`、自动预览 `shouldAllowAutoPreviewForPart`、流式光标全部依赖原始下标，见 research/start-check-stream-split.md §Q3）。分区条目携带原始下标即为此设。
3. 组内必须全量透传 `MessagePartRenderer` 现有 props，以 `MessagePartRenderer.tsx:31-56` 签名为准。
4. React key 不用过滤后下标：用 `${message.id}:${partIndex}` 等稳定标识（research/start-check-stream-split.md 附 4）。
5. 失败定位**不发起程序化滚动**；失败步 DOM 锚点复用 `tool-part:<owner>:<index>` 既有 scheme 即可（为将来定位预留，本任务不实现滚动定位）。

## 8. i18n

- 新增键挂 `chat.message.*`（zh/en 同步，键集对齐）：执行过程 / 查看执行过程 / 已完成 X/Y 步 / N 个步骤 / 耗时（复用既有 `duration` 或补秒/分秒键，实现时定）/ F 步失败 / `+N`。
- 收敛 3 处现成硬编码英文（research/start-check-compact-steps.md §3.2）：`GrepItem.tsx:100-104` 的 `in {path}`→`toolInPath`、`LsItem.tsx:65-67` 的 `{n} items`→`toolItemCount`、`GrepItem.tsx:131-135` 的 `+{n} more`→`toolMoreFiles`（仅在简洁行/面板文案经过处收敛，不做全仓文案治理）。

## 9. 复用清单（不重造）

| 能力 | 复用 |
|---|---|
| 侧栏宿主 | `openPersistentToolPanel`（items/persistentToolPanelState.tsx:72）+ ToolResultPanel |
| 状态色/形 | `CollapsiblePill` statusStyles/statusIconColors（五态） |
| 简洁文案与面板 payload | 各 Item 现成拼装（§4.3 表） |
| 子代理卡片 | SubagentBlock 原样（组内一行卡片） |
| 覆盖态模式 | createSingletonStore（state-management spec） |
| 锚点 scheme | messagePartAnchors.ts（`tool-part:<owner>:<index>`） |
| 计时 | useElapsedSeconds / computeDuration（ToolCallItem.tsx） |

## 10. 测试

- 新增 `__tests__/executionProcess.test.ts`（node:test 风格，与既有 `__tests__/*.test.ts` 同构）：分区（类型归类/草稿外置/产物外置/下标保留）、步数（执行中/完成同口径）、能力去重上限、失败计数、耗时回退、当前动作选取。
- 覆盖态与展开判定：失败自动展开幂等、用户覆盖优先，两例。
- 回归：消息滚动、工具面板、子代理块既有 node:test 套件；`pnpm lint`、`pnpm build`（tsc -b）通过。
- 手动验证（implement 收尾）：长任务流式→完成收起、上翻不被拽回、失败默认展开、移动端形态、暗色、历史消息同构。

## 11. 不采取的方案（防返工）

- ❌ 按 text 切多段分组（每段过程一个组）—— 与「整条消息一个过程」目标冲突，步骤数翻倍。
- ❌ 失败步骤单独提升到分组外 —— 用户已选「分组展开并标失败」；提升会重新平铺。
- ❌ 失败自动展开时程序化滚动 —— 与流式跟随/用户上翻冲突（research/start-check-failed-locate.md §4）。
- ❌ 展平 subagent 内部 parts 进组 —— 双重渲染与 panelKey 冲突。
- ❌ 用 `tool_call_id` 做定位/DOM 锚 —— 可缺失且有降级匹配（research/start-check-failed-locate.md §1）。
- ❌ 新建详情容器 / 暖色 token / 失败重试入口 —— 均 Out of Scope。

## 12. Rollback

- 改动集中在前端：`ChatMessage/index.tsx` 分区接入点、新增 3 个文件、i18n 两处 locale、既有 Item 两处小适配（通用工具摘要、sandbox 面板入口）+ 3 处文案收敛。回滚 = 还原 index.tsx 渲染路径并删除新增文件；parts 数据层零改动，无迁移风险。
