# Research: 流式状态机、步骤计时、历史恢复、错误与待确认的数据模型 (目标 C)

- **Query**: 新「执行过程」分组所需的流式状态、计时、历史恢复、错误/待确认模型与展开持久化现状
- **Scope**: internal（前端 useAgent/ChatMessage + 后端 writer/routes + spec）
- **Date**: 2026-10-16

## Findings

### 1. 前端流式状态管理（已证实）

| File Path | Description |
|---|---|
| `frontend/src/hooks/useAgent.ts:65,95-130` | `messages` 为 `useState<Message[]>`，无全局 store/reducer；`streamingMessageIdRef`、`processedEventIdsRef`、`lastHistoryTimestampRef`、`activeSubagentStackRef`、`streamVersionRef` 等 ref 做去重/排序/防过期管理 |
| `frontend/src/hooks/useAgent/types.ts:13-39,127-162` | `Message{isStreaming,duration,runId,cancelled,parts}`；`ToolPart{isPending,startedAt,completedAt}`（string ISO）；`SubagentPart{isPending,status,startedAt,completedAt}`（number ms）；`ThinkingPart/TextPart` 无时间戳字段 |
| `frontend/src/hooks/useAgent/eventHandlers.ts:307-323,338-377` | `MESSAGE_EVENTS` 白名单 + `processMessageEvent(..., isStreaming=true)` 合并；`complete/done` 仅 `clearAllLoadingStates`（226-247 行），不消费 `steps` 计数 |
| `frontend/src/hooks/useAgent/eventProcessor.ts:106-504` | 统一合并函数：`tool:start` 新建 pending `ToolPart`，`tool:result` 按 `tool_call_id` 回填 `result/success/error/completedAt`；`agent:call/result` 进出 subagent 栈；`token:usage` 写 `tokenUsage` + `duration=data.duration*1000`（383 行）；`error` 翻译 + `clearAllLoadingStates` |
| `frontend/src/hooks/useAgent/messageParts.ts:28-46,71-92,484-547,615-670` | `createToolPart/createSubagentPart`（subagent `startedAt=parseDate(timestamp)`）；`updateToolResultInDepth` 按 id 回填；`clearAllLoadingStates` 把 pending 工具/子代理标 cancelled（保留 complete/error 终态） |
| `frontend/src/components/chat/ChatMessage/index.tsx:540-608,610-621` | 实时与历史共用 `ChatMessage` + `MessagePartRenderer` 按 `parts` 顺序渲染；底部 `CollapsiblePill` "generating" 仅 `message.isStreaming` 时显示 |
| `frontend/src/components/chat/ChatMessage/ToolCallItem.tsx:11-57,100-146` | 计时已存在：`useElapsedSeconds(isPending, startedAt)`（rAF 计 running 秒数）+ `computeDuration(startedAt, completedAt)`（秒级最终耗时，footer 展示）；`status` 由 `isPending/cancelled/success/hasResult` 推导 |

步骤序号/耗时字段结论（已证实）：
- 无步骤序号字段。前端 state 里没有 `step_index/total`；后端 `_step_count` 只在 `agent:call` 时 +1（`src/infra/writer/presenter_events.py:269`），且 `done` 事件的 `steps/tool_calls` 前端直接丢弃。
- 起止时间戳：`tool:*` 有（`startedAt/completedAt` ISO string），`subagent` 有（ms number），`thinking/text/todo/summary` 无（事件层 timestamp 到前端被丢弃）。
- 总耗时：`Message.duration` 来自 `token:usage.duration`（秒→ms）。若该事件缺失则无总耗时（fallback 未找到，见下）。

### 2. 历史消息加载（已证实）

| File Path | Description |
|---|---|
| `frontend/src/services/api/session.ts:281-310,66-126` | `getEvents`（`limit` 默认 1000）+ `getAllEvents` 按 `after` cursor 全量翻页、`historyEventKey` 去重 |
| `src/api/routes/session.py:276-356` | `GET /api/sessions/{id}/events`，`completed_only=True`（329 行，运行中 trace 不返回，避免与 SSE 重复），返回 `events/has_more/next_cursor/history_complete/ordering_version` |
| `frontend/src/hooks/useAgent/historyLoader.ts:357-575` | `reconstructMessagesFromEvents`：按 `compareHistoryOrder`（`history_order` 四元组优先，其次 `seq`，最后 timestamp/trace/event_id，82-114 行）排序后，用同一 `processMessageEvent(..., isStreaming=false)` 重建 `parts`；`user:cancel` 补 `cancelled` part（455-490 行） |
| `frontend/src/hooks/useAgent.ts:406-536` | `loadHistory` 并行拉 events/status/feedback；运行中 run 经 `prepareMessagesForRunningRun`（577-618 行，复用同 runId 的 assistant 气泡标 `isStreaming`）+ `connectToSSE` 续流 |
| `frontend/src/components/layout/AppContent/ChatView.tsx:360-382` | 历史与实时走同一 `ChatMessage`（Virtuoso `virtuosoItemContent`），`recommend_questions` 仅末条渲染 |
| `.trellis/spec/frontend/sop-history-restoration.md` | SOP 历史恢复契约：`sop:updated` 不进 message body，重建成 `sopPlan` state；`restoreSopPlanFromHistory`（`historyLoader.ts:117-168`）取最新快照 + 同 plan 审批元数据 |
| `.trellis/spec/backend/trace-event-storage.md` / `session-history-pagination.md` | 事件不可变存储 + 游标分页契约（`seq/event_id/history_order` 身份与排序） |

历史是否保留思考/工具步骤（已证实）：是。`Presenter.save_event` 全量持久化，`historyLoader` 对 `thinking/tool:start/tool:result/agent:call/result/todo/summary/token:usage` 全部重放；例外只有 `sop:updated`（进 `sopPlan`）、`approval_required(sop_plan)`（拦截进 SOP 卡，不进通用审批）、`metadata/done/goal:updated`（跳过）。

### 3. 错误模型（已证实为主，一处未完全证实）

| File Path | Description |
|---|---|
| `src/infra/writer/presenter_events.py:554-583` | `done()` 带 `{status,steps,tool_calls}`；`error(message, error_type, details)` 生成 `{error,type,trace_id,details,timestamp}` |
| `src/api/routes/chat.py:702-737` | SSE 迭代 `asyncio.wait_for(..., timeout=60.0)`，generator 异常时补 `event: error {"error":"An internal error occurred"}` |
| `frontend/src/hooks/useAgent/eventProcessor.ts:489-500` | `error` 事件：`translateBackendError` + `type==="CancelledError"` 走取消分支（不写 error 文案），否则 `content=errorPrefix` 并清 loading |
| `frontend/src/hooks/useAgent/eventHandlers.ts:491-527` | `handleError`：取消→`appendCancelledPart` + `cancelled:true`；非取消→覆盖 `content` 为错误文案；两种都 `clearAllLoadingStates` + `onClearApprovals` |
| `frontend/src/components/chat/ChatMessage/ToolCallItem.tsx:137-146` | 工具失败渲染：`success===false` 且有 result → `status="error"`（红色）；pending→loading；cancelled→cancelled |
| `frontend/src/components/chat/ChatMessage/SubagentBlocks.tsx:770-780` | 子代理 error 红色边框卡（`effectiveStatus==="error"`） |
| `frontend/src/components/chat/ChatMessage/cancelledRetry.ts:1-37` + `ChatView.tsx:281-295` | 唯一重试入口：cancelled 气泡的「重试回答」→ 取上一条 user 消息原文重发 `onSendMessage` |

未找到：工具级超时事件（无 `tool:timeout` 之类事件名，前后端均未搜到）；整体失败除 `error` 事件外无 `status:failed` 持久化字段（`RunSummary.status` 有 pending/running/completed/failed，但为 runs 列表投影，非消息内标记）；失败重试生成入口不存在（仅取消可重试，失败只能用户手动重发，未找到 `retryFailed` 代码）。

### 4. 待确认模型 HITL/审批（已证实，暂停语义一处需跟读）

| File Path | Description |
|---|---|
| `src/infra/writer/presenter_events.py:383-418` | `present_ask_human` 发 `approval_required{id,message,type,choices,default,timestamp}`；`src/agents/team_agent/sop/tool.py:60-76` 发 `approval_required(sop_plan)` |
| `src/api/routes/human.py:103-144,147-230,252-385` | `create_approval`（Mongo+本地 Event+Redis）；`wait_for_response(timeout=300)`（本地 Event 与 Mongo 轮询双路）；`GET /human/pending`、`POST /human/{id}/respond?approved&response`、`POST /human/{id}/extend`、`GET /human/{id}`、`DELETE /human/{id}`；`sop_plan` 重复响应幂等回既有决策（289-299 行） |
| `frontend/src/hooks/useApprovals.ts:1-98` | `approvals: PendingApproval[]`；`addApproval`（SSE 驱动，去重）；`respondToApproval` 调 respond 接口后本地移除；`clearApprovals` 供失败/取消/停止时清空；`sessionId` 变化时 `fetchApprovals` 恢复刷新后的 pending（84-88 行） |
| `frontend/src/hooks/useAgent/eventHandlers.ts:259-273,536-566` | `approval_required` 非 sop_plan → `handleApprovalRequired`（先 `GET /human/{id}` 确认仍 pending 才 `onApprovalRequired`）；sop_plan → `reduceSop` 进 SOP DAG 卡，不进通用面板 |
| `frontend/src/hooks/useAgent/historyLoader.ts:233-273` | 历史 `approval_required` 同样查 `/human/{id}`，仅 `status==="pending"` 才回填；sop_plan 跳过 |
| `frontend/src/components/panels/ApprovalPanel.tsx:196-549` | 卡片 UI：倒计时/urgent 标红、超时自动关闭、extend 防抖、form 字段渲染、submit/cancel |
| `frontend/src/hooks/useAgent.ts:899-929` | `stopGeneration` 立即 `onClearApprovals()` + `setSopPlan(null)` + `sessionApi.cancel`（不等 SSE cancel 事件） |

暂停/恢复语义（已证实一半）：后端 `wait_for_response` 阻塞 agent 至响应/超时/extend（已证实）；超时后 agent 侧行为（继续/报错/取消）需跟读 `ask_human` 工具调用方，未找到调用点，本次标「未完全证实」。前端无暂停/恢复流式事件，只有取消整轮 + 清审批（推测：待确认期间流保持 open 等待，无专门 `paused` 状态；待实现方验证）。

### 5. 展开/收起持久化（已证实：无）

- `CollapsiblePill.tsx:101`：`useState(defaultExpanded=false)`，`onExpandChange` 回调存在但无任何调用方持久化（grep 无 `localStorage/sessionStorage` 与其联动）。
- `SubagentBlocks.tsx:359`（`CollapsibleSection`）、`SopBlock.tsx:57`（`useState(true)`）、`RevealArtifactsSummary.tsx:180`：全部组件内 `useState`，刷新/切换会话/重挂载即重置。
- `persistentToolPanelState.tsx` + `subagentPanelControl.ts`：面板为内存单例 + 模块级 `autoOpenDismissed` flag，不落盘。
- grep `localStorage|sessionStorage` 在 chat 目录仅命中 `newlineModifier` 等输入偏好，无按 `message.id` 存展开态的做法（已证实缺失）。
- 推论影响：「用户手动展开后不再被强制收起」需新做：按 message id 存用户覆盖态（内存 Map 至少，跨刷新则 localStorage），且自动收起逻辑必须先查覆盖态。

### 6. 后端是否需要改动 + 事件定义文件

| File Path | Description |
|---|---|
| `src/infra/writer/present.py:1-20,66-76` | 事件目录注释 + `Presenter._step_count/_tool_calls` 计数器 |
| `src/infra/writer/presenter_events.py:83-109,204-381,451-583` | `_build_event`（depth/agent_id 注入）；thinking/summary/todo/agent/tool/sandbox/token/done/error 全部定义见上表 |
| `src/api/routes/chat.py:315,702-737,787-815` | `goal:start` 下发；SSE 60s 超时与 error 兜底；cancel 接口 |
| `src/api/routes/session.py:276-356` | 历史事件 API（含 `completed_only` 与分页） |
| `src/api/routes/human.py` 全文件 | 审批全生命周期 |

判断（带依据）：
- **纯前端可实现（v1）**：「N 步」= 顶层 `tool` + `subagent` part 计数（含嵌套可选）；「X 秒」= `Message.duration`（有 `token:usage` 时）或 `max(completedAt)-min(startedAt)` 回退；能力分类 = 工具名前缀（`MessagePartRenderer.tsx:80-246` 已有 read/reveal/edit/write/grep/ls/glob/execute + `:` 判 MCP）；错误/待确认外显 = 按 `success===false/isPending/approvals` 过滤提升；历史同构（同一 parts 体系）天然支持刷新后收纳。
- **需要后端小改（v2 精确化，如需承诺口径）**：(a) thinking/text 无时间戳 → 补 `startedAt/completedAt` 或由后端直接下发每步耗时；(b) 无步骤序号 → 复用/扩展 `done.steps` 或新增 `run:start/run:end{started_at,ended_at,step_count}`，否则跨刷新「X 秒」依赖 `token:usage`（缺失即无值）；(c) 能力分类靠名称推断脆弱 → 加 `category` 字段（如 file/search/mcp/execute）。三项均为加字段/补事件，不碰存储与排序。
- **需要后端大改：否**。事件全量持久化 + `seq/history_order` 排序 + `completed_only` 分页已满足「历史同样收纳」；无需动 trace 存储、merge 或游标协议。

## Caveats / Not Found

- 未找到：步骤序号字段、`tool:timeout` 事件、失败重试入口、按 message id 的展开持久化、run 级起止时间戳事件（仅 `goal:start/end` 有，不等价于 run）。
- 未完全证实：`wait_for_response` 超时后 agent 行为（需读 ask_human 工具调用方）；`sseConnection.ts` 重连退避细节（本次未精读，只确认存在）；`useApprovals` 与 `ChatAppContent` 的 `onApprovalRequired/onClearApprovals` 接线（只在两端确认，未逐行跟读中间透传）。
- 推测（已标注）：待确认期间流状态为 open 等待、无 paused 事件；`Message.duration` 缺失率取决于后端是否每轮都发 `token:usage`。
