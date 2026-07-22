# Implementation Plan

## 1. Store 调度与相等判断

- [x] 为 `createSubagentPanelStore` 增加可注入的通知调度器。
- [x] 实现 dirty agent 集合和单帧 flush，保证快照立即写入、通知帧级合并。
- [x] 将 `parts` 深序列化比较改为引用比较，保留标量字段比较。
- [x] 正确处理 delete、unsubscribe 及 flush 中再次写入。

## 2. React 订阅

- [x] 将 `useSubagentPanelData` 从 `useState + useEffect + forceRender` 改为 `useSyncExternalStore`。
- [x] 保持 agentId 切换和服务端快照行为稳定。

## 3. Tests

- [x] 调整现有 store 测试以显式使用同步/可控调度器。
- [x] 新增单帧 100 次不同更新仅通知一次的回归测试。
- [x] 新增 last-write-wins、set-delete、跨 agent、取消订阅和下一帧重入测试。
- [x] 更新 `parts` 引用相等契约测试。

## 4. Verification

- [x] 运行目标单元测试。
- [x] 运行前端 type-check 和全量 lint。
- [x] 浏览器复测目标会话加载、子智能体面板及控制台错误状态。
- [x] 确认没有混入当前工作树内其他 task 的改动。
