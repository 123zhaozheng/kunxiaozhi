# Research: Other Loop Sources — Similar Patterns

- **Query**: 检查 `ThinkingBlock`、`TodoBlock`、`MessagePartRenderer`、`persistentToolPanelState` 其他订阅者是否有"effect 依赖不稳定引用 + store.set 无脏检查"模式。
- **Scope**: internal
- **Date**: 2026-07-08

## 检查清单

### 1. `ThinkingBlock`（`SubagentBlocks.tsx:574-628`）

**有类似模式，但脏检查由 `Object.is` 兜底，风险较低。**

```tsx
useEffect(() => {
  if (!isPersistentToolPanelOpen(panelKey)) return;
  updatePersistentToolPanel(
    (prev) => ({
      ...prev,
      status,
      children: (<div className="p-3 sm:p-4 [&_.markdown-preview]:thinking-content">
        <MarkdownContent content={content} isStreaming={isStreaming} />
      </div>),
    }),
    panelKey,
  );
}, [content, isStreaming, panelKey, status]);
```

- 依赖：`content`（流式时每个 chunk 变）、`isStreaming`、`panelKey`（=`part.thinking_id`，`MessagePartRenderer.tsx:237`）、`status`。
- `content` 流式时高频变化 ⇒ effect 高频重跑 ⇒ `updatePersistentToolPanel` 返回新对象（含新 `children` JSX） ⇒ emit ⇒ `PersistentToolPanelHost.forceRender`。
- **但**：`panelKey` 是 `thinking_id`，而 `SubagentPanelContent` 所在 panel 的 `panelKey` 是 `subagent-${agentId}`。`updatePersistentToolPanel` 在 `currentPanel.panelKey !== panelKey` 时早退（`persistentToolPanelState.tsx:86`）。
  - 若 thinking 的 panel **未单独打开**（用户没点 thinking pill），`isPersistentToolPanelOpen(thinking_id)` 为 false ⇒ effect 早退，不 emit。
  - 若 thinking panel **单独打开**（用户点了 thinking pill），则当前 panel 的 `panelKey === thinking_id`，updater 生效 ⇒ emit。
- 风险：当 thinking panel 打开 + 流式 thinking 时，每个 chunk 触发一路 forceRender。但这与 subagent 循环**独立**，且 `content` 变化是真实内容变化，emit 合理。
- 结论：**非 subagent 循环源**，但若与 subagent panel 同时打开会叠加 forceRender。

### 2. `SummaryItem`（`SummaryItem.tsx:13-64`）

**与 `ThinkingBlock` 完全同构，同样非 subagent 循环源。**

```tsx
useEffect(() => {
  if (!isPersistentToolPanelOpen(panelKey)) return;
  updatePersistentToolPanel((prev) => ({...prev, status, children: (<div><MarkdownContent content={content} isStreaming={isStreaming} /></div>)}), panelKey);
}, [content, isStreaming, panelKey, status]);
```

- `panelKey` 由 `MessagePartRenderer.tsx:284-286` 构造为 `summary:${agent_id}:${depth}:${summary_id}`。
- 同样依赖 `panelKey` 早退保护。
- 结论：非循环源。

### 3. `TodoBlock`（`TodoBlock.tsx:32-100`）

**无 effect，无 store 交互，非循环源。**

- 纯展示组件，无 `useEffect`、无 `subagentPanelStore` / `updatePersistentToolPanel` 调用。
- props `items` / `isStreaming` 来自 `MessagePartRenderer.tsx:273-280`。
- 结论：非循环源。

### 4. `MessagePartRenderer`（`MessagePartRenderer.tsx:26-360`）

**纯分发组件，无 effect，无 store 交互，非循环源。**

- 根据 `part.type` 分发到 `MarkdownContent` / `ToolCallItem` / `ThinkingBlock` / `SubagentBlock` / `SandboxItem` / `TodoBlock` / `SummaryItem` 等。
- 无 `useEffect`、无 `useState`、无 store 调用。
- 结论：非循环源（只是把 `parts` 引用透传给 `SubagentBlock`）。

### 5. `persistentToolPanelState` 订阅者

**唯一订阅者是 `PersistentToolPanelHost`（`usePersistentToolPanel`），无其他循环源。**

- `subscribePersistentToolPanel`（`persistentToolPanelState.tsx:62-64`）仅被 `usePersistentToolPanel`（`:96-108`）调用。
- `usePersistentToolPanel` 仅被 `PersistentToolPanelHost`（`:110-138`）使用。
- grep 确认全仓无其他订阅者（见调研过程）。
- `PersistentToolPanelHost` 是 `ChatView` 的直接子节点（`ChatView.tsx:501`），与消息列表平级，渲染在 `document.body` portal。
- 结论：`updatePersistentToolPanel` 的 emit 只触发 `PersistentToolPanelHost` 重渲染，进而连带重渲染 `panel.children`（可能是 `SubagentPanelContent` 或 thinking/summary 内容）。

### 6. `subagentPanelStore` 订阅者

**唯一订阅者是 `useSubagentPanelData`（`SubagentBlocks.tsx:70-79`），仅被 `SubagentPanelContent`（`:408`）使用。**

- grep 确认全仓无其他订阅者。
- `openSubagentPanelByAgentId`（`:297-322`）只读 `store.get`，不订阅。
- 结论：`subagentPanelStore.set` 的 emit 只触发 `SubagentPanelContent` 重渲染。

### 7. `SubagentPanelContent` 内 `ResizeObserver`（`SubagentBlocks.tsx:468-491`）

```tsx
useEffect(() => {
  const scroller = scrollRef.current;
  if (!scroller || typeof ResizeObserver === "undefined") return;
  const observer = new ResizeObserver(() => {
    if (shouldAutoScrollSubagentPanel({ scroller, userScrolledUp: userScrolledUpRef.current })) {
      startAutoScroll();
    }
  });
  observer.observe(scroller);
  if (contentRef.current) observer.observe(contentRef.current);
  return () => observer.disconnect();
}, [startAutoScroll]);
```

- 依赖 `[startAutoScroll]`（稳定）⇒ observer 只在挂载时创建一次。
- 回调内 `startAutoScroll()` 不触发 React setState（只设 `scrollTop` + `setInterval`）。
- 结论：非循环源。但内容高度变化时会被触发，与 `useLayoutEffect` 叠加可能加剧同步滚动压力。

### 8. `SubagentBlock` 卸载清理 effect（`SubagentBlocks.tsx:743-747`）

```tsx
useEffect(() => {
  return () => { subagentPanelStore.delete(agent_id); };
}, [agent_id]);
```

- 仅卸载时触发，非循环源。
- 但注意：`store.delete` 也 emit（`subagentPanelStore.ts:38-43`），若 `SubagentPanelContent` 仍在订阅则会 forceRender 一次（无害，因为 panel 通常已关闭）。

## 汇总

| 组件 | 有 effect? | 调 store.set/updatePersistentToolPanel? | 依赖不稳定引用? | 循环源? |
|---|---|---|---|---|
| `SubagentBlock` | 是 | `subagentPanelStore.set` + `updatePersistentToolPanel` | `parts`（每事件新引用） | **是（主）** |
| `ThinkingBlock` | 是 | `updatePersistentToolPanel` | `content`（流式变，但真实变化） | 否（panelKey 早退 + 真实变化） |
| `SummaryItem` | 是 | `updatePersistentToolPanel` | `content` | 否（同上） |
| `TodoBlock` | 否 | 否 | — | 否 |
| `MessagePartRenderer` | 否 | 否 | — | 否 |
| `SubagentPanelContent` | 是（`useLayoutEffect` 无依赖） | 否（只读 store） | — | 否（不触发 setState，但阻塞） |
| `PersistentToolPanelHost` | 否（只订阅） | 否 | — | 否（被动重渲染） |

## 结论

**唯一符合"effect 依赖不稳定引用 + store.set 无脏检查"模式的组件是 `SubagentBlock`。** `ThinkingBlock` / `SummaryItem` 形态相似，但：
1. 依赖的 `content` 是真实内容变化（流式追加），emit 合理；
2. `panelKey` 早退保护，只在对应 panel 打开时生效；
3. `updatePersistentToolPanel` 走 `createSingletonStore`，有 `Object.is` 兜底（同引用不 emit）。

因此修复应聚焦 `SubagentBlock` + `subagentPanelStore` + `SubagentPanelContent` 的 `useLayoutEffect`，无需改动 `ThinkingBlock` / `SummaryItem` / `TodoBlock` / `MessagePartRenderer`。

## Caveats

- 若 implement 阶段发现 thinking/summary panel 与 subagent panel 同时打开时仍有高频重渲染，可考虑给 `updatePersistentToolPanel` 的 updater 加字段级比较（返回 `prev` 当 `status`/`subtitle` 未变），但这属于优化，非循环根因。
