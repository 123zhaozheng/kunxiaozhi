# Research: Validation of Initial Suspects

- **Query**: 对 4 个初步嫌疑点逐一验证，明确"确认/部分确认/否定"并给证据。
- **Scope**: internal
- **Date**: 2026-07-08

## 嫌疑点 1：`SubagentBlock` effect 依赖 `parts` 数组引用

**结论：确认。**

- 位置：`SubagentBlocks.tsx:681-741`，依赖数组 `:724-741` 含 `parts`（`:732`）。
- 证据：`parts` 流式时每个 SSE 事件都是新引用（见 `02-parts-reference-flow.md`），React `Object.is` 比较失败 ⇒ effect 重跑 ⇒ `subagentPanelStore.set`（`:682`）。
- effect 内 `store.set` 与 `updatePersistentToolPanel`（`:698`）均无条件执行（仅 `isPersistentToolPanelOpen(panelKey)` 分支选择），无脏检查。
- 这是循环的**高频触发源**，但本身不是重入（effect 重跑频率受 SSE 事件数限制）。

## 嫌疑点 2：`subagentPanelStore.set` 无脏检查

**结论：确认。**

- 位置：`subagentPanelStore.ts:47-50`：
  ```ts
  set(next) {
    data.set(next.agentId, next);
    emit(next.agentId);   // 无 Object.is / 浅比较
  }
  ```
- 对比 `createSingletonStore.set`（`createSingletonStore.ts:17-23`）有 `Object.is` 脏检查；`subagentPanelStore` 没有任何比较。
- 证据：`subagentPanelStore.test.ts` 现有测试只验证"通知订阅者"与"删除通知"，**未覆盖"相同数据不通知"**（见 `04-test-coverage.md`）。
- 后果：即使 `SubagentBlock` effect 因 `parts` 新引用重跑，但 store 数据实质未变（例如 `parts` 内容相同），仍 emit → `SubagentPanelContent.forceRender`。这是**循环的放大器**，把"effect 重跑"放大为"订阅组件重渲染"。
- 修复必要性：高。加浅比较即可消除大量无效 emit。

## 嫌疑点 3：`SubagentPanelContent` 的 `useLayoutEffect` 无依赖数组

**结论：确认（机制存在），但需修正"把 store 更新拉入同步渲染阶段"的描述。**

- 位置：`SubagentBlocks.tsx:455-466`：
  ```tsx
  useLayoutEffect(() => {
    if (!shouldAutoScrollSubagentPanel({ scroller: scrollRef.current, userScrolledUp: userScrolledUpRef.current })) {
      return;
    }
    scrollToBottom();
  });   // ← 无依赖数组
  ```
- 机制确认：无依赖数组 ⇒ 每次 `SubagentPanelContent` 渲染后都同步执行 `scrollToBottom` → `startAutoScroll` → `startSubagentPanelScrollToBottom`（`subagentPanelScroll.ts:56-114`），后者同步设 `scroller.scrollTop` + 启动 `setInterval`。
- 修正描述：`scrollToBottom` 本身**不触发 React setState**，因此不是直接的循环源。它的危害是：
  1. 每次渲染都同步滚动 + 创建新 `setInterval`（旧的由 `stopAutoScroll` 清理），阻塞主线程；
  2. 把 store emit 的副作用（forceRender 调度的渲染）拖入同步布局阶段，使 React 无法在事件循环中 flush pending updates，放大 update depth 累积。
- `scrollToBottom` 是 `useCallback([startAutoScroll])`，`startAutoScroll` 是 `useCallback([markProgrammaticScroll, stopAutoScroll])`，二者均稳定，因此 `scrollToBottom` 引用稳定。加 `[scrollToBottom]` 依赖不会改变触发频率（仍每次渲染后跑），但能避免 lint 警告；真正要改的是**只在 `data` 变化时滚动**（依赖 `data` 或 `data.parts?.length`）。
- 修复必要性：中-高。止血（减少同步阻塞）+ 治本（语义正确：内容变才滚）。

## 嫌疑点 4：`updatePersistentToolPanel` 无脏检查

**结论：部分确认（语义已部分保护，但仍有放大）。**

- 位置：`persistentToolPanelState.tsx:80-88`：
  ```tsx
  export function updatePersistentToolPanel(updater, panelKey?) {
    const currentPanel = panelStore.get();
    if (!currentPanel) return;
    if (panelKey && currentPanel.panelKey !== panelKey) return;   // ← panelKey 不匹配时早退
    panelStore.set(updater(currentPanel));   // ← updater 返回新对象则 emit
  }
  ```
- 已有的保护：
  1. `panelKey` 不匹配时早退（`:86`）—— 保护了"别的 panel 打开时不误更新"。
  2. `createSingletonStore.set` 有 `Object.is` 脏检查（`createSingletonStore.ts:18-20`）—— 若 updater 返回**同一引用**（`(prev) => prev`）则不 emit。`persistentToolPanelState.test.ts:35-57` 覆盖此场景。
- 仍有的放大：`SubagentBlock` effect（`SubagentBlocks.tsx:698-705`）的 updater 是 `(prev) => ({...prev, status: panelStatus, subtitle})`，**总是返回新对象**，即使 `status`/`subtitle` 与 `prev` 相同 ⇒ `Object.is` 失败 ⇒ emit ⇒ `PersistentToolPanelHost.forceRender` ⇒ `SubagentPanelContent`（作为 `panel.children`）连带重渲染。
- 后果：每个 SSE 事件额外触发一路 forceRender（`PersistentToolPanelHost`），与 `subagentPanelStore.set` 的 forceRender 叠加。
- 修复必要性：中。在 `updatePersistentToolPanel` 调用前比较 `panelStatus`/`subtitle` 是否变化，或在 updater 内返回 `prev`（利用现有 `Object.is`）。优先级低于嫌疑点 2（因为 `Object.is` 已兜底同引用情况）。

## 汇总表

| # | 嫌疑点 | 结论 | 证据 | 角色 |
|---|---|---|---|---|
| 1 | `SubagentBlock` effect 依赖 `parts` | 确认 | `SubagentBlocks.tsx:732` + `02-parts-reference-flow.md` | 高频触发源 |
| 2 | `subagentPanelStore.set` 无脏检查 | 确认 | `subagentPanelStore.ts:47-50`（无比较）vs `createSingletonStore.ts:18`（有 `Object.is`） | 放大器 |
| 3 | `useLayoutEffect` 无依赖数组 | 确认（机制），修正描述 | `SubagentBlocks.tsx:455-466`；`scrollToBottom` 不触发 setState，但阻塞同步阶段 | 同步阻塞 + 放大 |
| 4 | `updatePersistentToolPanel` 无脏检查 | 部分确认 | `persistentToolPanelState.tsx:80-88` + `createSingletonStore.ts:18`；updater 返回新对象仍 emit | 次要放大器 |
