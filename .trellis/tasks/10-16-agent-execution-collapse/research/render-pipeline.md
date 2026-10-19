# Research: 前端 Agent 消息渲染管线与现有折叠行为

- **Query**: 前端「Agent 消息渲染管线与现有折叠行为」（技术栈、SSE→block 链路、折叠机制、正文/过程分区、消息头/错误/审批、流式当前步骤）
- **Scope**: internal（全部结论来自本仓库前端源码；无外部检索）
- **Date**: 2026-10-16

## Findings

### 1. 前端技术栈与目录（已证实）

| 结论 | 证据 |
|---|---|
| 框架：React 19.2.5 + react-dom 19.2.5，构建 Vite，包管理 pnpm | `frontend/package.json:70-71`（`"react": "19.2.5"`, `"react-dom": "19.2.5"`），scripts 含 `dev: vite` / `build: tsc -b && vite build` |
| 样式：Tailwind CSS 3.4.17 + clsx + tailwind-merge；主题色走 CSS 变量（`var(--theme-*)`） | `frontend/package.json:88,112`（`tailwind-merge`, `tailwindcss: ^3.4.17`）；如 `frontend/src/components/chat/ChatMessage/index.tsx:521` 使用 `style={{ color: "var(--theme-text)" }}` |
| 图标库：lucide-react ^0.468.0（Wrench/Globe/Brain/Bot/Box/Copy 等全部来自 lucide） | `frontend/package.json:64`；如 `frontend/src/components/chat/ChatMessage/ToolCallItem.tsx:1`，`SubagentBlocks.tsx:10-29` |
| Markdown：react-markdown ^9.0.1 + remark-gfm/remark-breaks（ApprovalPanel 直接引入） | `frontend/package.json:76`，`frontend/src/components/panels/ApprovalPanel.tsx:12-14` |
| 列表虚拟化：react-virtuoso ^4.18.3 渲染消息流 | `frontend/package.json:80`，`frontend/src/components/layout/AppContent/ChatView.tsx:512-528`（`<Virtuoso data={messages} itemContent={...}>`） |
| 无 zustand/jotai/redux：面板状态用自研 singleton store（createSingletonStore + subscribe） | `frontend/package.json` 无 zustand/jotai/redux 依赖；`frontend/src/components/chat/ChatMessage/items/createSingletonStore.ts`；面板 store `frontend/src/components/chat/ChatMessage/items/persistentToolPanelState.tsx:36` |
| i18n：react-i18next，中文文案在 `frontend/src/i18n/locales/zh.json` | 状态文案见 §5 表格 |

聊天界面入口组件文件路径（已证实）：

| 层级 | 文件 |
|---|---|
| 聊天视图（消息列表 + 输入框 + 审批/SOP/预览宿主装配） | `frontend/src/components/layout/AppContent/ChatView.tsx`（Virtuoso 列表 :512-528；ApprovalPanel :531-535；SopBlock :539-546；RevealPreviewHost/PersistentToolPanelHost :548-554） |
| 单条消息（用户 bubble / 助手全文布局） | `frontend/src/components/chat/ChatMessage/index.tsx`（`ChatMessage` memo 组件 :432） |
| part 分发器 | `frontend/src/components/chat/ChatMessage/MessagePartRenderer.tsx:32`（`MessagePartRenderer`） |
| 状态推导（是否 running） | `frontend/src/components/layout/AppContent/sessionState.ts:5-10`（`isSessionRunning`）；`ChatView.tsx:124` 消费 |

### 2. SSE/流式事件 → parts → block 完整链路（已证实）

数据流：`useAgent` SSE（`sseConnection.ts`）→ `handleStreamEvent`（`eventHandlers.ts`）→ `processMessageEvent`（`eventProcessor.ts`）操作 `Message.parts: MessagePart[]` → `ChatMessage` 按序 map → `MessagePartRenderer` 按 `part.type` / `tool.name` 分发到专用组件。历史加载走同一 `processMessageEvent`（`historyLoader.ts:310`，`isStreaming=false`）。

#### 2.1 后端 SSE 事件类型（已证实）

`frontend/src/hooks/useAgent/types.ts:13-39` 定义 `EventType` 共 25 种：`metadata` / `message:chunk` / `user:message` / `user:cancel` / `thinking` / `tool:start` / `tool:result` / `todo:updated` / `sop:updated` / `summary` / `recommend:questions` / `followup:questions` / `agent:call` / `agent:result` / `approval_required` / `sandbox:starting` / `sandbox:ready` / `sandbox:error` / `token:usage` / `skills:changed` / `queue_update` / `goal:start` / `goal:end` / `complete` / `done` / `error`。

其中真正产生/变更 message parts 的（`eventHandlers.ts:307-323` 的 `MESSAGE_EVENTS` 集合）：`agent:call` / `agent:result` / `thinking` / `message:chunk` / `tool:start` / `tool:result` / `sandbox:starting|ready|error` / `token:usage` / `todo:updated` / `summary` / `recommend:questions` / `followup:questions` / `error`。其余走副作用分支（`eventHandlers.ts:129-299`）：`metadata`（仅设 sessionId）、`goal:start|end`（写 `activeGoal`/`goalsByRunId`）、`complete|done`（`isStreaming=false` + `clearAllLoadingStates` + 标记已读，:226-247）、`queue_update`（toast 提示，无 parts，:249-257）、`approval_required`（调 `handleApprovalRequired` 或 SOP 拦截，见 §5）、`sop:updated`（写 `sopPlan` state，不进 parts，:275-282）、`skills:changed`（toast 回调）。

前端 `MessagePart` 类型共 10 种（`frontend/src/types/message.ts:34-44`）：`text` / `tool` / `subagent` / `thinking` / `sandbox` / `token_usage` / `cancelled` / `todo` / `summary` / `recommend_questions`。

#### 2.2 事件 → part → 渲染组件对照表（已证实，除特别标注）

| SSE 事件 | 生成/更新的 part（`eventProcessor.ts` 行号） | 渲染组件（文件:行号） |
|---|---|---|
| `message:chunk`（顶层合并相邻 text；depth>0 路由进 subagent） | `text`（`eventProcessor.ts:214-248`，`messageParts.ts:104-126` 合并逻辑） | `MarkdownContent`（`MessagePartRenderer.tsx:63-78` → `MarkdownContent.tsx`） |
| `thinking`（按 `thinking_id` 累加拼接） | `thinking`（`eventProcessor.ts:158-210`） | `ThinkingBlock`（`SubagentBlocks.tsx:578-632`；`MessagePartRenderer.tsx:249-257`；`panelKey=thinking_id`） |
| `tool:start`（`data.tool` 为工具名，`tool_call_id` 为 id；顶层直接 append，depth>0 进 subagent） | `tool`（`isPending:true`，`eventProcessor.ts:252-282`；`createToolPart` 在 `messageParts.ts:28-46`） | 见下表「工具名 → 专用组件」（`MessagePartRenderer.tsx:80-247` 的 if 链） |
| `tool:result`（按 `tool_call_id` 匹配 `isPending` part，写 `result/success/error/isPending:false`） | 更新 `tool`（`eventProcessor.ts:284-333`；`messageParts.ts:484-603`；注意无 id 时按 name 回退匹配 :510, :574） | 同上（同一组件由 loading 翻转为 success/error） |
| `agent:call`（depth=该 subagent depth；入 `activeSubagentStack`） | `subagent`（`status:running,isPending:true,parts:[]`，`eventProcessor.ts:123-141`；`messageParts.ts:71-92`；`eventHandlers.ts:333-336` push 栈） | `SubagentBlock`（`SubagentBlocks.tsx:635-857`；`MessagePartRenderer.tsx:259-276`） |
| `agent:result`（出栈，写 result/success/status） | 更新 `subagent`（`eventProcessor.ts:143-154`；`messageParts.ts:370-421`；`eventHandlers.ts:380-388` pop 栈） | 同上 |
| depth>0 的 thinking/chunk/tool/todo/summary | 路由进匹配 subagent 的 `parts[]`（`addPartToDepth`，`messageParts.ts:104-251`；找不到时 warn 并落顶层 :241-250） | `SubagentPanelContent` 内递归 `MessagePartRenderer`（`SubagentBlocks.tsx:527-536`，默认折叠的「处理过程」区） |
| `sandbox:starting|ready|error`（upsert，全局最多一个） | `sandbox`（`eventProcessor.ts:337-368` + `upsertSandboxPart :511-518`） | `SandboxItem`（`SubagentBlocks.tsx:860-922`；`MessagePartRenderer.tsx:279-287`） |
| `todo:updated`（顶层 upsert 替换；depth>0 进 subagent） | `todo`（`eventProcessor.ts:391-408` + `upsertTodoPart :521-528`） | `TodoBlock`（`TodoBlock.tsx:32-100`；`MessagePartRenderer.tsx:290-297`）**常驻展开卡片，非 pill** |
| `summary`（按 `summary_id` 累加） | `summary`（`eventProcessor.ts:412-456`；`findSummaryIndex` 在 `messageParts.ts:357-365`） | `SummaryItem`（`SummaryItem.tsx:13-65`；`MessagePartRenderer.tsx:300-311`；`panelKey=summary:{agent}:{depth}:{summary_id}` :301-303） |
| `recommend:questions` / `followup:questions`（upsert；流式中不渲染） | `recommend_questions`（`eventProcessor.ts:460-485`） | 流式返回 null（`MessagePartRenderer.tsx:313-316`）；结束后仅最后一条消息底部渲染为可点击问题按钮组（`index.tsx:705-726` + `MessagePartRenderer.tsx:318-336`） |
| `token:usage`（不进 parts，挂 `message.tokenUsage/duration`） | —（`eventProcessor.ts:372-385`；`eventHandlers.ts:364-369` 写回 message） | 右下角 Info 按钮 + popup（`index.tsx:95-250` `TokenDetailsButton`，:667-675 挂载；`resolveTokenUsageModelDetails` 在 `tokenUsageModel.ts`） |
| `approval_required`（type=form 走审批流；sop_plan 被拦截） | 不进 parts | 通用表单走 `ApprovalPanel`（ChatView 底部 :531-535，见 §5）；`sop_plan` 走 `SopBlock` DAG 卡片（:539-546） |
| `error`（流式时 `clearAllLoadingStates`；Cancelled 置 `cancelled`） | `clearAllLoadingStates`（`messageParts.ts:615-670`；`eventProcessor.ts:489-500`） | `cancelled` part → 中断条 + 重试按钮（`MessagePartRenderer.tsx:339-374`，`onRetryCancelled` 来自 `index.tsx:553-557`）；非取消错误直接拼 `message.content = "…errorPrefix"`（`eventProcessor.ts:497`） |
| `complete`/`done`（结束） | `clearAllLoadingStates` 全清（`eventHandlers.ts:228-238`） | 底部 `CollapsiblePill generating` 消失（`index.tsx:610-621`）；`RevealArtifactsSummary` 出现（见 §4） |

#### 2.3 工具名 → 专用渲染组件（已证实，`MessagePartRenderer.tsx:80-247`）

| 工具名 | 组件 | 文件 |
|---|---|---|
| `read_file` | `ReadFileItem`（去行号、显文件路径） | `items/ReadFileItem.tsx:15` |
| `reveal_file` | `FileRevealItem`（图片/视频/文档产物卡片，anchor 高亮 wrapper :98-116） | `items/FileRevealItem.tsx:77` |
| `reveal_project` | `ProjectRevealItem` | `items/ProjectRevealItem.tsx` |
| `edit_file` | `EditFileItem` | `items/EditFileItem.tsx` |
| `write_file` | `WriteFileItem` | `items/WriteFileItem.tsx` |
| `grep` | `GrepItem` | `items/GrepItem.tsx` |
| `ls` | `LsItem` | `items/LsItem.tsx` |
| `glob` | `GlobItem` | `items/GlobItem.tsx` |
| `execute` | `ExecuteItem` | `items/ExecuteItem.tsx` |
| 定时任务草稿工具（`isScheduledTaskDraftToolName`，完成后且成功） | `ScheduledTaskDraftCard`（确认/取消卡片） | `items/ScheduledTaskDraftCard.tsx`（分发 `MessagePartRenderer.tsx:225-234`） |
| 其他一切（含 `server:tool` 格式 MCP 工具，`:` 切 server 名） | 通用 `ToolCallItem`（MCP 工具显示 Globe 图标 + server 后缀，`ToolCallItem.tsx:113-122`） | `ToolCallItem.tsx:79` |

图片/文件结果渲染层级（已证实）：工具结果内的图片走 `ToolResultContent`（`items/McpBlockPreview.tsx:267`）：LangChain blocks 数组（:302-339）、MCP `{text, blocks}`（:341-373）、`result.images[]` 生成图（`extractGeneratedImageResults`，`items/toolImageResults.ts:42`，经 `getFullUrl` 解析，:53）→ `GeneratedImageResults`；富文本 `dict{title/url/content}`（:380-389）。`reveal_file` 结果（`{key,url,name}` 新格式 / `file_reveal` 旧格式）解析后渲染为媒体卡片（`FileRevealItem.tsx:125-172` 解析，`ImageViewer`/`VideoViewer` 预览，`sessionImageGallery` 注册 :123）。

### 3. 现有折叠/展开机制（已证实）

核心原子组件：`CollapsiblePill`（`frontend/src/components/common/CollapsiblePill.tsx:87-164`）。

- **默认状态**：`defaultExpanded=false`（:93），即**所有 pill 默认折叠**，只占一行（`inline-flex … h-7 rounded-full` pill 按钮，:129）。
- **展开状态保存位置**：`useState(defaultExpanded)` 组件**局部 state**（:101），`onExpandChange` 可选外发（:111），**无全局 store、无持久化**（刷新/重挂载即丢失；列表虚拟化卸载重建也会重置——推测，未实测，标为推测）。
- **为什么每一步仍占用一行**：`ChatMessage`（`index.tsx:541-566`）把 `message.parts` **逐个** map 为 `MessagePartRenderer`，每个 tool/thinking/summary/sandbox part 各渲染**一个** `CollapsiblePill`（`my-1` 上下 margin，`CollapsiblePill.tsx:125`），thinking/summary/thinking 等占一行、tool 占一行、subagent 占一个卡片——**不存在分组容器**，N 个步骤 = N 行 pill。这是现状逐条展开的直接原因（已证实）。
- **折叠变体**：`CollapsiblePill` 有 `variant: default|tool|thinking|summary`（:12）但实际样式函数忽略 variant（`_variant`，:94），颜色只由 `status` 决定（`statusStyles` :33-42）：loading=amber、success=emerald、error=red、cancelled/idle=stone。
- **两种展开形态**（已证实）：
  1. 内联展开：`isExpanded && children`（:159-161），如 `ToolCallItem` 的 args/result 内联区（`ToolCallItem.tsx:224-265`，`max-h-96 overflow-y-auto`，左侧竖线）。
  2. 侧边面板（`onPanelOpen` 优先于内联，`CollapsiblePill.tsx:106-109`）：`openPersistentToolPanel` 打开右侧 `ToolResultPanel` 侧边栏（`items/persistentToolPanelState.tsx:72-78`；`ToolResultPanel` 默认 `viewMode=sidebar`，`ToolResultPanel.tsx:139-140`，可切 center 全屏）。**桌面端点击 pill 一律走面板**：`ToolCallItem`（:212-222）、`ThinkingBlock`（`SubagentBlocks.tsx:617-629`）、`SummaryItem`（`SummaryItem.tsx:49-61`）都传了 `onPanelOpen`，因此其 `children` 内联分支实际不可达；面板内容更新靠 `updatePersistentToolPanel` + `isPersistentToolPanelOpen(panelKey)`（`SummaryItem.tsx:27-40`，`SubagentBlocks.tsx:591-605/701-708`）。
- **移动端**：`openPersistentToolPanel` 中 `panel.auto && isMobileDevice()` 直接丢弃（`persistentToolPanelState.tsx:73`）；ToolResultPanel 移动端为 bottom-sheet（`ToolResultPanel.tsx` 有 mobile bottom-sheet 注释与 `mobileFillViewport` 选项，`persistentToolPanelState.tsx:33`）。
- **`CollapsibleSection`**（`SubagentBlocks.tsx:346-404`）：面板内部用的标题+Chevron 折叠块，`defaultExpanded=true`（:348）**默认展开**（与 pill 相反），用于面板内 args/result/error 分区；`TodoBlock` 头部进度条区不折叠、任务列表常显（`TodoBlock.tsx:92-98`，`max-h-60` 滚动）。
- **`SubagentBlock`**（`SubagentBlocks.tsx:765-856`）：非 pill，是紧凑卡片（一行：角色图标+名+input 摘要+右上状态圆点），点击 `handleOpenInPanel`（:752-763）在侧边面板看内部递归 parts；内部 `SubagentPanelContent`（:410-571）里 input 默认展开、**内部过程区 `processing` 默认折叠**（:520-539 `defaultExpanded={false}`）、result 默认展开。是全站唯一已存在的「过程收纳」雏形。
- **其他折叠点**：`SandboxItem` 用 `CollapsiblePill` + 局部 `isExpanded`（`SubagentBlocks.tsx:870,899`）；产物汇总 `RevealArtifactsSummary` 本身不折叠（整宽卡片，点击开 `panelKey=reveal-artifacts` 全文件面板，:434-503）；文件树目录 `TreeDirRow` 局部 `expanded` 默认收起（`RevealArtifactsSummary.tsx:180,483`）。

### 4. 正文区 vs 过程区；产物卡片位置（已证实）

- **正文区**：`text` part → `MarkdownContent` 全宽渲染（`MessagePartRenderer.tsx:63-78`）；`recommend_questions`（结束后最后一条底部按钮组，`index.tsx:705-726`）；`cancelled` 中断条（`MessagePartRenderer.tsx:339-374`）。
- **过程区（当前散装）**：`thinking` / `tool/*` / `sandbox` / `summary` / `subagent` 各自独立成行（`index.tsx:541-566` 的 `space-y-3` 容器 `my-2 pl-1` 内逐个渲染）。`TodoBlock` 是常驻展开的进度卡片（`TodoBlock.tsx:43-49`）。
- **产物卡片位置**（已证实，分两层）：
  1. 行内层：`reveal_file`/`reveal_project` 作为普通 part 插在过程流之间（`MessagePartRenderer.tsx:96-138` 带 anchor 高亮 div）；工具结果里的图片/文件缩略图藏在各 tool 的折叠详情里（`ToolResultContent`）。
  2. 汇总层：`RevealArtifactsSummary`（`index.tsx:567-571`，parts 之后），`collectRevealArtifacts(parts)` 聚合（`RevealArtifactsSummary.tsx:370`），**流式中不渲染**（:430 `if (isStreaming...) return null`），完成后渲染「全部文件」整宽卡片（:507-537）→ 点击开侧边全文件面板（文件树 + 项目分区，:434-503）→ 图片走 `ImageViewer` 灯箱（:538-550）。

### 5. 消息头、状态文案、错误、审批（已证实）

| 项 | 现状 | 证据 |
|---|---|---|
| 消息头 | 头像（`AssistantAvatar`，persona 头像优先）+ 助手名/ persona 名（serif 大标题）+ hover 才显示的时间戳 | `index.tsx:513-535`（`AssistantAvatar` :515；名 :519-524；时间 `opacity-0 group-hover:opacity-100` :527） |
| 状态文案 | **无「正在处理/已完成」文字头**。流式中底部一个 amber `CollapsiblePill`「生成中…」（`generating`）+ `Sparkles` + 打字点；完成即消失。无完成态横幅 | `index.tsx:609-621`；`zh.json:766 "generating": "生成中"`。`sessionRunning=isLoading 或任一 message.isStreaming`（`sessionState.ts:5-10`） |
| 停止 | 输入框发送键变停止键（需二次确认弹窗），`onStop→onStopGeneration` | `ChatInput.tsx:649-650,958-1060`（`stopConfirmOpen` 确认弹窗）；`ChatView.tsx:413-414` 传入 |
| 错误提示 | 三处：①非取消错误直接把翻译后文本拼进 `message.content` 当正文（`chat.errorPrefix` 前缀）；②取消（CancelledError）→ `cancelled` part 中断条「你已终止本次回答」+ 重试按钮；③sandbox 错误 → `SandboxItem` error pill + 全局 `sandboxError` state（`eventHandlers.ts:398-400`） | `eventProcessor.ts:489-499`；`MessagePartRenderer.tsx:339-374`；`zh.json:767 "interrupted": "你已终止本次回答"`；`SubagentBlocks.tsx:859-922` |
| 待确认 human-in-the-loop | 通用表单审批渲染在 **ChatView 底部输入框上方**的 `ApprovalPanel`（表单字段 + 倒计时 + 同意/拒绝），与消息流分离；`useApprovals` 管理；`approval_required`（form）→ `handleApprovalRequired`（`eventHandlers.ts:259-273` 起） | `ChatView.tsx:531-535`；`panels/ApprovalPanel.tsx:22-30`（props）；`hooks/useApprovals.ts` |
| SOP 审批 | 被拦截不进 ApprovalPanel，渲染为 `SopBlock` DAG 卡片（ChatView 底部同区），`sopPlan` state 驱动 | `eventHandlers.ts:263-270`；`ChatView.tsx:537-546`；`historyLoader.ts:232-249`（历史不回填 generic approval；sop_plan 同样跳过） |
| 定时任务草稿确认 | 行内 `ScheduledTaskDraftCard`（确认/取消卡片，直接嵌在 parts 流中） | `MessagePartRenderer.tsx:225-234` |

相关 i18n 文案（`frontend/src/i18n/locales/zh.json`，已证实）：`thinking 思考中 :782` / `thought 已思考 :783` / `executing 正在执行... :758` / `running 运行中... :777` / `processing 过程 :773`（subagent 面板内部分区标题）/ `toolCalls 工具调用 :791` / `toolOutput 工具输出 :809` / `queueStart 任务开始处理 :823`。

### 6. 流式执行中当前步骤的呈现（已证实）

- 空消息（无 parts）时：骨架屏 `ThinkingIndicator`（4 组灰条，`index.tsx:42-69`，挂载条件 `isStreaming && !hasParts` :538）。
- 有 parts 后：当前 pending 的 tool/thinking pill 显示 **loading（amber）+ `LoadingSpinner` + 经过秒数**（`ToolCallItem.tsx:100,137-146,181-187` 的 `useElapsedSeconds`，rAF 计时，`formatElapsed` :41-46；`CollapsiblePill.StatusIndicator` :53-85）。thinking 流式加 `animatedDots` 打字点（`CollapsiblePill.tsx:141` + `SubagentBlocks.tsx:615`）。`TodoBlock` 流式在头部转圈（`TodoBlock.tsx:67-72`）。`SandboxItem` starting 态 amber + 打字点（`SubagentBlocks.tsx:877-899`）。
- 底部常驻「生成中」pill（`index.tsx:610-621`，`expandable={false}` 不可展开）。
- 子代理运行时：卡片 amber 边框 + 右上转圈状态点（`SubagentBlocks.tsx:770-771,791`）；首个 running subagent **自动打开侧边面板**（`shouldAutoOpenSubagentPanel`，:709-726；关闭后不再自动打扰 `dismissSubagentPanelAutoOpen`，`subagentPanelControl.ts`）。
- 完成时（`complete`/`done`）：`clearAllLoadingStates` 把所有 pending 置 false/cancelled、thinking `isStreaming=false`、todo `in_progress→pending`（`messageParts.ts:615-670`），底部 pill 消失，`RevealArtifactsSummary` 出现。
- 断线重连：`shouldShowStreamingFooterSkeleton`（`sessionState.ts:12-32`）在 disconnected/reconnecting 且无可见流式消息时底部补骨架。

## Caveats / Not Found（未找到/推测，需明确区分）

- **未找到**任何现成的「执行过程」分组容器组件：`parts.map` 直铺（`index.tsx:541-566`）是唯一主路径；`SubagentPanelContent` 内的 `processing` 折叠区（`SubagentBlocks.tsx:520-539`）是唯一收纳先例但只覆盖 subagent 内部。
- **未找到** thinking/tool 展开状态的全局/持久化存储：均为组件局部 `useState`（`CollapsiblePill.tsx:101`）。列表卸载（Virtuoso 虚拟化）后是否重置为推测，未实测。
- **未找到**「正在处理 N/M」「已完成，耗时 Xs」这类聚合状态文案：耗时只存在于各 tool 面板 footer（`ToolCallItem.tsx:103-111`）和 `tokenUsage.duration` popup（`index.tsx:209-218`）。
- **未找到**搜索/图片生成专用 part 类型：搜索类 MCP 工具走通用 `ToolCallItem`（`server:tool` 冒号切分，`ToolCallItem.tsx:114-117`）；图片产物走 `result.images[]`/`blocks`（`toolImageResults.ts:42` + `McpBlockPreview.tsx:289-299`）。如后端新增事件类型，前端 `MESSAGE_EVENTS` 白名单（`eventHandlers.ts:307-323`）会 warn 丢弃（:325）。
- **推测**：`args.partial`（`ToolCallItem.tsx:124-132`）为流式参数片段，但未在 `tool:start` 处理中看到增量更新 args 的逻辑，流式中 args 可能只显示首帧——如需可进一步查后端是否发增量事件。
- 本调研只读了前端；后端事件发送顺序/字段以 `backend` 为准，未核对。

## Related Specs

- 未在 `.trellis/spec/` 下找到与聊天渲染管线直接对应的 spec（仅列出目录：`backend/ frontend/ guides/`）。如需可后续检索确认。

## 新「执行过程」分组组件最可能的落点与复用点（基于已证实结构）

1. **落点（按推荐度排序）**：
   - `frontend/src/components/chat/ChatMessage/index.tsx:540-572` 的 parts 容器：把 `message.parts` 按「过程 part（thinking/tool/sandbox/summary/todo?/subagent?）vs 正文 part（text/recommend/cancelled）」分区渲染，新分组组件包裹过程 parts 即可，改动面最小且与 `RevealArtifactsSummary`（:567-571，同容器内）天然相邻。
   - 新文件建议与 `MessagePartRenderer.tsx` / `SubagentBlocks.tsx` 同目录（`frontend/src/components/chat/ChatMessage/`），命名如 `ExecutionProcessGroup.tsx`，内部复用 `MessagePartRenderer` 逐个渲染过程 parts（subagent 内部已有递归先例 `SubagentBlocks.tsx:527-536`）。
2. **复用点**：
   - 折叠原子：`frontend/src/components/common/CollapsiblePill.tsx:87`（状态色/loading spinner/打字点/移动端 `onPanelOpen` 分流逻辑）。
   - 面板宿主：`frontend/src/components/chat/ChatMessage/items/persistentToolPanelState.tsx:72`（`openPersistentToolPanel`）+ `ToolResultPanel.tsx:112`（sidebar/center 切换）——分组头可沿用「点击开面板看明细」交互。
   - 状态推导：`sessionState.ts:5`（`isSessionRunning`）、各 part `isPending/isStreaming` 字段、计时 `useElapsedSeconds`（`ToolCallItem.tsx:11`）、聚合耗时 `computeDuration`（:49-57）。
   - 文案：`zh.json` 现有 `thinking/thought/executing/running/processing/generating` 可直接复用，注意 `processing`（:773 „过程"）已是 subagent 面板内部分区标题，命名需避重。
   - 历史/流式一致性：只要消费 `Message.parts`（`types/message.ts:16`）而非 SSE 事件，分组自动同时作用于实时流与 `historyLoader.ts:310` 重放；`complete/done` 的 `clearAllLoadingStates`（`messageParts.ts:615`）即分组的「收起/完成态」触发点。
