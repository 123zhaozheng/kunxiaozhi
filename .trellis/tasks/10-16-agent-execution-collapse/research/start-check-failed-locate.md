# Research: 失败展开定位可行性核对（start-check）

- **Query**。整组展开定位失败步的可行性。核对锚点现状、滚动责任方、SubagentBlocks 展开层级、最小实现路径与 Virtuoso 风险。
- **Scope**。internal，纯前端。
- **Date**。2026-10-16
- **写入说明**。本内容应存为 `.trellis/tasks/10-16-agent-execution-collapse/research/start-check-failed-locate.md`，由主代理代存。子代理侧因约束未执行 Write。

## Findings（每条标注已证实/推测）

### 1. MessagePart / ToolPart 稳定 key / id 现状与失败定位锚点

已证实：

- `ToolPart.id` 是可选字段。`D:\code\python\LambChat\frontend\src\types\message.ts:127-141` 定义 `id?: string`。`SubagentPart.agent_id` 是必填 string，`D:\code\python\LambChat\frontend\src\types\message.ts:143-162`。`ThinkingPart.thinking_id` 可选，`message.ts:118-125`。
- `id` 来自 `tool_call_id`，但可能缺失。`D:\code\python\LambChat\frontend\src\hooks\useAgent\messageParts.ts:28-46`（`createToolPart` 中 `id: toolCallId`）；`D:\code\python\LambChat\frontend\src\hooks\useAgent\eventProcessor.ts:252-266`（`tool:start` 取 `data.tool_call_id as string | undefined`）。`tool:result` 在 `eventProcessor.ts:284-302` 同样容忍 undefined。
- 结果回填有两套降级匹配，证明 `tool_call_id` 不可作为唯一稳定锚。`messageParts.ts:484-547`（`updateToolResultInDepth` 先按 `p.id === toolCallId` 匹配，再按 `!p.id && p.isPending` 兜底）；`messageParts.ts:549-603`（递归版同样有无 id 兜底）；`eventProcessor.ts:303-323`（depth 0 且无 toolCallId 时按 `p.name === toolName && p.isPending` 匹配第一个 pending）。
- 现有锚点工具只有三个函数，全部基于 index / agentId，不基于 tool_call_id。`D:\code\python\LambChat\frontend\src\components\chat\ChatMessage\messagePartAnchors.ts:1-14`：`createToolPartAnchorId(ownerId, partIndex)` 得 `tool-part:${ownerId}:${partIndex}`；`createSubagentAnchorOwnerId(agentId)` 得 `subagent:${agentId}`；`createSubagentPanelKey(agentId)` 得 `subagent-${agentId}`。
- `messagePartKeys` 不存在。Grep `messagePartKeys|getPartKey|partKeys` 在 `frontend/src` 无命中。已证实缺失。任务背景中"现有 messagePartKeys"的前提不成立。
- React key 全用 index，不稳定。`D:\code\python\LambChat\frontend\src\components\chat\ChatMessage\index.tsx:545`（`key={index}`）；`D:\code\python\LambChat\frontend\src\components\chat\ChatMessage\SubagentBlocks.tsx:527-529`（`key={index}`）。Virtuoso 层才用稳定 key，`D:\code\python\LambChat\frontend\src\components\layout\AppContent\ChatView.tsx:517`（`computeItemKey={(_, message) => message.id}`）。
- 有 DOM `id` 的 tool 锚点目前只有 `reveal_file` / `reveal_project`。`MessagePartRenderer.tsx:98-101` 与 `120-123` 包了一层 `div id={toolPartAnchorId}`；其余 `read_file`、`edit_file`、`write_file`、`grep`、`ls`、`glob`、`execute`、通用 `ToolCallItem` 分支（`MessagePartRenderer.tsx:80-246`）均无 `id`。结论。失败定位不能直接复用现有锚点，必须给失败步补 `id`。
- 子代理内部 part 的 owner 方案已存在，可照抄。`SubagentBlocks.tsx:527-536` 以 `messageId={createSubagentAnchorOwnerId(agentId)}` + `partIndex={index}` 渲染内部 `MessagePartRenderer`，即嵌套锚点形如 `tool-part:subagent:<agentId>:<index>`。外部导航的递归查找与此同构，`D:\code\python\LambChat\frontend\src\components\layout\AppContent\useMessageScroll.externalNavigation.ts:481-524`（`findRevealPartMatchInParts` 递归进 `part.parts` 并拼 `subagentChain`）。

推测：

- index 锚点在 parts append-only 假设下基本稳定。`messageParts.ts:112-126`、`219-221`、`240-250` 均为追加或原位替换，未见头部插入或重排。text/thinking 合并只改 content 不改顺序。但流式中"失败步的 index"是在结果到达时才确定的， paranoia。实现时应以"失败判定时刻的 partIndex 快照 + message.id"为锚，不要长期缓存 index。标为推测，因未逐条审计全部事件分支。

### 2. 滚动由谁负责，会不会和主 Virtuoso 跟随冲突

已证实：

- 子代理面板的滚动只是"跟底"，没有"定位到某步"。`SubagentBlocks.tsx:503-508` 是面板内独立滚动容器（`overflow-y-auto`）；跟底触发在 `SubagentBlocks.tsx:459-470`（`useLayoutEffect` + `shouldAutoScrollSubagentPanel`）和 `472-495`（`ResizeObserver` 监听 scroller + content）；滚动执行体是 `D:\code\python\LambChat\frontend\src\components\chat\ChatMessage\subagentPanelScroll.ts:56-114`（`startSubagentPanelScrollToBottom`，30ms 轮询至多 20 次），落点是底部 footer（`subagentPanelScroll.ts:52` 的 `footer?.scrollIntoView` + `scrollTop = scrollHeight`）。没有任何 scrollToIndex / scroll 到指定 part 的代码。
- 主列表是 Virtuoso，按 message 粒度虚拟化。`ChatView.tsx:512-527`：`followOutput={"smooth"}`（520）、`atBottomStateChange`（518）、`computeItemKey=message.id`（517）。底部跟随循环在 `D:\code\python\LambChat\frontend\src\components\layout\AppContent\messageScrollUtils.ts:349-575`（`startVirtuosoScrollToBottom`，含高度稳定判定与 `keepAliveWhile` 流式保活），由 `D:\code\python\LambChat\frontend\src\components\layout\AppContent\useMessageScroll.hook.ts:188-257`（`requestScrollToBottom`）驱动，以 `userScrolledUp` 为 abort 条件（247）。
- 全站唯一"滚动到某 part"的现成路径是外部导航（文件 reveal 跳转），在 `useMessageScroll.hook.ts:702-807`。其冲突处理是先断跟随再滚动。`useMessageScroll.hook.ts:709-713`（`userScrolledUp=true`、`autoScrollActive=false`、`streamLockActive=false`），再 `useMessageScroll.hook.ts:759-766`（`virtuoso.scrollToIndex` 到消息），最后 `useMessageScroll.hook.ts:773-801`（`scrollElementIntoViewWithRetries` 到精确锚点 + `highlightElementForExternalNavigation` + `focusElementForExternalNavigation`）。
- 精确滚动原语可复用。`useMessageScroll.externalNavigation.ts:112-191`（`scrollElementIntoViewWithRetries`，重试 12-36 次、对 Virtuoso 高度未稳定做了 settle 容忍）；`193-218`（`highlightElementForExternalNavigation`，`data-external-navigation-highlighted` 1600ms）；`296-316`（`ensureSubagentPanelsOpen`，按 `subagentChain` 逐层调 `openSubagentPanelByAgentId`）。注意 `791-794`。有 `subagentChain` 时不传主 scroller，退化为 `element.scrollIntoView`（168 行），即面板内定位靠原生 scrollIntoView，不经过 Virtuoso。
- 高亮样式已预埋在 part 层。`MessagePartRenderer.tsx:100` 与 `122` 的 reveal 包裹 div 有 `data-[external-navigation-highlighted=true]:ring-2 ... ring-amber-500/80`；`index.tsx:471` 与 `506` 的消息容器同理。失败高亮可复用该属性通道，但失败步的包裹 div 目前不存在（见 §1），需新建。
- 大纲跳转是"Virtuoso 先行 + 原生兜底"的第二例证。`D:\code\python\LambChat\frontend\src\components\layout\AppContent\useChatOutline.tsx:55-74`（`scrollToIndex` 后 `requestAnimationFrame` 里 `document.getElementById` + `scrollIntoView smooth`）。

已证实的风险（会冲突，条件明确）：

- 流式执行中对失败步做 smooth 滚动，会与 `followOutput="smooth"` + `startVirtuosoScrollToBottom` 的保活循环打架（`useMessageScroll.hook.ts:245-246` 的 `keepAliveWhile` 在流式期间持续把视图往底拽）。外部导航的解法是直接把用户标记为"已上翻"并停掉自动跟随（709-713），失败定位若要滚动必须同样先断跟随，否则来回拉扯。用户定稿是"整组展开定位失败步"，若在流式中途（失败刚出现但消息仍在 streaming）就滚动，必然触发此冲突。最小路径应避免在流式中滚动。

### 3. 失败 step 在 SubagentBlocks / CollapsibleSection 里要展开哪些层级

已证实（默认值逐层列出）：

- L0 新分组（ExecutionProcessGroup，尚不存在，按 `design.md` 落在 `index.tsx:540-572` 插入）。其默认展开态按 prd R1.4。执行中展开、完成后收起。`prd.md:35`。
- L1 SubagentBlock 卡片本身不可"展开"，只有"开面板"。`SubagentBlocks.tsx:765-856` 的卡片是固定一行（图标 + 名 + input 摘要 + 右上状态点），点击走 `handleOpenInPanel`（`SubagentBlocks.tsx:752-763`）调 `openPersistentToolPanel`。内部 parts 只在侧栏 `SubagentPanelContent` 里渲染，不在消息流内联。因此嵌套失败的定位必经"开侧栏"这一步，不存在"在消息流内逐层展开 subagent"的路径。
- L2 面板内部分区默认值。`SubagentBlocks.tsx:510-519`（args 区，`CollapsibleSection` 默认 `defaultExpanded=true`）；`520-539`（processing 区，显式 `defaultExpanded={false}`，唯一默认折叠）；`540-550`（error 区，无 defaultExpanded 即 true，默认展开）；`551-560`（result 区，默认展开）。`CollapsibleSection` 默认值定义在 `SubagentBlocks.tsx:346-359`（`defaultExpanded = true`，局部 `useState`）。
- L3 失败 tool pill 自身。`D:\code\python\LambChat\frontend\src\components\chat\ChatMessage\ToolCallItem.tsx:137-146` 状态机。`isPending→loading`、`cancelled→cancelled`、`success→success`、`hasResult→error`。即 `success===false` 且有 result 时 pill 头直接红色，无需展开即可被看见。详情（args/result 内联区 `ToolCallItem.tsx:224-265`，或侧栏 `panelContent` `150-189` 经 `openPersistentToolPanel` 在 `212-222` 打开）才需要展开。
- 顶层失败（tool/subagent/sandbox 直接挂在 message.parts 下）。只需开 L0 新分组。失败 pill 头的红色状态在分组展开后直接可见，无需再开 pill。
- 嵌套失败（tool 挂在 subagent.parts 里）。需开 L0 分组 + L1 开侧栏（`openSubagentPanelByAgentId`，`SubagentBlocks.tsx:301-326`）+ L2 展开 processing 区（默认折叠，唯一必须动手的一层）。error 区默认展开，但注意它是 subagent 自身的 `data.error`（`SubagentBlocks.tsx:540-550` 的条件是 `data.error && effectiveStatus === "error"`），内部 tool 的 error 文本不在这里，在 pill 内。
- `ensureSubagentPanelsOpen` 已证明"L1 可程序化开启"。`useMessageScroll.externalNavigation.ts:296-316` 按 chain 逐个 `openSubagentPanelByAgentId`，失败定位可复用该函数或其模式。但 processing 区（L2）的程序化展开目前无任何代码。已证实缺失。`CollapsibleSection` 只有内部 `useState`（359 行），无受控 props、无 store。

推测：

- `CollapsiblePill` 的默认折叠行为本次未逐行读（`frontend/src/components/common/CollapsiblePill.tsx` 存在但未打开），其"默认折叠、局部 state、无持久化"的结论引自本任务既有 `research/render-pipeline.md:85-86`。标为转引既有研究，非本次重证。

### 4. 结论。最小实现路径与 Virtuoso 卸载风险

已证实结论：

- 最小路径是"只开顶层分组 + 高亮，不主动滚动"（失败在首屏常见情形下零滚动即可定位）。依据。失败 pill 头红色已足够外显（`ToolCallItem.tsx:137-146`）；subagent 卡片 error 态自带红边与红字（`SubagentBlocks.tsx:774-775`、`839`）；高亮通道现成（`highlightElementForExternalNavigation` + 两处 amber ring 样式）。滚动只在"失败步不在可视区"时触发，且必须复用外部导航的断跟随三件套（`useMessageScroll.hook.ts:709-713`）。
- 次选才是"滚动或仅高亮"的条件分支，而非二选一写死为滚动。流式中（`message.isStreaming`）只展开 + 高亮、不滚动；消息完成（`isStreaming false`，`didLatestStreamingAssistantFinish` 在 `messageScrollUtils.ts:156-173` 有现成判定）且用户在底部跟随时，可跟随自然到底，无需程序化滚动；仅当用户已上翻（`userScrolledUp=true`）且失败不在可视区时，才提供"定位到失败"按钮式的一次性滚动（复用 `scrollElementIntoViewWithRetries` + `scrollToIndex` 先行模式）。
- 锚点最小补齐。每类失败步的包裹层补 `id` 即可，命名沿用现有 scheme。顶层 `tool-part:<message.id>:<partIndex>`；嵌套 `tool-part:subagent:<agentId>:<partIndex>`；subagent 自身可用 `createSubagentPanelKey` / anchor owner（`messagePartAnchors.ts:8-13`）。不要用 `tool_call_id` 做 DOM id。它可缺失、有降级匹配（§1），且含不可预期的字符与重复风险。
- Virtuoso 卸载风险真实存在，落在"展开态"而非"锚点"。`CollapsibleSection` 状态是局部 `useState`（`SubagentBlocks.tsx:359`），无 store、无持久化（本任务 `research/state-history.md:68-74` 已证实 chat 目录无按 message.id 的展开存储）。Virtuoso 卸载重挂会重建组件，局部展开态丢失。prd R3.1/R3.4 要求的新建"按 message id 的内存覆盖态"（`prd.md:48-51`）正是解药，但目前不存在。已证实缺失。因此"失败自动展开"必须写进该覆盖态（否则用户一切会话、一切滚动都可能丢态），而不能只靠 `defaultExpanded` 或局部 state。

推测（标明，需实现时专项验证）：

- 远距离失败消息被 Virtuoso 卸载时，`document.getElementById(anchorId)` 为 null，重试循环（`maxAttempts 24`，有 chain 时 36）大概率能等到 `scrollToIndex` 渲染完成，依据是外部导航同一机制。但失败定位的"先开分组再取锚点"多了一拍状态更新，首帧大概率取不到，比外部导航更依赖重试。建议实现时把"开分组"放进 `getElement` 回调内部（效仿 `ensureSubagentPanelsOpen` 在 `useMessageScroll.hook.ts:775` 的放法），而不是先开完再启动滚动。

## Caveats / Not Found

- `messagePartKeys` 不存在（frontend/src 无命中）。背景前提需修正。
- `CollapsibleSection` 的程序化展开接口不存在。需新增受控能力或 refs 方案。
- 按 message.id 的展开覆盖态 store 不存在（state-history §5 已证实）。失败自动展开必须依赖它的新建。
- `CollapsiblePill.tsx` 本体本次未重读，默认折叠结论转引自本任务既有 render-pipeline 研究。
- 后端失败字段侧（`success===false` 的产生条件、`error` 事件）本次未查，沿用 state-history §5 的结论（`ToolCallItem.tsx:137-146` 渲染口径 + 无 `tool:timeout` 事件 + 无失败重试入口）。

## 相关 Spec / 任务文件

- `D:\code\python\LambChat\.trellis\tasks\10-16-agent-execution-collapse\prd.md:31-35,46-51,70-73`（R1.4 默认展开、R3 覆盖态、R7.1 失败外显二选一）
- `D:\code\python\LambChat\.trellis\tasks\10-16-agent-execution-collapse\design.md:50-75`（分区与分组 props，失败子集引用设计）
- `D:\code\python\LambChat\.trellis\tasks\10-16-agent-execution-collapse\research\render-pipeline.md:85-93`（折叠机制与 SubagentBlock 收纳雏形）
- `D:\code\python\LambChat\.trellis\tasks\10-16-agent-execution-collapse\research\state-history.md:48-74`（失败渲染口径、展开持久化缺失）
- `D:\code\python\LambChat\.trellis\tasks\10-16-agent-execution-collapse\research\icons-style-scroll.md:67-74`（Virtuoso 跟随机制）
