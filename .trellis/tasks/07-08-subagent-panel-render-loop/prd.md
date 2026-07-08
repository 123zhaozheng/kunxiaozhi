# PRD: 修复子代理面板渲染循环 (Maximum update depth exceeded)

## 背景

fast_agent 会话调用子任务（subagent）时，前端报 `Maximum update depth exceeded`，子代理面板无法正常渲染，ErrorBoundary 捕获后页面崩溃。刷新后有 1-2 秒正常渲染，流式事件到达后再次报错。

## 问题现象

- 触发场景：fast_agent 会话中调用子任务，子任务运行过程中
- 报错：`Maximum update depth exceeded`
- 调用栈（来自浏览器控制台）：
  ```
  at listener (SubagentBlocks.tsx:74:28)
  at subagentPanelStore.ts:34:38
  at Set.forEach (<anonymous>)
  at emit (subagentPanelStore.ts:34:16)
  at Object.set (subagentPanelStore.ts:49:7)
  at SubagentBlocks.tsx:682:24
  ```
- 控制台 `[handleStreamEvent] Received event` 高频刷屏
- 刷新后短暂正常（1-2 秒），流式恢复后复现

## 初步根因（待 research 确认）

1. **`SubagentBlock` effect 依赖 `parts` 数组引用**（`SubagentBlocks.tsx:681-741`）：子任务流式时每个 SSE 事件都让 `parts` 变成新引用，effect 高频触发。
2. **`subagentPanelStore.set` 无脏检查**（`subagentPanelStore.ts:47-50`）：相同数据仍同步 `emit`，触发订阅组件 `forceRender`。
3. **`SubagentPanelContent` 的 `useLayoutEffect` 无依赖数组**（`SubagentBlocks.tsx:455-466`）：每次渲染都同步执行 `scrollToBottom`，把 store 更新拉入同步渲染阶段。
4. **`updatePersistentToolPanel` 无脏检查**（`persistentToolPanelState.tsx:80-88`）：可能放大 forceRender 频率。

## 验收标准

- [ ] fast_agent 子任务运行时不再报 `Maximum update depth exceeded`
- [ ] 子代理面板内容在流式过程中正确更新（不丢内容、不卡顿）
- [ ] 子代理面板自动滚动到底部行为正常
- [ ] 刷新页面后历史子任务渲染正常
- [ ] 不影响非子任务场景（普通对话、search_agent、沙箱）的现有行为
- [ ] 相关单元测试通过

## 约束

- 仅修改前端代码（`frontend/src`）
- 不改变子代理面板的现有交互行为
- 修复需从根源切断循环，而非仅抑制报错
- 不引入新的外部依赖
