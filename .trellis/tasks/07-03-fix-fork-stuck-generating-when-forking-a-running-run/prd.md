# fix fork stuck generating when forking a running run

## Goal

修复「在 AI 流式生成中点击消息分叉后，新会话 UI 永久卡在"正在生成"、输入框停在暂停键」的 bug。让 fork 出来的新会话始终是干净的"历史快照"状态，不会因源 run 还在 running 而被前端误判为有任务在运行。

## Requirements

- fork 任意消息（含正在生成的 assistant 回复、checkpoint）后，新会话 UI 立即空闲：无"正在生成"，输入框是发送键
- 新会话历史消息渲染正确（`isStreaming` 全为 false）
- 新会话 `metadata.current_run_id` 不再被 fork 写入（新会话没有运行中的 run）
- 新会话内克隆 trace 的 `status` 不再是 "running"
- 不破坏现有 fork 行为（已完成消息 / checkpoint / user 消息 fork 仍正常）

## Acceptance Criteria

- [ ] fork 一个 running run 后，新会话克隆 trace 的 `status == "completed"`
- [ ] fork 后新会话 `metadata` 不含 `current_run_id`（由 `clone_session_metadata` 统一剔除）
- [ ] `get_run_status(session_id, run_id)` 的 trace 回退查询带 `session_id` 过滤，不再跨会话命中
- [ ] fork running run 后，新会话 `getStatus` 返回非 `"pending"/"running"`，前端 `isTaskRunning === false`
- [ ] 新增/更新单测覆盖以上三点
- [ ] 已有 fork 测试全绿（`tests/infra/session/test_fork_title.py`、`tests/api/routes/test_session_fork_routes.py`、`tests/infra/test_checkpoint_fork_clone.py`）
- [ ] 老会话（legacy 无 seq 事件）fork suffixed assistant 气泡不再 404（`getForkMessageId` 传 runId）
- [ ] lint / typecheck 通过

## Definition of Done

- 单测覆盖四个修复点（3 后端 + 1 前端）
- lint / typecheck / 相关测试绿
- 不引入回归

## Technical Approach（方案 B，已选定）

三处后端核心改动 + 一处前端附带修复（老会话 404）：

1. **`src/infra/session/manager.py:507-513` `_build_cloned_trace_doc`**
   克隆后强制 `cloned["status"] = "completed"`（与 `_build_partial_user_trace_doc:534` 对齐）。fork 出的是历史快照，不可能是 running。

2. **`src/infra/session/manager.py:319-320` fork 写入 current_run_id**
   删除这两行（不无条件写 `current_run_id`）。`clone_session_metadata`（`src/kernel/schemas/session.py:69-79`）本就在 `:78` 主动 `pop("current_run_id")`，注释明写 "without transient branching state" —— 项目设计意图就是新会话不带 current_run_id。`:320` 又加回去违背设计，是 bug。

3. **`src/infra/task/status_queries.py:43-59` `get_run_status` 的 trace 回退查询**
   `:46` 的 `{"run_id": run_id}` 加 `session_id` 过滤 → `{"run_id": run_id, "session_id": session_id}`。消除跨会话同 run_id 污染（虽然正常 run_id 是 uuid 全局唯一，但 fork 克隆 trace 与源 trace 同 run_id，是真实存在的重复）。

4. **前端附带修复：fork suffixed bubble 404（实测老会话时发现）**
   - `frontend/src/components/chat/ChatMessage/index.tsx:643` 改传 `getForkMessageId(message)`
   - 新 helper `getForkMessageId`（`frontend/src/components/layout/AppContent/sessionState.ts`）：assistant 用 `message.runId`（真实 run_id），user 用 `message.id`
   - 测试：`sessionState.test.ts` 加 2 个 case
   - 触发条件：legacy 事件无 `seq`（`historyLoader.ts:239-248`）→ timestamp 排序同毫秒 tie 打散 run → `nextAssistantId`（:279-284）产生 `runId:N` suffixed 气泡 → fork 它传 `runId:N` → 后端 `_resolve_fork_target` 不认 → 404。新会话有 `seq` 排序稳定不触发，故只老会话暴露。

## Decision (ADR-lite)

**Context**: fork running run 后新会话 UI 永久卡在"正在生成"。根因是 fork 把 running trace 状态原样克隆 + 写入 current_run_id + status 查询跨会话命中，三者合力让前端误判新会话有任务在跑并连接一个永不会推 done 的 SSE 流。

**Decision**: 选方案 B（对症修复 + 语义修正），不做方案 C 的前端 SSE 重连改造。
- 改动 1（强制 completed）断掉根因链第 1 步：即使后两处不改，running fork 也不再触发
- 改动 2（移除 current_run_id 写入）纠正违背 `clone_session_metadata` 设计意图的错误，且消除 executor/arq_worker/wecom/recovery 误判"新会话忙碌"的连锁风险
- 改动 3（trace 查询加 session_id）是独立的跨会话语义修正
- 不选 C：前端 SSE onclose retry 上限是独立的防御纵深改造，范围大、需重新设计重连语义，作为后续独立任务

**Consequences**:
- ✅ 三处都是后端、局部、可独立测试，回归风险低
- ✅ 修复了 current_run_id 谎报状态可能导致的"用户新会话发消息被拒/排队"潜在 bug
- ⚠️ 前端 SSE 重连无上限的隐患仍在（本次不修，记入 Out of Scope）

## Out of Scope

- 前端 `sseConnection.ts` onclose retry 加上限 / 兜底清理 isStreaming（方案 C，独立加固）
- 前端对 running 消息禁用/隐藏 fork 按钮（产品决策）
- fork 的其它新功能

## Research References

- [`research/root-cause.md`](research/root-cause.md) — 完整根因链（文件:行）与三处修复点定位

## Technical Notes

- TaskStatus 枚举：`src/infra/task/status.py`（PENDING/RUNNING/COMPLETED/FAILED）
- trace status 写入点：`src/infra/session/trace_storage.py:253`(running) / `:404`(complete)
- current_run_id 消费者（判断会话是否忙碌）：`executor.py:527-531`、`arq_worker.py:42`、`wecom/handler.py:551`、`recovery.py:378`
- 相关测试：`tests/infra/session/test_fork_title.py`、`tests/api/routes/test_session_fork_routes.py`、`tests/infra/test_checkpoint_fork_clone.py`
