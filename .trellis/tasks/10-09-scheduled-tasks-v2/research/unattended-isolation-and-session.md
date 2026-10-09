# R2 调研：无人值守隔离 + 定时会话续聊 / checkpoint 清理

## 结论摘要
1. 全仓只有两处会等人：`ask_human` 与团队 `update_sop` 门禁，别无中间件或 interrupt 式审批。README 里的「敏感操作人工审批」就是这两处。已验证：grep `create_approval|wait_for_response|interrupt_on|HumanInTheLoop`，`middleware/` 下无人工等待（`tool_interception.py` 只处理文件大小）。
2. 当前不会串到别的会话界面，但没有任何防护。`fetchApprovals` 实际是死代码，审批只来自「当前已加载会话」的 SSE 和历史回放。用户打开 `/chat/sch_*` 时会看到并能响应审批。
3. 「无人值守」标记用按次运行的执行器参数 `unattended`，经 TraceContext 的新 ContextVar 给工具读。禁止放进 `agent_options` 或 session metadata：前端续聊会把 `agent_options` 带回去。
4. 隔离方案选「工具短路立即返回」为主，提示词说明为辅。SOP 门禁在无人值守时自动拒绝，不自动批准。
5. 定时会话的续聊、checkpoint 清理、统计基本可用。需要补：续聊模型不一致、hidden 取消时机、重试重复计数、`CHECKPOINT_CLEANUP_ENABLED` 默认关闭。

## 现状（证据）
**A 阻塞点**
- `ask_human`：默认 timeout=300，LLM 可传 10–3600（`human_tool/tool.py:62,114`）。超时返回 `{"status":"timeout","values":默认值}`，不报错，LLM 带着默认值继续跑（`tool.py:177-185`）。审批文档 TTL 为 3600 秒（`storage/mongodb.py:150`）。
- 上下文来源：`session_id` 来自构造入参（`fast_agent/context.py:162`），run_id 和 user_id 来自 `TraceContext.get_request_context()`（`tool.py:151`）。该上下文是 ContextVar，由 `executor.py:121` 和 `agent/core/base.py:359` 设置，且只在值非 None 时才 set（`logging/context.py:162-170`）。
- 用户无法禁用：`tool_filter.py:11-18`。但团队模式已有「移除」先例：`team_agent/context.py:11-14`、`team_agent/nodes.py:414`。
- SOP：`sop/tool.py:26` 等待 300 秒（`:142`）。超时时 plan 置为 `timed_out`，返回 `timed_out:true`。`direct_answer` 或无 steps 不阻塞（`:211`）。

**B 前端**
- `ChatAppContent.tsx:81` 传 `sessionId:null`，而 `useApprovals.ts:85-87` 在 `!sessionId` 时直接 return。`refresh` 从未被解构使用。因此 `GET /human/pending` 在 UI 里没被调用。
- 审批只有两个入口：SSE（`eventHandlers.ts:540`，先 `GET /human/{id}` 确认 pending）和历史回放（`historyLoader.ts:250`）。`loadHistory` 与 `stopGeneration` 会清空列表（`useAgent.ts:340,893`）。
- `ApprovalPanel` 不按 session 过滤（仅 `:238` 按 id 去重），列表本身是全局单例。结论：潜在隐患，目前无串线。
- 后端 `register_approval_callback` 无人注册（grep 确认），没有全局审批推送。
- 清理 worker 会跳过有 pending 审批的会话（`checkpoint/cleanup_worker.py:188`），最多挡 1 小时。

**C 传递链**：runner `_execute` → `manager.submit` / `submit_arq`（`manager.py` 两个签名都没有该参数）→ `run_task` → `executor(...)` → `_execute_agent_stream`（`chat.py:278`，显式形参）→ `agent.stream(**kwargs)` → `config["configurable"]`（`base.py:377`）。`arq_worker.py:94-109` 与 `recovery.py:177-196` 都只带固定字段。
- 续聊带回 agent_options 的链路已验证：`useAgent.ts:368-371` → `ChatAppContent.tsx:710-720` → `useAgentOptions.ts` 的 `restoreAgentOptions`（任意 key 原样放入）→ `buildAgentOptionValues` 展开 → `sessionConfigRef.agentOptions`（`:410`）→ `chat.py:202` 写回 metadata。
- `_run_info` 是进程内字典，arq 下是 worker 自己构造的，不可靠。

**D 续聊**
- `GET /sessions/{id}` 与 `/stream` 只做 owner 校验（`session.py:100,207`），不看 hidden；`sch_*` 可直接打开。
- 前端从 metadata 还原 agent_id、persona、agent_options（`useAgent.ts:368-390`、`handleConfigRestored`）。
- 续聊实际用：agent 取 metadata.agent_id；专家 `resolve_persona_request` 会重新 `use_preset` 取最新快照（`chat.py:252-275`），专家被删或无权限时续聊 404。模型：runner 只在 `task.model_id` 有值时才写 `model_id`（`runner.py:241`），否则续聊用 UI 当前默认模型，与定时运行可能不同。
- thread_id 等于 session_id（`base.py:377,575`，`fast_agent/graph.py:173`），上下文连续。
- 重试：每次尝试新建 `_rN` 会话（`runner.py:67,174`），run 记录只保留最后一次的 session_id，前几次失败会话成孤儿。
- hidden：仅 `list_sessions` 过滤（`storage.py:384`，覆盖侧边栏、搜索、项目、收藏）。其余未过滤：按 id 读取、`analytics/storage.py:1089`（符合「按普通会话计入」）、WS `task:complete`（`executor.py:~397`）会给 sch_ 会话推未读和系统通知，与 `run_finished` 重复。续聊后仍 hidden：`build_conversation_config` 不含该键，`update_metadata_only` 是按键合并（`storage.py:312`）。
- 分享是否过滤 hidden：未查（推断：不过滤）。

**E 清理**：`list_inactive_session_ids`（`storage.py:721`）无 hidden 过滤，`sch_*` 会被清理。UI 的「已清理」（输入置灰、禁分叉）只看 metadata（`useAgent.ts:355`、`ChatView.tsx:374,613`），同样适用。`CHECKPOINT_CLEANUP_ENABLED=False`，保留 30 天，间隔 24 小时，每批 200（`kernel/config/base.py:233-236`）。注意 `update_metadata_only` 会刷新 `updated_at`，也就是重置保留计时。

**F 统计**：local 和 arq 的 `submit`、`submit_arq` 都在 API 侧 `_persist_initial_user_message`（`manager.py:143-149`）写一次 user:message 和 `record_message_activity`，并以 `user_message_written=True` 下发（`arq_worker.py:94,109`），`run_task` 的 `already_written` 跳过（`executor.py:133-145`）→ 各恰好一次（已验证，读代码未运行）。专家归属：trace 带 `persona_preset_id`（PresenterConfig），会话 metadata 也写（`runner.py:274`），by-persona 以会话 metadata 为准、trace 兜底（`analytics/storage.py:691`）。

## 缺口
1. 标记无通道，`ask_human` 与 SOP 都会挂起。
2. 重试会让 messages 与 sessions 最多重复计 `1+max_retries`（≤4）次。
3. 续聊模型可能与定时运行不一致。
4. 续聊后会话永远 hidden。
5. 无人值守提示词缺失。
6. 恢复路径（`recovery.py`）不会继承标记。

## 推荐方案
- 标记：新增执行器参数 `unattended: bool=False`。
  - `runner._execute` 传 `unattended=True`，改 `manager.submit/submit_arq` 签名与 arq payload、`arq_worker.run_task` 调用、`executor.run_task`、`_execute_agent_stream`、`concurrency.py:615`。
  - `logging/context.py` 新增 `_unattended` ContextVar，并在 `executor.py:121` 与 `base.py:359` 每次显式 set（True 或 False，不走 `is not None` 分支）。
  - `RequestContext.unattended` 供工具读取，与 `human_tool/tool.py:151` 一致，闭包型 SOP 工具也在调用时读取。
- 恢复路径：runner 写 metadata `unattended_run_id=<agent_run_id>`（按 run 绑定，续聊 run_id 不同，不会误继承）。`recovery.py` 用 `== source_run_id` 判定并改写成 new_run_id。
- `ask_human` 短路：`_arun` 开头若 unattended，不 `create_approval`，直接返回 `{"status":"unattended","message":"当前为定时无人值守运行，无法提问。请按保守默认值继续，涉及不可逆或外部副作用的操作一律不执行，并在最终答复中列出待用户确认事项。","values":默认值}`。
- SOP：`_request_confirmation` 开头短路，plan 置 `rejected` 并返回 `{"approved":false,"unattended":true,"feedback":"无人值守不可执行需确认的计划，请缩减为可直接完成的步骤或直接回答"}`。
- 提示词：在 `fast/search nodes.py:117/153` 的 persona 段旁加一段「无人值守」固定文案（`team/nodes.py:193` 同理）。系统提示词每次重建，不会污染 checkpoint。备选是给 runner 的 message 加前缀，但会进入 checkpoint。
- 续聊：`chat.py:_update_session_config` 在 `source==scheduled_task` 且 hidden 为 True 时写 `hidden_from_conversation_list=False`。runner 把解析后的 model_id 一并写入 metadata.agent_options。
- 重试：`attempt>0` 时不再重复计 `record_message_activity`，或在统计里按 `metadata.scheduled_task_run_id` 去重。
- 运维：上线 v2 时开启 `CHECKPOINT_CLEANUP_ENABLED`，并按定时任务的新增量调大 batch 或缩短 interval。

## 备选与权衡
| 方案 | 优点 | 缺点 |
|---|---|---|
| 工具短路（推荐） | 不改工具列表、不产生审批、没有 UI 暴露 | 靠提示词引导 LLM 行为 |
| 从工具列表移除 | LLM 不会调用 | 需把标记穿进 context 构造；`subagent_prompts.py:37` 仍要求调用 `ask_human`；与续聊提示缓存不一致 |
| 挂起等待（现状） | 无需改动 | 占用并发槽，最长 5–60 分钟；超时按默认值继续（可能默认执行危险动作）；可能被 `/chat/sch_*` 看到 |

可叠加：短路作为保底，提示词作为引导，移除只在以后有需要时再做。

## 风险
- 标记漏传（local、arq、recovery、`concurrency.py:615` 四条路径）会静默退回挂起。
- ContextVar 必须每次显式 set，否则 local 下 `create_task` 复制的上下文可能带出旧值。
- 对续聊 unhide 后会话出现在侧边栏，用户可能一时疑惑。
- 清理批量不够会让 checkpoint 持续增长。

## 测试建议
- 单测：tool 在 unattended 下不调用 `create_approval`。
- 参数化测试：local 与 arq 两条路径，payload 往返后标记仍在。
- 续聊 run 的 unattended=False，且 `agent_options` 里不含该键。
- SOP 自动 rejected 且不阻塞。
- `unhide` 仅对 scheduled 会话生效。
- 重试后消息数和会话数的计数断言。
- `list_inactive_session_ids` 包含 `sch_*`。

## 需要产品决策
1. 续聊首次是否取消 hidden？推荐：是，用户主动互动是明确信号。
2. 无人值守团队 SOP 的处理？推荐：自动拒绝；v2 暂不允许团队智能体建定时任务，管理员开关（默认关）可放开。
3. 重试是否计入统计？推荐：只计成功或最后一次，避免膨胀。
4. 定时会话是否使用独立保留期？推荐：先沿用全局保留期，只强制要求清理已开启。
5. 续聊模型不同是否接受？推荐：不接受，runner 持久化解析后的 model_id。

## 跨课题发现
- runner 直接 `manager.submit`，不经 `limiter.acquire`，但 `_on_task_done` 仍会 release 并发槽（`manager.py:~420`），是否错配未查。
- `recovery` 会生成新的 run_id，runner `_wait` 仍轮询旧 id，恢复后 runner 可能误判失败。
- 运行记录与 `task:complete` 通知重复推送（`executor.py:~397`、`useWebSocketNotifications.tsx:~190`）。
- 定时任务的 `agent_id` 若解析为 team 且未传 `team_id`，可能失败。
