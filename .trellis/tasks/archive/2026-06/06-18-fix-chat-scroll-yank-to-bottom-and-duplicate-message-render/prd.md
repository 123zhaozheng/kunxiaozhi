# 修复聊天会话滚动被强制拉回底部及消息重复渲染

## 背景 / 问题描述

用户反馈（会话多轮聊天场景），共三个相关现象：

1. **滚动不跟手 + 被无形力量拉扯**：点进一个会话时消息排列正常，向上滚动几下后会被一个"无形的力量"拉回某处，来回拉锯。用户实测：去掉跳顶恢复后无效，说明此现象的感知部分可能由现象 2/3 的消息错位、Virtuoso 虚拟列表回收复用放大。
2. **向上滚后整屏变成同一条 AI 回复**：往上滚到最上面再往下一滚，整个可视区全部渲染成同一条 AI 回复，原本的用户提问消失。用户描述"用户消息一出来就闪一下"（Virtuoso 惰性加载/卸载）。
3. **暂停后继续，消息顺序排乱**：用户问 Q1 → AI 答 A1 → 用户暂停 → 用户再问 Q2 → AI 继续答 A2。预期顺序 `用户 Q1 → AI A1 → 用户 Q2 → AI A2`，实际变成 `用户 Q1 → 用户 Q2 → AI A1 → AI A2`（顺序错乱，不是内容合并）。
4. （附带）点击任意会话时 `GET /api/feedback/?skip=0&limit=100&session_id=xxx` 返回 `缺少权限: feedback:read`。

## 已确认的关键证据

通过临时诊断日志（已移除）在用户可复现的会话上捕获：

- `[DIAG] ChatView duplicate ids at render: {count: 16, dupes: Array(1)}`
- React 报错：`Encountered two children with the same key, run_20260618064521_a46159bd`（重复多次）
- `[DIAG] runs missing user:message event: []`（user 消息事件未丢失，排除"截断丢 user 事件"猜想）

**结论：现象 2 的直接原因是 `reconstructMessagesFromEvents` 产出了两条 `id` 相同的 assistant 消息，Virtuoso `computeItemKey={(_, m) => m.id}` 撞 key，虚拟列表在滚动回收时把同一 key 的内容铺满可视区。**

## 根因分析

### 共同根因：事件缺乏稳定顺序 + 重建按 timestamp 排序 + 同 run 单 id 约定

- 后端 trace 文档 schema 注释声明每个事件有全局递增 `seq`（`src/infra/session/trace_storage.py:14-17, 26-29`），但 **`append_event` 实际从不写 `seq`**（`trace_storage.py:268-281` 只写 `event_type/data/timestamp`），聚合管道也不 project `seq`。全局序号计数器（`session_events_counter`）在代码中不存在。→ **后端返回的事件没有任何稳定顺序字段**。
- 聚合管道 `get_session_events`（`trace_storage.py:731-761`）：`$match → $sort {started_at:1} → $unwind → $limit`。`$sort` 按 **trace 文档的 `started_at`** 排，不是事件时间戳。`$unwind` 在单 trace 内保持数组写入顺序（真实先后），但 **跨 trace 完全靠 trace `started_at`**，多个 run 的 trace 交错时返回顺序即乱。
- 前端 `reconstructMessagesFromEvents` 第一步 `[...events].sort((a,b) => parseEventTimestamp(a).getTime() - parseEventTimestamp(b).getTime())`（`historyLoader.ts:235-239`）按 **timestamp** 重排。timestamp 是 `utc_now()`，毫秒精度，快速连续/并发事件可能同毫秒或乱序；JS `sort` 对并列键不稳定。→ **重排后顺序不可靠**。
- assistant 消息 `id = event.run_id`（`historyLoader.ts:158`），user 消息 `id = ${run_id}:user`（`presenter_events.py:427`）。同 run 事件被拆到不连续位置时，重建会生成第二条 `id = run_id` 的 assistant 气泡 → 撞 key。

### 现象 2（整屏同一条 AI 回复）的具体链路
同 run 的事件被时间戳重排拆成两段，中间夹了别的 run 的 user:message → 重建生成两条 id 相同的 assistant 消息 → Virtuoso 撞 key → 滚动回收时整屏渲染同一条。

### 现象 3（暂停后顺序排乱 用户用户 AI AI）的具体链路
暂停后继续，Q2 与 A1 末尾事件时间戳接近/并列，前端按 timestamp 重排把 Q2 排到 A1 之前 → 视觉上 Q1 Q2 A1 A2。本质同上：缺乏稳定顺序字段。

### 现象 1（拉扯）的定位
- `useMessageScroll.hook.ts` 的"跳顶恢复"逻辑（`shouldIgnoreUnexpectedTopJumpDuringBottomLock` → `forceVirtuosoToBottom`）会在 `history-finalize` 后 2.4s 观察窗内把 `scrollTop<=1` 当异常跳顶强制拉回底部。用户实测去掉后无效，故此逻辑非主因，但作为可疑项仍一并清理。
- 真正的"拉扯感"更可能来自现象 2 的撞 key 导致 Virtuoso 在滚动时反复错位重渲染。

### feedback 接口 403
- `frontend/src/hooks/useAgent.ts:375-377` 在 `loadHistory` 并行请求 `feedbackApi.list(0,100,undefined,undefined,sessionId)` 拉历史点赞/点踩贴到 assistant 消息。
- 后端 `GET /api/feedback/` 要求 `feedback:read`（`src/api/routes/feedback.py:88`）。前端用 `canReadFeedback = hasAnyPermission([FEEDBACK_READ, FEEDBACK_WRITE])` 守卫，但 token 里 permissions 未同步 `feedback:read` → 403。前端已 `.catch()` 容错（`useAgent.ts:378-381`），不影响聊天，仅控制台噪音。

## 目标 / 范围

### 阶段一：去掉跳顶恢复（已实现）
彻底移除 `shouldIgnoreUnexpectedTopJumpDuringBottomLock` 触发的 `forceVirtuosoToBottom` 拉回分支及相关孤儿代码（`recoverUnexpectedTopJumpUntilRef`、`getUnexpectedTopJumpRecoveryUntilAfterUserIntent`、`isUnexpectedTopJump`）。保留 `loadHistory` 后单次滚到底与流式贴底跟随。相关单测相应删除/更新。
**状态：已改，但用户实测对主现象无效——保留改动作为清理，根因在阶段二。**

### 阶段二：修复消息重复 id 与顺序错乱（核心，已实现）

两条互补的修复，缺一不可：

**(A) 后端：给事件写入稳定全局顺序 `seq`，并按 `seq` 排序（已实现）**
- `trace_storage.py` 新增 `counter_collection`（`session_events_counter` 集合）与 `next_event_seq(session_id)`：`find_one_and_update` 原子 `$inc`，返回该 session 的下一个全局序号。
- `append_event` 写入时调用 `next_event_seq` 为事件存 `seq`（`append_event` 新增 `session_id` 参数）。
- 实时路径走 `dual_writer` 批量写入：`_do_flush` 按 session 预分配一批连续 seq（每 session 一次 `$inc: {seq: count}`，取回 base 后展开），`_build_mongo_bulk_operations` 按 batch 顺序为每个事件填 `seq`。
- 聚合管道 `get_session_events` 在 `$unwind` 后新增 `$set events.seq_sort = $ifNull(events.seq, 0)` + `$sort {seq_sort, started_at, events.timestamp}`，`$limit` 移到排序之后；最终 `$project` 增加 `seq` 字段。
- 兼容旧存量：无 seq 的事件 `seq_sort=0`，排在所有 seq≥1 的新事件之前（旧事件时间上更早，正确），内部按 `started_at`/`timestamp` 排序。

**(B) 前端：按 `seq` 排序 + 同 run 单 assistant 气泡 + 重复时后缀 id（已实现）**
- `HistoryEvent` 类型新增 `seq?: number`。
- `reconstructMessagesFromEvents` 排序改为优先按 `seq`（回退 timestamp）。
- 维护 `assistantMessageIndexByRunId`：同 run 事件被别的 run 的 user:message 拆开后，后续同 run 事件**回挂**到已 parked 的气泡（splice 出来继续累积），而不是新建第二条 → 避免重复 id。
- `assistantBubbleCountByRunId` + `nextAssistantId`：run 的第一条 assistant 气泡 id = `run_id`（与实时发送路径 `useAgent.ts:748` 的 id 约定一致，保证 SSE `messageId` 匹配）；只有当同 run 真的产生第二条气泡时才用 `${run_id}:2` 后缀，绝不与第一条撞 key。
- **保留既有行为**：`user:cancel` 后同 run 的迟到事件（token:usage、迟到 thinking）回挂到已 cancel 的气泡（测试 7/8 验证）；不引入"cancel 边界断开"，否则会破坏迟到事件归并。

**关于现象 3（暂停后顺序排乱 用户用户 AI AI）的最终定位**：这不是 cancel-同 run 续答（同 run 第二个 user:message 会被 `seenUserMessageRunIds` 跳过，且迟到 AI 事件回挂到已 cancel 气泡）。它更可能是暂停后开启**新 run_id**，Q2/A2 与 A1 末尾事件 timestamp 并列导致前端旧排序把 Q2 排到 A1 前。阶段二 (A) 的 seq 排序即修复此现象——seq 单调递增、无并列，Q2 的 seq 必然大于 A1，顺序天然正确。若实测后现象 3 仍在，则需进一步确认暂停后是否真用了新 run_id（再补 `seenUserMessageRunIds` 按 message_id 去重的改动）。

**安全边界（联网研究 + 代码审计确认）**：
- `react-virtuoso` `computeItemKey` 把返回值直接当 React key 转发；重复 key 在虚拟窗口滚动时触发 React reconciliation 错位（react#31343），即"整屏同一条"症状。修复只需保证 `message.id` 唯一稳定，不动 Virtuoso 其它配置。
- `followOutput="smooth"` 仅在已在底部时跟随，用户上滚自动暂停——合理行为，不动。
- `initialTopMostItemIndex` 仅 mount 生效；`<Virtuoso key={messageListSessionKey}>` 切会话 remount——设计如此，不动。
- `prepareMessagesForRunningRun` 按 `runId` 查（不按 id）、`createMessageAnchorId` 读取 `message.id` 不假设格式 → 改 id 格式安全跟随。
- 不动实时 SSE 路径的 id 约定（`useAgent.ts:748` 仍把乐观 assistant id 替换成 `newRunId`），重复 id 只在历史重建路径修。

验收：
- 多轮会话点进去，向上滚到顶再往下滚，不再整屏变成同一条 AI 回复，用户消息不消失。
- 暂停后继续：顺序为 `用户 Q1 → AI A1(已取消) → 用户 Q2 → AI A2`，不出现 `用户用户 AI AI`。
- `historyLoader` 单测：跨 run 交错不产生重复 id（已新增 test 9）；cancel 后迟到事件仍回挂到已 cancel 气泡（既有 test 7/8 通过）；同 run 第一条 assistant id = run_id（既有 test 通过）。
- 后端 `get_session_events` 管道含 seq 排序与 `seq` 投影（更新 test 通过）；`ruff`/`mypy`/`pytest tests/infra/session` 全绿。

### 阶段三（可选）：feedback 403 噪音
前后端权限串对齐 `feedback:read`，或前端无权限时不发该请求。

## 不做

- 不动聊天输入、工具面板、分享页等其他模块。
- 不改 SSE 实时流路径（`eventHandlers.ts`）的按 id 更新逻辑——实时路径不易触发重复 id，问题集中在历史加载重建。
- 阶段二不改 Virtuoso 本身配置（`computeItemKey` 保持 `message.id`），靠保证 id 唯一性解决。
