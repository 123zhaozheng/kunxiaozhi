# 修复子智能体流式面板更新深度溢出

## Goal

修复子智能体高频流式输出期间前端偶发卡死并抛出 `Maximum update depth exceeded` 的问题，同时保持面板内容、状态与自动滚动及时更新。

## Background

- 2026-07-22 的「文档生成 📄」会话中，单次子智能体调用产生了至少 5000 条持久化事件，其中 4726 条为 depth=1 的 `thinking` 分片，峰值为每秒 166 条。
- 该会话只有一次、且顺序正确的 `agent:call`；本次问题不是重复创建子智能体或父子事件错序导致。
- 旧修复会对完全相同的面板数据跳过通知，但真实流式 token 每次都会改变 `parts`，因此仍会执行 `SubagentBlock effect -> subagentPanelStore.set -> listener forceRender`。
- 旧实现还会在每次更新时 `JSON.stringify` 不断增长的 `parts`，使长流的比较成本持续上升。

## Requirements

- 子智能体面板 store 必须立即保存最新快照，但同一浏览器帧内的多次写入只通知订阅者一次。
- React 订阅必须使用外部 store 的标准订阅模型，避免在生产者 effect 中同步触发手工 `setState` 回写。
- 数据相等判断不得序列化完整 `parts`；应依赖不可变更新产生的引用变化和标量字段比较。
- 删除、切换 agent、取消订阅后不得发生陈旧通知、内存泄漏或串台。
- 不改变 SSE 事件协议、消息树路由、面板展示内容及自动滚动语义。

## Acceptance Criteria

- [ ] 同一 agent 在单帧内连续写入 100 次不同快照时，订阅回调只触发一次，读取到最后一次快照。
- [ ] 不同 agent 的更新分别通知各自订阅者，不串台。
- [ ] `set -> delete` 在同一帧发生时，最终快照为 `undefined`，且最多通知一次。
- [ ] 相同引用和相同标量数据不产生冗余通知；`parts` 引用或任一标量变化会更新快照。
- [ ] 子智能体正常流式输出、完成、失败和取消时，面板状态及内容正确。
- [ ] 复测长 thinking 流时控制台不再出现 `Maximum update depth exceeded`，页面保持可交互。
- [ ] 相关单元测试、前端 lint/type-check 通过。

## Out of Scope

- 修改后端 token 粒度或 SSE 协议。
- 重构全部聊天消息状态管理。
- 处理另一会话中出现的 `No matching subagent found for depth` 路由告警；现有复现会话没有该错序。
