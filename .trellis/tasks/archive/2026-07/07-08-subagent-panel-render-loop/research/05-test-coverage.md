# Research: Existing Test Coverage

- **Query**: 搜索 `frontend/src/**/__tests__/` 里 `SubagentBlocks`、`subagentPanelStore`、`persistentToolPanelState`、`useMessageScroll` 相关测试，记录路径供 implement 维护。
- **Scope**: internal
- **Date**: 2026-07-08

## 相关测试文件

### 1. `subagentPanelStore`

**文件**：`frontend/src/components/chat/ChatMessage/__tests__/subagentPanelStore.test.ts`

**覆盖范围**（node:test）：
- `notifies only listeners subscribed to the updated agent id`（`:17-27`）—— 验证按 `agentId` 隔离通知。
- `notifies listeners when an agent entry is deleted`（`:29-40`）—— 验证 `delete` 触发 emit。
- `tracks current store size`（`:42-50`）—— 验证 `size()`。

**未覆盖（implement 需补）**：
- `set` 相同数据（同 `agentId`、同字段值）不 emit（脏检查）。
- `set` 部分字段变化时 emit。
- 嵌套 `parts` 引用变化但内容相同时的行为（取决于修复方案）。

### 2. `SubagentBlocks`（纯函数部分）

**文件**：`frontend/src/components/chat/ChatMessage/__tests__/subagentBlocks.test.ts`

**覆盖范围**：
- `buildSubagentPanelState` 的 `subtitle` 只显示开始时间（`:9-35`）。
- `getSubagentRoleIconMeta` 角色名匹配（`:37-42`）。
- `getSubagentAvatarImageUrl` 头像 URL 处理（`:44-51`）。

**未覆盖**：
- `SubagentBlock` 组件的 effect 行为（store.set 调用、脏检查）。
- `SubagentPanelContent` 的 `useLayoutEffect` 滚动行为（组件级，需 RTL）。
- `useSubagentPanelData` 的订阅/forceRender。
- `openSubagentPanelByAgentId` 的 panel 打开逻辑。

### 3. `persistentToolPanelState`

**文件**：`frontend/src/components/chat/ChatMessage/items/__tests__/persistentToolPanelState.test.ts`

**覆盖范围**：
- `keyed panel updates do not replace another open panel`（`:11-33`）—— 验证 `panelKey` 早退。
- `same-reference panel update does not notify listeners`（`:35-57`）—— 验证 `Object.is` 脏检查（`(prev) => prev` 不 emit）。

**未覆盖**：
- updater 返回新对象但字段值相同时的行为（嫌疑点 4 的修复需补此测试）。
- `openPersistentToolPanel` 的 `auto` + mobile 分支。

### 4. `subagentPanelScroll`

**文件**：`frontend/src/components/chat/ChatMessage/__tests__/subagentPanelScroll.test.ts`

**覆盖范围**：
- `isNearSubagentPanelBottom` 阈值判断（`:9-27`）。
- `shouldAutoScrollSubagentPanel` 用户上滑禁用（`:29-51`）。
- `startSubagentPanelScrollToBottom` 内容高度变化时持续滚动（`:63-84`）。

**未覆盖**：
- `maxAttempts` 达到上限后停止。
- `shouldAbort` 回调终止。

### 5. `subagentPanelControl`

**文件**：`frontend/src/components/chat/ChatMessage/__tests__/subagentPanelControl.test.ts`

（存在，未详读，但 grep 确认覆盖 `shouldAutoOpenSubagentPanel` / `dismissSubagentPanelAutoOpen` 等纯函数。）

### 6. `useMessageScroll`

**文件**：
- `frontend/src/components/layout/AppContent/__tests__/useMessageScroll.test.ts`
- `frontend/src/components/layout/AppContent/__tests__/messageScrollSessionReset.test.ts`

（存在，与 subagent panel 滚动独立，是主消息列表的滚动 hook。修复 subagent 循环不应影响这些测试。）

### 7. `eventProcessor` / `eventHandlers`

**文件**：
- `frontend/src/hooks/useAgent/__tests__/eventProcessor.test.ts`
- `frontend/src/hooks/useAgent/__tests__/eventHandlers.test.ts`
- `frontend/src/hooks/useAgent/__tests__/eventHandlers.userMessageTimestamp.test.ts`

（存在，覆盖 `processMessageEvent` 与 SSE 事件处理。修复 subagent 循环若不动 `messageParts.ts` / `eventProcessor.ts` 的数据流，则不影响这些测试。）

## 测试栈说明

- 上述测试均使用 `node:test`（Node 原生 test runner），非 Jest/Vitest。
- 纯函数测试直接 import 源 `.ts` 文件（如 `import {...} from "../subagentPanelStore.ts"`）。
- 组件级测试（`SubagentBlock` / `SubagentPanelContent` 的 effect）目前在仓库内**未见**，implement 若需测组件行为需引入 RTL（`@testing-library/react`）或改用 React act + render 测试。建议优先补纯函数级测试（`subagentPanelStore` 脏检查），组件级行为靠手动验证。

## implement 维护建议

| 修复点 | 需更新/新增的测试 |
|---|---|
| `subagentPanelStore.set` 加脏检查 | 扩展 `subagentPanelStore.test.ts`：新增"相同数据不 emit""字段变化才 emit" |
| `SubagentBlock` effect 跳过无变化 set | 新增组件测试或重构出纯函数 `shouldUpdatePanelData(prev, next)` 测试 |
| `useLayoutEffect` 加依赖 | `subagentPanelScroll.test.ts` 已覆盖滚动逻辑，组件级需手动验证 |
| `updatePersistentToolPanel` 字段比较 | 扩展 `persistentToolPanelState.test.ts`：新增"updater 返回新对象但字段相同不 emit" |
