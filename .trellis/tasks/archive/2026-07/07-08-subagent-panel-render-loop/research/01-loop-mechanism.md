# Research: Subagent Panel Render Loop — Precise Mechanism

- **Query**: 追踪 `Maximum update depth exceeded` 的精确闭环机制：`SubagentBlock` effect 调 `store.set` → emit → `SubagentPanelContent` forceRender，为何会让 `SubagentBlock` 再次重渲染？
- **Scope**: internal
- **Date**: 2026-07-08

## TL;DR

这不是一个简单的两点重入（`SubagentBlock` ↔ `SubagentPanelContent`）。`SubagentPanelContent` 渲染在 portal 里（`PersistentToolPanelHost` 的子节点），与 `SubagentBlock` 不共享父组件，`SubagentPanelContent` 的 `forceRender` **不会直接**让 `SubagentBlock` 重渲染。

真正的机制是 **高频级联 setState + 同步阻塞** 导致 React 触发 update depth 保护：

1. 每个 SSE 事件 → `setMessages` → 新 `message.parts` 引用 → `ChatMessage` 重渲染 → `SubagentBlock` 重渲染。
2. `SubagentBlock` effect（依赖 `parts`）每个事件重跑 → 同步调用 `subagentPanelStore.set`（无脏检查）+ `updatePersistentToolPanel`。
3. 两个 `set` 在 effect 内同步 `emit` → `SubagentPanelContent.forceRender` + `PersistentToolPanelHost.forceRender`，二者在同一 commit 阶段被调度。
4. `SubagentPanelContent` 的 `useLayoutEffect`（**无依赖数组**，`SubagentBlocks.tsx:455-466`）每次渲染都同步跑 `scrollToBottom` → `startAutoScroll`，把 store emit 拉进同步布局阶段，阻塞主线程。
5. 当 SSE 事件高频到达（fast_agent 流式，控制台 `[handleStreamEvent]` 刷屏），React 来不及 flush，pending updates 累积超过 50 次阈值 → `Maximum update depth exceeded`。

**可能的真正重入路径（嵌套子代理）**：若 subagent 的 `parts` 内含嵌套 `subagent` part（fast_agent / rubric grader 会产生 `depth=1` 嵌套，见 `src/infra/agent/events/processor.py:177-208`），`SubagentPanelContent` 会渲染一个嵌套 `SubagentBlock`，其 effect 调 `store.set(nestedAgentId, ...)`。若嵌套 agent 的 panel 也被打开（`SubagentPanelContent` 渲染嵌套 `SubagentBlock` 时 `shouldAutoOpenSubagentPanel` 为真且无 panel 打开），则形成递归重入：`SubagentPanelContent` forceRender → 渲染嵌套 `SubagentBlock` → effect `store.set` → 嵌套 `SubagentPanelContent` forceRender → …。这是最可能触发严格意义重入的路径。

## 证据链

### 1. `SubagentBlock` 与 `SubagentPanelContent` 的拓扑关系

- `SubagentBlock` 由 `MessagePartRenderer`（`MessagePartRenderer.tsx:242-259`）渲染，挂在 `ChatMessage`（`ChatMessage/index.tsx:538-564`）下，属于消息流（Virtuoso `data={messages}`，`ChatView.tsx:470-485`）。
- `SubagentPanelContent` 由 `openPersistentToolPanel({children: <SubagentPanelContent agentId={agent_id} />})` 注入 `panelStore`（`SubagentBlocks.tsx:713-722`、`317`），由 `PersistentToolPanelHost`（`persistentToolPanelState.tsx:110-138`）通过 `createPortal(..., document.body)` 渲染。
- `PersistentToolPanelHost` 与消息列表是 `ChatView` 的兄弟节点（`ChatView.tsx:501`），不共享 `messages` state 的直接订阅。

结论：`SubagentPanelContent` 的 `forceRender` 不会通过 React 父子关系传回 `SubagentBlock`。

### 2. `SubagentBlock` effect 的依赖与触发频率

`SubagentBlocks.tsx:681-741`：
```tsx
useEffect(() => {
  subagentPanelStore.set({ agentId: agent_id, ..., parts, ... });   // :682 总是 emit
  if (isPersistentToolPanelOpen(panelKey)) {
    updatePersistentToolPanel((prev) => ({ ...prev, status: panelStatus, subtitle }), panelKey);  // :698 也 emit
  } else if (shouldAutoOpenSubagentPanel(...)) {
    openPersistentToolPanel({ ..., children: <SubagentPanelContent agentId={agent_id} /> });       // :713
  }
}, [agent_id, agent_name, input, result, success, error, isPending, parts, startedAt, completedAt, effectiveStatus, panelStatus, subtitle, formattedAgentName, RoleIcon, panelKey]);
```

- 依赖数组里只有 `parts` 是非稳定引用（见 `02-parts-reference-flow.md`），其余均为原始值或稳定组件引用（`RoleIcon = roleIconMeta.icon`，`panelKey = createSubagentPanelKey(agentId) = "subagent-${agentId}"`，`messagePartAnchors.ts:12-14`）。
- 因此 effect 在 fast_agent 流式时每个 SSE 事件重跑一次。

### 3. `subagentPanelStore.set` 无脏检查 → 同步 emit

`subagentPanelStore.ts:47-50`：
```ts
set(next) {
  data.set(next.agentId, next);
  emit(next.agentId);   // 无 Object.is / 浅比较，相同数据也 emit
}
```

`emit`（`:31-35`）同步 `subscribed.forEach((listener) => listener())`，listener 是 `useSubagentPanelData` 的 `forceRender`（`SubagentBlocks.tsx:74`）。

`forceRender((n) => n + 1)` 在 effect 执行期间被同步调用 → React 调度 `SubagentPanelContent` 重渲染。

### 4. `SubagentPanelContent` 的 `useLayoutEffect` 无依赖数组

`SubagentBlocks.tsx:455-466`：
```tsx
useLayoutEffect(() => {
  if (!shouldAutoScrollSubagentPanel({ scroller: scrollRef.current, userScrolledUp: userScrolledUpRef.current })) {
    return;
  }
  scrollToBottom();   // 每次渲染都同步跑
});                    // ← 无依赖数组
```

- 无依赖数组 ⇒ 每次 `SubagentPanelContent` 渲染后都同步执行 `scrollToBottom` → `startAutoScroll` → `startSubagentPanelScrollToBottom`（`subagentPanelScroll.ts:56-114`）。
- `startSubagentPanelScrollToBottom` 同步调用 `scrollToBottom()`（设置 `scroller.scrollTop`）并启动 `setInterval`（30ms × 20 次）。
- 本身不触发 React setState，但把 store emit 拉进同步布局阶段，阻塞主线程，使 React 无法及时 flush pending updates。

### 5. `updatePersistentToolPanel` 同步 emit → 第二路 forceRender

`SubagentBlocks.tsx:697-705`（panel 已打开分支）：
```tsx
updatePersistentToolPanel((prev) => ({ ...prev, status: panelStatus, subtitle }), panelKey);
```

`persistentToolPanelState.tsx:80-88`：
```tsx
export function updatePersistentToolPanel(updater, panelKey?) {
  const currentPanel = panelStore.get();
  if (!currentPanel) return;
  if (panelKey && currentPanel.panelKey !== panelKey) return;
  panelStore.set(updater(currentPanel));   // updater 返回新对象 → Object.is 失败 → emit
}
```

`createSingletonStore.set`（`createSingletonStore.ts:17-23`）有 `Object.is` 脏检查，但 updater 返回 `{...prev, status, subtitle}` 是新对象 ⇒ emit ⇒ `PersistentToolPanelHost.forceRender` ⇒ `SubagentPanelContent` 作为 `panel.children` 被连带重渲染。

注意：`children` 引用不变（`<SubagentPanelContent agentId={agent_id} />` 是 effect 闭包里的同一个元素），但 `ToolResultPanel` 未 memo，仍会重渲染子树。

### 6. 高频 SSE → pending updates 累积

`eventHandlers.ts:60-64`：
```ts
console.log("[handleStreamEvent] Received event:", { eventType, event.event, messageId, eventId });
```

该日志高频刷屏 ⇒ SSE 事件密集。每个 message-transforming 事件（`eventHandlers.ts:309-348`）调 `ctx.setMessages((prev) => prev.map(...))`，`prev.map` 对匹配 message 返回 `{...m, parts: result.parts, ...}` 新对象 ⇒ `ChatMessage`（memo）因 `message` prop 变化而重渲染 ⇒ `SubagentBlock` 重渲染 ⇒ effect 重跑 ⇒ 两路 emit。

当事件频率 > React flush 能力（被 `useLayoutEffect` 同步阻塞拖慢），pending updates 累积超过 50 ⇒ React 抛 `Maximum update depth exceeded`，调用栈指向 effect 内的 `store.set`（`SubagentBlocks.tsx:682`）→ emit → listener（`SubagentBlocks.tsx:74`）。

### 7. 嵌套子代理重入路径（可能的严格重入）

- `SubagentPanelContent` 渲染 `data.parts.map((part) => <MessagePartRenderer ... />)`（`SubagentBlocks.tsx:523-532`）。
- `MessagePartRenderer` 对 `part.type === "subagent"` 渲染 `<SubagentBlock ... parts={part.parts} />`（`MessagePartRenderer.tsx:242-259`）。
- 嵌套 `SubagentBlock` 的 effect（`SubagentBlocks.tsx:681`）调 `subagentPanelStore.set({agentId: nestedAgentId, parts: nestedParts, ...})`。
- 若嵌套 agent 的 panel 也被打开（`shouldAutoOpenSubagentPanel` 在 `effectiveStatus === "running"` 且无 panel 打开时为真，`subagentPanelControl.ts:22-32`），则形成递归：
  `SubagentPanelContent(parentId)` forceRender → 渲染嵌套 `SubagentBlock(nestedId)` → effect `store.set(nestedId)` → `SubagentPanelContent(nestedId)` forceRender → 渲染更深嵌套 `SubagentBlock` → …

后端确认嵌套存在：`src/infra/agent/events/processor.py:177-208` 的 Rubric Grader 以 `depth=1` emit `agent:call` / `agent:result`；deepagents `SubAgent` 机制（`fast_agent/nodes.py:12` import `CompiledSubAgent, SubAgent`）允许子代理再调子代理。

## 对调用栈的对应解释

```
at listener (SubagentBlocks.tsx:74:28)        ← useSubagentPanelData 的 forceRender listener
at subagentPanelStore.ts:34:38                 ← subscribed.forEach(listener)
at emit (subagentPanelStore.ts:34:16)
at Object.set (subagentPanelStore.ts:49:7)     ← store.set → emit
at SubagentBlocks.tsx:682:24                    ← SubagentBlock effect 调 store.set
```

React 检测到：在 `SubagentBlock` 的 effect 提交阶段，`store.set` 同步触发了 listener（`forceRender`），而该 forceRender 调度的重渲染又（通过级联或嵌套重入）再次触发了同一个 effect 的 `store.set`，循环计数超过阈值。

## Caveats

- 严格重入（单次 update 内 >50 次）最可能由 **嵌套子代理 panel 同时打开** 触发；非嵌套场景下更可能是 **高频 SSE + 同步 useLayoutEffect 阻塞** 让 React 误判为深度溢出。两者根因相同：`store.set` 无脏检查 + `parts` 不稳定引用 + `useLayoutEffect` 无依赖。
- 未能在仓库内找到能 100% 复现重入的单元测试（测试均为纯函数级，见 `04-test-coverage.md`），故嵌套重入路径基于代码静态分析 + 后端事件源确认，建议 implement 阶段用 fast_agent 触发子任务实跑验证。
