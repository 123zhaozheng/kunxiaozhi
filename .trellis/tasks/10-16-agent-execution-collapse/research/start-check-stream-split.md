# Research: 流式分区的分组风险核查（start-check-stream-split）

- **Query**: 新分组把一条消息 parts 分区（过程进组、正文在外）——核实流式中的 4 类风险
- **Scope**: internal（前端 `frontend/src` + 后端事件源 `src/infra`；无外部检索）
- **Date**: 2026-10-16
- **约定**: 每条结论标注【已证实】（读到代码原文）或【推测】（合理外推，需 implement 阶段验证）

---

## Q1. message:chunk 合并规则 / 交错序列 / 分组后正文位置 / 历史重建同一性

### 合并规则（顶层）

- 【已证实】顶层合并只看**相邻末位**，不看任何 id：
  - `frontend/src/hooks/useAgent/eventProcessor.ts:233-246` — depth==0 时，若 `lastPart?.type === "text" && !lastPart.depth` 则把 chunk 拼进末位 text 的 content，否则 push 一个新 `text` part；同时 `result.content = content + chunkContent`（:245，正文镜像拼在 `message.content` 上，分区不影响它）。
  - `frontend/src/hooks/useAgent/messageParts.ts:112-126`（`addPartToDepth` 的 `targetDepth <= 0` 分支）是同一规则的第二实现（条件含 `!lastPart.depth`，:116）。
- 【已证实】**新 text part 的产生条件**：当顶层最后一个 part 不是“无 depth 的 text”时。`tool:result` 是原位更新（`eventProcessor.ts:284-333`，`messageParts.ts:484-547`），不打断；但 `tool:start`（:252-282，顶层直接 append）、`agent:call`（:123-141，append subagent）、`thinking`（:158-210，push 新 thinking）都会让末位变成非 text，**下一个 chunk 必起新 text part**。
- 【已证实】后端 `message:chunk` 自带 `text_id`（`src/infra/writer/presenter_events.py:129-153`），用途注明“用于前端合并同一块的多个事件”；但**前端 `eventProcessor.ts:214-248` 完全忽略 `text_id`**，只做相邻合并。后果见下。

### 实时是否会出现「过程→正文→过程」交错

- 【已证实】会。典型序列：`text(chunk×n)` → `tool:start`（末位变 tool）→ `tool:result`（原位）→ `chunk`（末位是 tool → **新 text part**）→ `agent:call`/`tool:start`（又进过程）。即 parts 数组天然是 `text, tool, text, subagent, text…` 穿插形态，不是“一段过程 + 一段正文”两截。
- 【已证实】后端持久层还会先做一次合并：`MERGEABLE_EVENT_TYPES = {"message:chunk", "thinking"}`，按 `(event_type, agent_id, depth, thinking_id, text_id)` 分组（`src/infra/session/event_merger.py:7,37`）。历史页读到的是已按 `text_id` 合并过的 chunk 行，但经 `historyLoader` 重放仍走相邻合并，结果一致（见下）。

### 分组聚合后中间正文位置如何变

- 【已证实】按 `design.md §2-3` 的分区（`processParts` vs `bodyParts` 各保原序），穿插在中间的 text 会被整体抽到正文区，原“过程-正文-过程”交错点消失。这是设计已接受的取舍（`design.md:52` “穿插顺序的诚实处理”），本核查只确认机制：**位置变化一定发生，幅度 = 中间 text 块的数量与长度**；最常见形态（过程在前、结论 text 在后）视觉与现状一致。
- 【推测】流式中分组块与正文块的相对位置是稳定的（过程块在上、正文在下），因为分区是纯函数、每 render 按同一规则计算，不存在流式中途跳变；唯一动态是两块各自增高。

### 历史重建同一性

- 【已证实】历史重建走**同一** `processMessageEvent`（`frontend/src/hooks/useAgent/historyLoader.ts:310-318`，`isStreaming=false`），事件先经 `compareHistoryEvents` 全局排序（:82-114：`history_order` 四元组优先，其次 `seq`，最后 timestamp/trace/event_id），再由 `reconstructMessagesFromEvents`（:357-575）累积进同一 assistant 气泡（含 run 切分重贴逻辑 :380-391）。因此**只要分组只消费 `message.parts`，实时与历史自动同构**，无需分叉。
- 【已证实】一个差异点：`user:cancel` 历史路径把 `cancelled` part 追加在末尾（`historyLoader.ts:471-476`），而实时 cancel 经 `handleError` 路径（`eventHandlers.ts:491-527`）。位置语义一致（都在末尾），分区归类不受影响。
- 【推测】`event_merger` 的 `text_id` 合并 + 前端相邻合并双重作用下，最终 text 切分点在历史与实时可能差 1 个 part 边界（如实时是两个相邻 text，历史被合并成一个），但分区只看 type，不看个数，所以分组结果不受影响。

---

## Q2. depth>0 parts 的渲染路径 / 顶层分组是否误伤子代理 / 双重收纳

### depth>0 是否走同一 ChatMessage 渲染

- 【已证实】是，**同一个** `MessagePartRenderer`。文件头注释即“Render single message part (shared by main agent and subagent)”（`MessagePartRenderer.tsx:30`）；subagent 面板内递归调用在 `SubagentBlocks.tsx:527-536`（`SubagentPanelContent` 的 processing 折叠区内 `data.parts.map(<MessagePartRenderer/>)`）。
- 【已证实】depth>0 事件的路由：`eventProcessor.ts:218-232`（chunk）、`:170-178`（thinking）、`:268-276`（tool:start）全部进 `addPartToDepth`，按 `agent_id` 精确匹配 `isPending` 的 subagent 并写入其 `parts[]`（`messageParts.ts:143-222`，text 相邻合并 :153-163，thinking 按 `thinking_id` 累加 :164-192，todo upsert :193-201，summary 按 `summary_id` :202-214，递归找嵌套 subagent :224-238）；找不到匹配 subagent 时 warn 并落顶层（:241-250）。
- 【已证实】落顶层的 depth>0 text 带 `depth` 字段（`eventProcessor.ts:219-224`），而顶层合并条件要求 `!lastPart.depth`（`eventProcessor.ts:236`；`messageParts.ts:116`），所以**误落顶层的子代理文本不会与正文 text 合并**——天然隔离，这点对分组是好消息。

### 分组组件只接顶层 parts 会否误伤

- 【已证实】不会，**前提是分组只 partition `message.parts` 这一层**，把 `subagent` part 当不透明整体透传给 `MessagePartRenderer`（其 `subagent` 分支 :259-276 渲染 `SubagentBlock` 卡片，内部 `parts` 原样传入）。`SubagentBlock` 收到的 `parts` 引用不变，写入 `subagentPanelStore` 的 effect（`SubagentBlocks.tsx:685-744`）与面板内容都不受影响。
- 【已证实】红线：分组**绝不能把 `subagent.parts[]` 展平并入顶层过程集**，否则同一 part 既在组里又在面板里（`SubagentPanelContent` 读 store :410-571），造成重复渲染与 `panelKey` 冲突。`design.md §3` 已定 subagent“计 1 步，内部不展开计数”，与此一致。

### 是否双重收纳

- 【已证实】会形成“嵌套收纳”，但不是重复收纳：顶层分组收的是 `SubagentBlock` 卡片（一行，`SubagentBlocks.tsx:765-856`），卡片点击经 `handleOpenInPanel`（:752-763）开侧边面板，面板内 `processing` 折叠区（`SubagentBlocks.tsx:520-539`，`defaultExpanded={false}`）收的是 subagent **内部** parts。两层收的是不同层级的内容。
- 【推测】体验上可接受（卡片在组内仍是一行，点击行为不变）；唯一要注意的是组内 `SubagentBlock` 的 `openPersistentToolPanel` 调用与 `shouldAutoOpenSubagentPanel` 自动开面板逻辑（:709-726）都不感知分组，无需改动。

---

## Q3. Virtuoso 高度/测量、followOutput/atBottomThreshold、Items 的 onPanelOpen 与 autoPreviewEligibility

### Virtuoso 配置现状（文件:行号）

- 【已证实】`frontend/src/components/layout/AppContent/ChatView.tsx:512-527`：
  - `:517` `computeItemKey={(_, message) => message.id}` —— 项粒度是**整条消息**，分组只改变同一 item 的内部高度，不增删 item；
  - `:518` `atBottomStateChange={handleVirtuosoAtBottomChange}`；
  - `:519` `atBottomThreshold={getAtBottomThresholdPx(isMobileViewport)}`，阈值定义在 `frontend/src/components/layout/AppContent/messageScrollUtils.ts:70-72`：**移动端 120px，桌面 4px**；
  - `:520` `followOutput={"smooth"}`；
  - `:523` `itemContent={virtuosoItemContent}`（:360-384，渲染整条 `ChatMessage`）；
  - `:524-526` `initialTopMostItemIndex={getInitialBottomItemLocation(...)}`；
  - Footer 在 `:342-354`，含 `messagesEndRef` 哨兵 + `getMessageListFooterSpacerClass`（`messageScrollUtils.ts:116-122`），`forceVirtuosoToBottom`/`startVirtuosoScrollToBottom` 以 footer `scrollIntoView` 为兜底（`messageScrollUtils.ts:310-405`）。
- 【已证实】底部跟随的完整状态机在 `frontend/src/components/layout/AppContent/useMessageScroll.hook.ts:167-257`（atBottom 变化 → rAF 更新 `isNearBottomRef`/`userScrolledUpRef`；`requestScrollToBottom` 用 `forceVirtuosoToBottom` + `startVirtuosoScrollToBottom` 带 `observeLayoutChanges` 与 keep-alive）。

### 展开/收起/流式追加是否稳

- 【已证实】分组引入的高度变化与**既有折叠 pill 完全同类**：`CollapsiblePill` 内部 `useState(defaultExpanded)`（`frontend/src/components/common/CollapsiblePill.tsx:101`），展开/收起本就会改变所在 Virtuoso item 高度；Virtuoso 对 item 内部尺寸变化是自动重测的（`followOutput` + 内部测量），不需要手动调 `measure`/`resize`。
- 【推测】三点风险都在可接受范围，但 implement 应知晓：
  1. 桌面 `atBottomThreshold=4px` 很紧：流式追加 mostly 生长最后一项，`followOutput="smooth"` 只在已在底部时跟随；折叠/收起是用户手势，此时一般不在“跟随”语义内，与现有 pill 行为一致，不新增风险类别。
  2. 流式中组内持续增高（tool 结果回填、thinking 累加）与现状“pill 流式增高”相同；`startVirtuosoScrollToBottom` 的 `observeLayoutChanges`（`useMessageScroll.hook.ts:238`）本来就是为布局变化续跑而设。
  3. 子代理面板是独立滚动容器（`SubagentBlocks.tsx:503-570`，自带 `ResizeObserver` :478-495 + `subagentPanelScroll.ts`），不受顶层分组影响。

### onPanelOpen 是否受影响

- 【已证实】不受影响，前提是 props 透传。`CollapsiblePill.tsx:104-113`：有 `onPanelOpen` 则点击开面板、不做内联展开。传入 `onPanelOpen` 的调用方：`ToolCallItem.tsx:212`、`ReadFileItem.tsx:152`、`EditFileItem.tsx:139`、`WriteFileItem.tsx:109`、`GrepItem.tsx:184`、`LsItem.tsx:132`、`GlobItem.tsx:135`、`ExecuteItem.tsx:174`、`ThinkingBlock`（`SubagentBlocks.tsx:617`）、`SummaryItem.tsx:49`。分组复用 `MessagePartRenderer` 逐个渲染即自动保留；**分组组件本身不应拦截或重写 `onPanelOpen`**。
- 【已证实】`MessagePartRenderer` 的 related props（`allowAutoPreview/activePreview/onOpenPreview/onRecommendQuestionClick/onRetryCancelled`，签名 :31-56）目前由 `index.tsx:544-564` 逐个传入；分组内渲染必须**逐一对齐透传**，否则开面板/自动预览/重试入口在组内静默丢失。

### autoPreviewEligibility 与 partIndex 耦合（本节最重要的已证实风险）

- 【已证实】`shouldAllowAutoPreviewForPart` 按 `(messageId, partIndex)` 精确匹配（`autoPreviewEligibility.ts:176-186`）；`latestAutoPreview` 目标由 `getLatestAutoPreviewTarget` / `getLatestObservedCompletionAutoPreviewTarget` 扫描**原始 `message.parts`** 下标算出（:49-77，:121-129；`useRevealPreview.ts:257-266` 计算后经 `ChatView.tsx:243,370` → `ChatMessage` → `index.tsx:559-563` 按 `parts.map` 的 index 传入）。
- 【已证实】同一下标还用于 DOM 锚点：`createToolPartAnchorId(messageId, partIndex)`（`messagePartAnchors.ts:1-6`），`MessagePartRenderer.tsx:58-61` 仅 `reveal_file`/`reveal_project` 包锚点 div（:98-137）；外部导航反查按原始 parts 扫描（`useMessageScroll.externalNavigation.ts:481-524`）；text 的 `headingAnchorContext` 也带 partIndex（`MessagePartRenderer.tsx:68-75`，供 `useChatOutline`）。
- 【已证实 → 结论】若分组内用**过滤后数组的下标**当 `partIndex` 传给 `MessagePartRenderer`，组内 `reveal_file`/`reveal_project` 的自动预览（`allowAutoPreview` 恒 false）与外部导航锚点会错位。**分组必须传递原始 parts 下标**（partition 时保留 index，或渲染时回查），这是硬性要求，不是优化。
- 【已证实 → 相关】`isLast` 同理：`index.tsx:550` 以 `index === parts.length - 1` 传入，控制流式光标与 thinking/summary 的 `isStreaming && isLast` 门（`MessagePartRenderer.tsx:67,253,294,307`，流式 `recommend_questions` 返回 null :313-316）。分组内渲染必须沿用**原始 isLast**，不能按组内末位重算，否则组内末位 part 会错误显示流式态、或真正末位丢失流式光标。

---

## Q4. 「已完成」头 / 底部生成中 pill / cancelled 中断条 / scheduledTaskDraftCard 的安放

### 现状位置（文件:行号）

- 【已证实】**无「已完成」文字头**。消息头只有头像 + 名称 + 时间（`index.tsx:513-535`）；完成态 = 底部 pill 消失 + `RevealArtifactsSummary` 出现。`design.md` 的分组头“已完成·历时”文案是新增渲染，分区不涉及现有头。
- 【已证实】底部「生成中」pill 在 parts 容器**之外**（`index.tsx:610-621`，`message.isStreaming && !(isStreaming && !hasParts)`，`expandable={false}`），与分区正交。
- 【已证实】`cancelled` 中断条是 parts 流中的一个 part（`MessagePartRenderer.tsx:339-374`，含重试按钮 `onRetryCancelled`，`index.tsx:553-557` 按 `part.type === "cancelled"` 注入回调）；历史路径同样以 part 形式追加（`historyLoader.ts:475,484`）。
- 【已证实】`ScheduledTaskDraftCard` 是 `tool` part 的一个渲染分支（`MessagePartRenderer.tsx:225-234`：`isScheduledTaskDraftToolName && !isPending && success !== false` 且能 parse 才渲染卡片，否则回落通用 `ToolCallItem`）；`design.md:40` 已将其定为“外置（待确认）”。

### 最小风险建议（保原位 / 进组 / 外置）

| 元素 | 建议 | 依据 |
|---|---|---|
| 底部「生成中」pill（`index.tsx:610-621`） | **保原位**（不动） | 它在 parts 容器外，挂的是 `message.isStreaming` 整消息态；分区后流式/完成切换语义零变化 |
| `cancelled` 中断条 | **保原位随正文流**（归 body，不进组） | 重试入口 `onRetryCancelled` 按 part 类型注入（`index.tsx:554-556`）；进组会被折叠藏起，丢失唯一的重试入口（`cancelledRetry.ts` + `ChatView.tsx:281-295` 为全站唯一重试链） |
| `scheduledTaskDraftCard` | **外置原位渲染**（排除出过程集，渲染路径不变） | 确认/取消卡藏入折叠组会阻断确认流；`design.md:40` 与 prd R7.2 已定排除，分区函数只需按 `isScheduledTaskDraftToolName(part.name)` 识别 |
| `recommend_questions` | **保原位**（现有 `index.tsx:705-726` 挂载点不动，仅归类防误收） | 流式返回 null（`MessagePartRenderer:313-316`），只在末条底部渲染；进组无意义 |
| `todo` | **正文区原位保留**（不进组） | `TodoBlock` 常驻展开进度卡（`TodoBlock.tsx:32-100`），非 pill 语义；`design.md:47` 已定 Out of Scope |
| `RevealArtifactsSummary`（`index.tsx:567-571`） | **保原位**（parts 容器内、分组与正文之后） | 它消费全量 `message.parts` 做产物汇总，分区后数据源不变 |

---

## 附：implement 阶段必须遵守的硬性约束（核查衍生，非建议）

1. 分区输入只能是顶层 `message.parts`；`subagent.parts[]` 不透明透传，禁止展平（§Q2）。
2. 组内渲染 `MessagePartRenderer` 时必须传**原始 partIndex 与原始 isLast**（§Q3 锚点/自动预览/流式光标）。
3. 组内必须全量透传 `MessagePartRenderer` 现有 props（`activePreview/onOpenPreview/allowAutoPreview/onRecommendQuestionClick/onRetryCancelled`），以 `MessagePartRenderer.tsx:31-56` 签名为准（§Q3）。
4. `key` 建议用稳定标识而非过滤后下标：当前顶层用 `key={index}`（`index.tsx:545`）；分区重排后若仍用下标，展开/收起与流式追加时 React 易 remount（光标/面板态丢失）。【推测】风险等级中，需单测或手动验证。

## Caveats / 未覆盖

- 后端 chunk 发射的具体交错时序（如工具调用期间是否还会吐正文 chunk）未逐个 presenter 核实；本报告的交错结论来自前端合并规则的**结构必然性**（只要过程事件与 chunk 交替到达即产生多 text part），与具体时序无关。
- `text_id` 在前端被忽略是读代码确认的事实，但是否为有意设计（历史原因）未知；不影响分组结论。
- Virtuoso 对“折叠导致 item 高度骤减 + 用户恰好不在底部”时的跳变行为未实测，标为【推测】可接受（与既有 pill 同类）。
