# Design: 子智能体面板帧级合并通知

## Root Cause

`SubagentBlock` 在消息树每次流式变化后通过 effect 写入 `subagentPanelStore`。store 当前同步遍历 listener，而 `SubagentPanelContent` 的 listener 又立即调用 `forceRender`。旧修复只能过滤内容完全相同的数据；真实 token 分片每次都改变 `parts`，所以高频长流仍持续形成 React effect 到 state update 的同步级联。

同时，store 为判断 `parts` 内容是否相等而对完整数组执行 `JSON.stringify`。随着 thinking 内容增长，这个比较在每个 token 上重复扫描完整历史内容，放大主线程阻塞，使事件更容易积压并在一次 React 工作周期内集中处理。

## Proposed Design

### 1. 快照立即更新，通知按帧合并

`subagentPanelStore.set/delete` 仍同步修改内部 `Map`，保证任何调用者立刻 `get()` 都得到最新值。listener 通知改为调度到下一次 animation frame：

- 每个 agent 只保留一个 pending 标记；
- 同一帧内反复 `set` 只加入一次 dirty agent 集合；
- flush 时按 agent 通知当前订阅者；
- 多次写入采用 last-write-wins，订阅者读取最终快照；
- `delete` 使用同一调度路径，因此不会与此前待发送的 `set` 通知打架。

调度器作为 store 构造参数注入，生产环境使用 `requestAnimationFrame`，无 DOM 环境使用可用的异步 fallback；测试使用可控调度器确定性 flush。

### 2. 使用 `useSyncExternalStore`

`useSubagentPanelData(agentId)` 改用 `useSyncExternalStore`：

- `subscribe` 只订阅当前 agent；
- `getSnapshot` 返回 `subagentPanelStore.get(agentId)`；
- agentId 改变时 React 负责正确解绑和重订阅；
- 移除手工 `forceRender(n => n + 1)` 状态桥接。

帧级异步通知切断 `SubagentBlock` passive effect 内同步触发另一个组件 state update 的链路；标准外部 store hook 保证快照一致性。

### 3. 采用引用相等契约

保留标量字段逐项比较，但 `parts` 仅比较引用：

- 消息处理器已采用不可变更新，真实内容变化会生成新的 `parts` 引用；
- 无关重渲染沿用同一引用，可直接跳过；
- 不再支持“新建一个内容相同的 parts 数组也视为相同”的昂贵深比较。

这是明确的内部契约，并由测试覆盖。

## Edge Cases

- listener 在 flush 前取消订阅：flush 读取最新 listener 集合，不调用已移除回调。
- agent 在 flush 前被删除：listener 被通知一次并读取 `undefined`。
- flush 回调中再次写 store：写入进入下一帧，不在当前 flush 中递归通知。
- 多 agent 同帧更新：共用一次 frame 调度，但逐个 agent 隔离通知。
- 页面卸载或无 `requestAnimationFrame`：fallback 保持异步边界。

## Verification

- 扩展 `subagentPanelStore` 单元测试，覆盖 burst、last-write-wins、删除、取消订阅、跨 agent 隔离和引用相等契约。
- 运行目标测试、前端 type-check/lint。
- 浏览器中使用高频子智能体会话复测，观察面板持续更新、页面可交互、控制台无更新深度错误。

## Risks

- 面板可见更新最多延迟一个浏览器帧，约 16ms；这是有意的合并窗口，不影响数据完整性。
- 若未来有调用方原地修改 `parts`，引用比较会漏报；当前消息处理代码必须继续遵守不可变更新约定。
