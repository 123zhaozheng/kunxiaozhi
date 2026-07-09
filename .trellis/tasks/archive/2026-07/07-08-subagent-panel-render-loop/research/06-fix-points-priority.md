# Research: Fix Points Priority

- **Query**: 按"止血"和"治本"分类，给出每个修复点的必要性、风险、依赖关系。
- **Scope**: internal
- **Date**: 2026-07-08

## 修复点总览

| # | 修复点 | 文件:行 | 类别 | 必要性 | 风险 | 依赖 |
|---|---|---|---|---|---|---|
| F1 | `subagentPanelStore.set` 加脏检查 | `subagentPanelStore.ts:47-50` | 治本 | 高 | 低 | 无 |
| F2 | `SubagentBlock` effect 跳过无变化的 `parts` 内容 | `SubagentBlocks.tsx:681-741` | 治本 | 高 | 中 | 依赖 F1 或独立浅比较 |
| F3 | `useLayoutEffect` 加依赖（只在内容变化时滚动） | `SubagentBlocks.tsx:455-466` | 止血 + 治本 | 中-高 | 低 | 无 |
| F4 | `updatePersistentToolPanel` 调用前比较 `status`/`subtitle` | `SubagentBlocks.tsx:697-705` | 治本（次要） | 中 | 低 | 无 |
| F5 | `parts` 引用稳定化（数据流层） | `messageParts.ts` / `eventProcessor.ts` | 治本（深层） | 低 | 高 | 不建议 |

---

## F1：`subagentPanelStore.set` 加脏检查（治本，优先级最高）

### 现状
`subagentPanelStore.ts:47-50`：
```ts
set(next) {
  data.set(next.agentId, next);
  emit(next.agentId);   // 无比较
}
```

### 方案
在 `set` 内对 `next` 与 `data.get(next.agentId)` 做浅比较（字段级 `Object.is`），若所有字段相同则只 `data.set` 不 `emit`。

参考 `createSingletonStore.set`（`createSingletonStore.ts:17-23`）的 `Object.is` 整体比较——但 `subagentPanelStore` 的 `next` 总是新对象（`SubagentBlock` effect 构造），整体 `Object.is` 必失败，需字段级浅比较。

```ts
set(next) {
  const prev = data.get(next.agentId);
  data.set(next.agentId, next);
  if (prev && shallowEqualPanelData(prev, next)) return;  // 不 emit
  emit(next.agentId);
}
```

`shallowEqualPanelData` 需对 `parts` 特殊处理：`parts` 是数组，浅比较引用即可（因为 F2 会保证只在内容变化时才传新引用——但若不做 F2，`parts` 每事件新引用仍会触发 emit）。

### 必要性
高。这是循环的核心放大器。即使 F2/F3 不做，F1 也能消除"内容未变但 emit"的情况（但 `parts` 每事件新引用，F1 单独无法完全止血——需配合 F2）。

### 风险
低。脏检查是纯增量逻辑，不影响正常更新路径。需确保 `shallowEqualPanelData` 覆盖所有 `SubagentPanelData` 字段（`agentId`/`agentName`/`input`/`result`/`success`/`error`/`isPending`/`parts`/`startedAt`/`completedAt`/`status`）。

### 测试
扩展 `subagentPanelStore.test.ts`：
- `set` 相同字段值不 emit。
- `set` 任一字段变化才 emit。
- `parts` 引用变但长度/元素相同 → 取决于浅比较策略（建议 `parts` 用引用比较，配合 F2）。

---

## F2：`SubagentBlock` effect 跳过无变化的 `parts` 内容（治本）

### 现状
`SubagentBlocks.tsx:681-741`：effect 依赖 `parts`，每个 SSE 事件 `parts` 新引用 ⇒ effect 重跑 ⇒ 无条件 `store.set`。

### 方案
在 effect 内对 `parts` 做内容比较（或用 `useRef` 缓存上次 `parts` 的序列化/指纹），若内容未变则跳过 `store.set`。

可选实现：
1. **`useRef` + 浅比较 parts 元素**：缓存上次 `parts`，逐元素比较关键字段（`type`/`content`/`result`/`status`/`isPending`）。复杂度高，parts 嵌套深。
2. **依赖数组去掉 `parts`，改用 `parts` 的派生指纹**（如 `parts.length` + 最后一个 part 的 `content` 长度 + `status`）。简单但可能漏边界。
3. **`useMemo` 稳定 `parts` 引用**：在 `SubagentBlock` 内用 `useMemo` 基于 `parts` 内容计算稳定引用，传给 effect 依赖与 `store.set`。

### 必要性
高。`parts` 每事件新引用是 effect 高频重跑的根因。即使 F1 加了脏检查，`parts` 引用变 ⇒ 浅比较失败 ⇒ 仍 emit。需 F2 保证"内容未变时不传新引用"或"effect 不重跑"。

### 风险
中。内容比较若漏字段会导致 panel 不更新（漏内容）。需覆盖 `parts` 内所有 part 类型的关键字段（text: content; thinking: content+isStreaming; tool: result+success+isPending; subagent: 嵌套 parts+status+result; todo: items; summary: content; sandbox: status; ...）。

### 依赖
与 F1 配合最稳：F2 保证 `parts` 内容未变时 effect 不调 `store.set`（或传同引用），F1 兜底其他字段。

---

## F3：`useLayoutEffect` 加依赖，只在内容变化时滚动（止血 + 治本）

### 现状
`SubagentBlocks.tsx:455-466`：
```tsx
useLayoutEffect(() => {
  if (!shouldAutoScrollSubagentPanel({...})) return;
  scrollToBottom();
});   // 无依赖
```

### 方案
加依赖数组，依赖 `data`（或 `data.parts?.length` + `data.result` + `data.isPending`）：

```tsx
useLayoutEffect(() => {
  if (!shouldAutoScrollSubagentPanel({...})) return;
  scrollToBottom();
}, [data?.parts?.length, data?.result, data?.isPending, scrollToBottom]);
```

或改为 `useEffect`（非 layout）以避免同步阻塞（但滚动到底部用 `useLayoutEffect` 是为了避免闪烁，需权衡）。

### 必要性
中-高。止血价值：减少每次渲染的同步滚动阻塞，让 React 有机会 flush。治本价值：语义正确（内容变才滚）。

### 风险
低。`scrollToBottom` 已稳定（`useCallback`）。依赖 `data?.parts?.length` 可能漏"长度不变但内容变"的情况（如最后一个 part 内容追加），需同时依赖最后一个 part 的内容指纹，或直接依赖 `data` 引用（但 `data` 引用受 F1/F2 影响）。

### 依赖
若 F1/F2 到位，`data` 引用在内容未变时稳定，`useLayoutEffect` 依赖 `data` 即可。若 F1/F2 未做，`data` 每事件新引用 ⇒ `useLayoutEffect` 仍每事件跑（但至少不是每次渲染都跑）。

---

## F4：`updatePersistentToolPanel` 调用前比较 `status`/`subtitle`（治本，次要）

### 现状
`SubagentBlocks.tsx:697-705`：
```tsx
updatePersistentToolPanel((prev) => ({...prev, status: panelStatus, subtitle}), panelKey);
```
updater 总返回新对象 ⇒ `Object.is` 失败 ⇒ emit。

### 方案
在调用前比较 `currentPanel.status === panelStatus && currentPanel.subtitle === subtitle`，若相同则不调；或 updater 内返回 `prev`：
```tsx
updatePersistentToolPanel((prev) =>
  prev.status === panelStatus && prev.subtitle === subtitle
    ? prev
    : {...prev, status: panelStatus, subtitle},
  panelKey);
```
利用现有 `Object.is` 脏检查（`createSingletonStore.set`）。

### 必要性
中。次要放大器。F1/F2 到位后，`SubagentBlock` effect 重跑频率大降，F4 收益相对小。但仍能消除"status/subtitle 未变时的 `PersistentToolPanelHost` forceRender"。

### 风险
低。`status`/`subtitle` 是原始值，比较安全。

### 依赖
无。可独立做。

---

## F5：`parts` 引用稳定化（数据流层，不建议）

### 现状
`messageParts.ts` 的 `addPartToDepth` / `updateSubagentResult` / `updateToolResultInDepth` 等总返回新数组（即使内容可合并）。

### 方案
在这些函数内加内容比较，若更新后与更新前内容相同则返回原数组。

### 必要性
低。问题不在数据流（不可变更新是 React 正道），而在消费端（`SubagentBlock` effect + `store.set`）。改数据流影响面太大。

### 风险
高。影响所有 message 事件处理（主 agent + subagent + history loader），回归风险大。`eventProcessor.test.ts` / `eventHandlers.test.ts` 大量测试可能受影响。

### 依赖
不建议做。F1/F2 已能在消费端解决。

---

## 推荐修复顺序

1. **F1**（`subagentPanelStore` 脏检查）—— 最小改动，最大收益，先做。
2. **F2**（`SubagentBlock` effect 内容比较）—— 配合 F1，彻底切断"内容未变但 emit"。
3. **F3**（`useLayoutEffect` 依赖）—— 止血 + 语义正确，低风险。
4. **F4**（`updatePersistentToolPanel` 字段比较）—— 消除次要放大，低风险。

F5 不做。

## 验证策略

- 单元测试：F1 扩展 `subagentPanelStore.test.ts`；F4 扩展 `persistentToolPanelState.test.ts`。
- 手动验证：fast_agent 触发子任务（含嵌套 rubric grader），观察控制台无 `Maximum update depth exceeded`、`[handleStreamEvent]` 不再高频刷屏（或刷屏但无循环）、panel 内容正确更新、自动滚动正常。
- 回归：普通对话、search_agent、沙箱场景不受影响（F1/F2/F3/F4 均限于 subagent panel 路径）。
