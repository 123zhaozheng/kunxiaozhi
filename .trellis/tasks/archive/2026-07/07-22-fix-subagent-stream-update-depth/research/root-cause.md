# Root Cause Research

## Historical fix

2026-07-08 的提交 `25463394` 为 `subagentPanelStore.set` 增加内容相等判断，并让持久面板的 status/subtitle updater 在无变化时返回原对象。该修复降低了重复 emit，但没有改变生产者 effect 同步通知 React listener 的结构，也没有对真实的高频内容变化做合并。

## Live evidence (2026-07-22)

会话：`3fe75f48-c462-4abb-aa23-02ed30387bf5`（「文档生成 📄」）。

- 读取到的前 5000 条事件中：4726 条 depth=1 thinking、43 条 depth=1 tool:start、43 条 depth=1 tool:result。
- 只有 1 条 agent:call，时间为 08:42:15.562Z，agent 为 `general-purpose_f337cf4e-8555-7336-dbd5-cd5506528ddf`。
- 峰值事件速率为 166 events/s；多个秒级窗口超过 100 events/s。
- 未发现重复 agent:call、同深度并行 agent，或子事件早于 agent:call。

因此，本会话不支持“消息深度路由错误导致循环”的假设。此前另一个会话控制台中的 `No matching subagent found for depth: 1` 是独立告警，不应混入本次根因。

## Confirmed update path

1. 每个子智能体 token 更新消息 `parts`。
2. 主消息树重渲染 `SubagentBlock`。
3. `SubagentBlock` effect 调用 `subagentPanelStore.set`。
4. 新 token 使 `parts` 内容不同，旧的脏检查无法跳过。
5. store 同步 emit，面板 listener 立即 `forceRender`。
6. 长流持续重复该 React effect/state 级联；事件积压时会在密集 React 工作周期内触发更新深度保护。

此外，每次 set 都 `JSON.stringify` 已增长的完整 parts，形成随流长度上升的重复扫描，显著放大卡顿。

## Conclusion

旧修复处理的是“重复数据”，本次复现暴露的是“高频但每次都不同的数据”。修复边界应放在 store 通知层：保留最后快照，按 animation frame 合并通知，并采用不可变引用契约消除完整序列化比较。

## Break-the-loop analysis

### 1. Root Cause Category

- **Category: D — Test Coverage Gap**：旧测试只覆盖相同数据是否跳过 emit，没有覆盖一帧内大量“内容不同”的真实 token 更新。
- **Category: E — Implicit Assumption**：旧实现隐含假设有效 SSE 更新频率不会高到持续触发 React effect/state 级联，且对增长中的 `parts` 做完整序列化成本可接受。

### 2. Why the previous fix failed

1. 旧脏检查属于表层修复，只降低重复数据的 emit；真实流每个 token 都不同，因此完全绕过。
2. 旧研究把“高频”识别出来了，但没有把频率约束写成 store 的调度架构和可执行 burst 测试。
3. `JSON.stringify(parts)` 在短 fixture 中没有性能问题，测试没有模拟不断增长的长 thinking 内容。

### 3. Prevention Mechanisms

| Priority | Mechanism | Specific Action | Status |
|---|---|---|---|
| P0 | Architecture | Snapshot synchronous, notifications coalesced per animation frame | DONE |
| P0 | React contract | Consume the store through `useSyncExternalStore` | DONE |
| P0 | Test coverage | Assert 100 writes in one frame produce one final notification | DONE |
| P1 | Performance contract | Compare immutable `parts` references instead of serializing history | DONE |
| P1 | Documentation | Record the streaming-store contract in frontend state-management spec | DONE |

### 4. Systematic Expansion

- **Similar issues**：其他 `createSingletonStore` 使用点目前多为低频用户操作；若以后承接 SSE、ResizeObserver 或高频 progress，也必须采用相同的帧级合并边界。
- **Design improvement**：区分快照更新频率和 UI 提交频率，数据完整性不要求每个 token 都触发一次 React commit。
- **Process improvement**：性能或循环更新修复必须包含 burst fixture，不能只测相同输入和单次调用。

### 5. Knowledge Capture

- [x] 更新 `.trellis/spec/frontend/state-management.md`。
- [x] 在 store 单测中加入 burst、delete、unsubscribe、cross-agent 和 re-entry 场景。
- [x] 保留本任务的真实事件速率与旧修复失效原因。
- [ ] 模板目录 `src/templates/markdown/spec/` 在当前仓库不存在，无法同步模板副本。
