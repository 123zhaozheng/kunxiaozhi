# Root Cause: fork running run 后新会话 UI 卡在"正在生成"

## 现象
在 AI 流式生成中点击消息分叉，新会话 UI 永久显示"正在生成"，输入框停在暂停键。

## 触发条件
fork 一条 `status="running"` 的源 run（用户在 AI 生成中点分叉）。
`handleForkMessage`（`frontend/src/components/layout/AppContent/ChatView.tsx:238-252`）没有对 running 消息做拦截。

## 根因链（文件:行）

### 1. 后端 fork 克隆保留 running 状态 + 写入 current_run_id
- `src/infra/session/manager.py:319-320` —— 无条件 `new_metadata["current_run_id"] = target["run_id"]`
- `src/infra/session/manager.py:507-513` `_build_cloned_trace_doc` = `deepcopy(trace)`，只换 trace_id/session_id/user_id，**原样保留 status**。源 trace running → 克隆也 running。
- 对比 `src/infra/session/manager.py:515-536` `_build_partial_user_trace_doc`（target_type="user"）在 `:534` 硬编码 `status:"completed"` —— 所以 fork user 消息不卡，只有 fork 正在生成的 assistant 才卡。
- 关键佐证：`src/kernel/schemas/session.py:69-79` `clone_session_metadata` 注释 "without transient branching state"，`:78` 主动 `pop("current_run_id")`。项目设计意图就是新会话不带 current_run_id，`manager.py:320` 违背了这个意图。

### 2. 前端 loadHistory 读 current_run_id → 误判运行中 → 恢复 SSE
- `frontend/src/hooks/useAgent.ts:332-335` —— 取 `metadata.current_run_id`（fork 写的源 run_id）
- `frontend/src/hooks/useAgent.ts:369-374` —— 因此发 `getStatus(newSession, sourceRunId)`
- `frontend/src/hooks/useAgent.ts:391-395` —— `isTaskRunning = status === "pending" || "running"`
- `frontend/src/hooks/useAgent.ts:449-474` —— 走恢复分支：`prepareMessagesForRunningRun`（置 `isStreaming:true`）+ `connectToSSE(...)`

### 3. 后端 status 查询跨会话、且 running 命中
- `src/infra/task/status_queries.py:32-63` `get_run_status`：第一步查新会话 `metadata.task_status`（fork 没设，跳过）；`:46` 改查 trace `{"run_id": run_id}` **不带 session_id 过滤** → 命中克隆的 running trace → `:55` `status_map["running"] → TaskStatus.RUNNING` → 返回前端 `"running"`。

### 4. 为什么"一直"卡：SSE 无限重连，isStreaming 永不重置
- `frontend/src/hooks/useAgent/sseConnection.ts:212-233` `onclose`：没收到 `done` → `getSSECloseAction` 返回 "retry" → `:217` `throw` 触发 fetchEventSource 自动重连。只有 terminal 分支（:222-232）才置 `isStreaming:false`，永远到不了。
- 新会话根本没 run 在跑，stream 永远不会推 done → 无限重连 → `isStreaming:true` 锁死。
- `frontend/src/components/layout/AppContent/sessionState.ts:5-10` `isSessionRunning = isLoading || messages.some(m => m.isStreaming)` 一直 true → "正在生成" + 暂停键。

## 连锁风险（除 UI 卡外）
`current_run_id` 的消费者（`executor.py:527-531`、`arq_worker.py:42`、`wecom/handler.py:551`、`recovery.py:378`）用它判断"会话是否有 run 在跑"。fork 把它写进新会话会让这些判断误认为新会话忙碌，**可能导致用户在 fork 新会话发首条消息时被拒绝/排队**。改动 2 同时消除这个风险。

## 修复点（方案 B）

| # | 文件:行 | 改动 |
|---|---------|------|
| 1 | `src/infra/session/manager.py:507-513` | `_build_cloned_trace_doc` 克隆后 `cloned["status"] = "completed"` |
| 2 | `src/infra/session/manager.py:319-320` | 删除 fork 写入 `current_run_id` 的两行 |
| 3 | `src/infra/task/status_queries.py:46` | trace 回退查询加 `session_id` 过滤 |

## 测试切入点
- `tests/infra/test_checkpoint_fork_clone.py` / `tests/infra/session/test_fork_title.py`：fork 一个 running trace，断言克隆 doc status == "completed"、断言新会话 metadata 无 current_run_id
- `tests/api/routes/test_session_fork_routes.py`：fork running run 后 getStatus 不返回 running
- status_queries：同 run_id 不同 session 的两条 trace，`get_run_status(sessionA, runX)` 返回 sessionA 的状态而非 sessionB 的
