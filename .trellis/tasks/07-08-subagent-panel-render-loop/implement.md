# Implement: 子代理面板渲染循环修复

## 执行清单

### 1. F1 — `subagentPanelStore.set` 脏检查
- [ ] 文件：`frontend/src/components/chat/ChatMessage/subagentPanelStore.ts`
- [ ] 新增 `shallowEqualPanelData(a, b)` 函数：非数组字段 `===`，`parts` 用 `JSON.stringify` 比较
- [ ] `set` 方法加脏检查：`const prev = data.get(next.agentId); if (prev && shallowEqualPanelData(prev, next)) return;`
- [ ] 验证：单元测试（见下）

### 2. F3 — `useLayoutEffect` 加依赖
- [ ] 文件：`frontend/src/components/chat/ChatMessage/SubagentBlocks.tsx:455-466`
- [ ] `useLayoutEffect` 依赖数组改为 `[data, scrollToBottom]`
- [ ] 验证：手动触发子任务，观察滚动正常

### 3. F4 — `updatePersistentToolPanel` updater 内比较
- [ ] 文件：`frontend/src/components/chat/ChatMessage/SubagentBlocks.tsx:697-705`
- [ ] updater 改为：`prev.status === panelStatus && prev.subtitle === subtitle ? prev : {...prev, status: panelStatus, subtitle}`
- [ ] 验证：单元测试（见下）

### 4. 单元测试
- [ ] `subagentPanelStore.test.ts`：扩展/新建，覆盖 F1（相同不 emit / 字段变 emit / parts 内容同不 emit / parts 内容变 emit）
- [ ] `persistentToolPanelState.test.ts`：扩展，覆盖 F4（status/subtitle 未变不 emit）
- [ ] 运行：`cd frontend && npx tsx --test src/components/chat/ChatMessage/__tests__/subagentPanelStore.test.ts src/components/chat/ChatMessage/items/__tests__/persistentToolPanelState.test.ts`

### 5. 手动验证
- [ ] fast_agent 触发子任务，观察无 `Maximum update depth exceeded`
- [ ] panel 内容正确流式更新
- [ ] 自动滚动正常
- [ ] 刷新后历史子任务渲染正常

## 验证命令

```bash
# 类型检查
cd frontend && npx tsc --noEmit

# 单元测试（项目用 node:test，经 tsx 运行，非 vitest）
cd frontend && npx tsx --test src/components/chat/ChatMessage/__tests__/subagentPanelStore.test.ts src/components/chat/ChatMessage/items/__tests__/persistentToolPanelState.test.ts

# Lint（若项目配置）
cd frontend && npm run lint
```

## Review Gate

- [ ] F1 脏检查覆盖所有 `SubagentPanelData` 字段（`agentId`/`agentName`/`input`/`result`/`success`/`error`/`isPending`/`parts`/`startedAt`/`completedAt`/`status`）
- [ ] F3 依赖 `[data, scrollToBottom]`，`scrollToBottom` 为 useCallback 稳定引用
- [ ] F4 updater 仅在 `status`/`subtitle` 变化时返回新对象
- [ ] 无新增 lint 警告
- [ ] 单元测试全绿

## Rollback

修复集中在 3 个文件（`subagentPanelStore.ts`、`SubagentBlocks.tsx`、测试文件），git revert 即可完整回滚。无数据迁移、无不可逆操作。

## 注意

- 不要改 `messageParts.ts` / `eventProcessor.ts`（F5 已排除）
- 不要改 `SubagentBlock` 的 effect 依赖数组（F2 已排除，F1 兜底）
- 保持 `SubagentPanelContent` 的 `ResizeObserver`、`handleScroll` 等其他逻辑不变
