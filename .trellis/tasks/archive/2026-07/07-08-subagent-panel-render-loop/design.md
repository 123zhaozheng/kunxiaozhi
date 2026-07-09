# Design: 子代理面板渲染循环修复

## 根因总结（来自 research）

不是简单的两点重入，而是**高频级联 setState + 同步阻塞**导致 React 触发 update depth 保护：

1. 每个 SSE 事件 → `setMessages` → 新 `message.parts` 引用 → `SubagentBlock` 重渲染。
2. `SubagentBlock` effect 依赖 `parts`（`SubagentBlocks.tsx:732`）→ 每事件重跑 → 同步调 `subagentPanelStore.set`（无脏检查）+ `updatePersistentToolPanel`。
3. 两个 `set` 在 effect 内同步 `emit` → `SubagentPanelContent.forceRender` + `PersistentToolPanelHost.forceRender`。
4. `SubagentPanelContent` 的 `useLayoutEffect`（`SubagentBlocks.tsx:455-466`，**无依赖数组**）每次渲染同步跑 `scrollToBottom`，阻塞主线程。
5. SSE 高频到达 + 级联 emit 累积超 50 次阈值 → `Maximum update depth exceeded`。

嵌套子代理（Rubric Grader `depth=1`）可能形成严格重入，但根因相同。

## 修复方案

### F1（核心）：`subagentPanelStore.set` 加脏检查

**文件**：`frontend/src/components/chat/ChatMessage/subagentPanelStore.ts:47-50`

**现状**：
```ts
set(next) {
  data.set(next.agentId, next);
  emit(next.agentId);   // 无比较
}
```

**改为**：
```ts
set(next) {
  const prev = data.get(next.agentId);
  if (prev && shallowEqualPanelData(prev, next)) return;  // 内容相同：不 set 不 emit
  data.set(next.agentId, next);
  emit(next.agentId);
}
```

**`shallowEqualPanelData` 设计**：
- 非数组字段（`agentId`/`agentName`/`input`/`result`/`success`/`error`/`isPending`/`startedAt`/`completedAt`/`status`）用 `===`。
- `parts` 用 `JSON.stringify(a.parts) === JSON.stringify(b.parts)` 比较。

**为什么 parts 用 JSON.stringify 而非引用比较或逐字段浅比较**：
- `parts` 每个 SSE 事件都是新引用（`messageParts.ts:219-221` 等总返回新数组），引用比较必失败。
- 逐字段浅比较需覆盖所有 part 类型（text/thinking/tool/subagent/sandbox/todo/summary）的关键字段，漏字段会导致 panel 漏内容（research F2 风险）。
- `JSON.stringify` 覆盖所有字段，无遗漏风险；parts 体量小（子代理 parts 通常 < 50 个），stringify 成本可接受（< 1ms/次）。
- 这使 F2（SubagentBlock effect 内容比较）成为非必要，简化修复。

### F3：`useLayoutEffect` 加依赖

**文件**：`frontend/src/components/chat/ChatMessage/SubagentBlocks.tsx:455-466`

**现状**：
```tsx
useLayoutEffect(() => {
  if (!shouldAutoScrollSubagentPanel({...})) return;
  scrollToBottom();
});  // 无依赖
```

**改为**：
```tsx
useLayoutEffect(() => {
  if (!shouldAutoScrollSubagentPanel({...})) return;
  scrollToBottom();
}, [data, scrollToBottom]);
```

**原理**：F1 到位后，`data` 引用只在内容变化时变（`store.set` 内容相同不 emit 不 set）。依赖 `[data]` 使 useLayoutEffect 只在内容真正变化时滚动，消除 `PersistentToolPanelHost` forceRender（panelStore emit）连带触发的无效同步滚动。`scrollToBottom` 是 `useCallback`，引用稳定。

### F4：`updatePersistentToolPanel` updater 内比较

**文件**：`frontend/src/components/chat/ChatMessage/SubagentBlocks.tsx:697-705`

**现状**：
```tsx
updatePersistentToolPanel(
  (prev) => ({ ...prev, status: panelStatus, subtitle }),
  panelKey,
);
```

**改为**：
```tsx
updatePersistentToolPanel(
  (prev) =>
    prev.status === panelStatus && prev.subtitle === subtitle
      ? prev
      : { ...prev, status: panelStatus, subtitle },
  panelKey,
);
```

**原理**：updater 返回 `prev` 时，`createSingletonStore.set`（`createSingletonStore.ts:18`）的 `Object.is` 判断相同，不 emit。消除 status/subtitle 未变时的 `PersistentToolPanelHost` forceRender。

## 不做的修复及理由

- **F2（SubagentBlock effect 内容比较）**：F1 用 JSON.stringify 已覆盖 parts 内容比较，effect 仍会每事件调用 `store.set`，但 store 内容相同不 emit 不 set，无副作用。F2 非必要。
- **F5（parts 引用稳定化，数据流层）**：影响所有 message 事件处理（主 agent + subagent + history loader），回归风险高。F1 已在消费端解决。

## 数据流与兼容性

- **数据流不变**：`eventHandlers.ts` → `processMessageEvent` → `messageParts.ts` 的 parts 仍每次新引用，但 `subagentPanelStore.set` 的脏检查在消费端拦截无效 emit。
- **交互行为不变**：panel 自动打开、滚动到底部、状态更新逻辑均保留。
- **影响范围**：仅 subagent panel 路径。普通对话、search_agent、沙箱不受影响（不经过 `subagentPanelStore`）。
- **嵌套子代理**：F1 的脏检查对嵌套 agentId 同样生效，切断嵌套重入路径。

## 测试策略

- **F1 单元测试**：扩展 `frontend/src/components/chat/ChatMessage/__tests__/subagentPanelStore.test.ts`（若不存在则新建），覆盖：
  - 相同字段值不 emit
  - 任一字段变化才 emit
  - parts 内容相同（不同引用）不 emit
  - parts 内容变化才 emit
- **F4 单元测试**：扩展 `persistentToolPanelState` 相关测试，覆盖 status/subtitle 未变时不 emit。
- **手动验证**：fast_agent 触发子任务（含嵌套 rubric grader），观察：
  - 控制台无 `Maximum update depth exceeded`
  - `[handleStreamEvent]` 不再导致循环（事件仍高频，但无报错）
  - panel 内容正确流式更新
  - 自动滚动正常
- **回归**：普通对话、search_agent、沙箱场景不受影响。

## 风险

- **JSON.stringify 性能**：极端情况下 parts 很大时 stringify 成本。子代理 parts 体量小，可接受；若后续发现问题可换用 parts 指纹（length + 各 part type + content 长度）。
- **F3 依赖漏"长度不变但内容变"**：`[data]` 依赖 data 引用，F1 保证 data 引用只在内容变化时变，故不会漏。
- **F4 比较字段不全**：仅比较 `status`/`subtitle`（updater 只改这两个字段），完整。
