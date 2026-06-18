# Research: E1 — trace 写入链路落 persona_preset_id 的精确改动点

- **Query**: PR2 PRD E1 要求在 trace 写入链路落 `persona_preset_id` 到 `traces.metadata`。精确化 submit → executor → PresenterConfig → presenter_storage._build_trace_metadata 的透传链路、WeCom 入口、arq/排队/recovery 三条 dispatch 路径，给出 file:line 级改动清单。
- **Scope**: internal
- **Date**: 2026-06-18

## 结论（给 implement 的一句话）

`persona_preset_id` 是一个纯透传字段，从 `chat.py` 的两处 submit 调用出发，要穿透 **4 条 dispatch 路径**（submit 直提交 / submit_arq 异步队列 / concurrency 排队 / recovery 恢复），最终都汇入 `executor.run_task`，再传入 `PresenterConfig`，在 `presenter_storage._build_trace_metadata` 里写入 `metadata["persona_preset_id"]`。`trace_storage.create_trace` **不用改签名**——metadata 是整个 dict 透传落库（`trace_storage.py:228` `"metadata": metadata or {}`），只需让 `_build_trace_metadata` 多返回一个 key 即可。WeCom 入口（`handler.py:391`）也走 `task_manager.submit`，必须同步传 `persona_preset_id`（它已有 `preset_id` 变量，直接复用）。**关键陷阱：`feedback.storage.create` 显式列举字段，不会自动落 `reason`（见 E2 文档）；但 trace metadata 是 dict spread，无需此担忧。**

---

## Findings

### 1. `task_manager.submit` 完整签名与透传链路

**文件**：`src/infra/task/manager.py`

- `submit` 签名在 **manager.py:230-253**。参数顺序：`session_id, agent_id, message, user_id, executor, disabled_tools, agent_options, attachments, run_id, project_id, disabled_skills, enabled_skills, persona_system_prompt, disabled_mcp_tools, session_name, display_message, team_id, trace_id, active_goal, user_message_written, write_user_message_immediately`。
- submit 如何把参数传给 executor：在 **manager.py:315-336** 用 `asyncio.create_task(task_executor.run_task(...))`，参数以**位置参数 + 关键字参数混合**传入：
  - 位置参数（manager.py:317-325）：`session_id, run_id, agent_id, message, user_id, executor, disabled_tools, agent_options, attachments`（共 9 个，对应 `run_task` 的前 9 个位置参数）。
  - 关键字参数（manager.py:326-334）：`disabled_skills=, enabled_skills=, persona_system_prompt=, disabled_mcp_tools=, display_message=, team_id=, existing_trace_id=trace_id or None, active_goal=, user_message_written=`。
  - **`persona_system_prompt` 已透传，但 `persona_preset_id` 完全不在链路里**——需要新增。
- executor 实际被调用时参数从哪来：`run_task` 是 `TaskExecutor` 方法（executor.py:61），参数即上面 create_task 传入的。`PresenterConfig` 在 run_task 内部构造（executor.py:95-105），**目前只用了 `session_id/agent_id/agent_name/user_id/run_id/trace_id/enable_storage`**，没接收 `persona_preset_id`。
- submit 把 executor 存哪：executor 不存——`executor` 是**可调用对象**（`_execute_agent_stream`），作为参数直接传进 `run_task`，run_task 在 executor.py:160 `async for event in executor(...)` 调用它。`_run_info[run_id]`（manager.py:306-312）只存 `session_id/trace_id/agent_id/user_id/user_message_written`。

### 2. `executor.py` run_task / PresenterConfig 构造

**文件**：`src/infra/task/executor.py`

- `run_task` 签名：**executor.py:61-81**。参数：`session_id, run_id, agent_id, message, user_id, executor, disabled_tools, agent_options, attachments, existing_trace_id, user_message_written, disabled_skills, enabled_skills, persona_system_prompt, disabled_mcp_tools, display_message, team_id, active_goal`。
- `PresenterConfig(...)` 构造点：**executor.py:95-105**。当前字段：`session_id, agent_id, agent_name=resolve_agent_name(agent_id), user_id, run_id, trace_id=existing_trace_id, enable_storage=True`。
- **`persona_preset_id` 要加到 `run_task` 签名 + `PresenterConfig` 构造两处**。建议加在 `active_goal` 之后（run_task 末尾），PresenterConfig 里加 `persona_preset_id=persona_preset_id`。
- 注意：run_task 调用 executor（`_execute_agent_stream`）时（executor.py:160-175）传的参数里**不含 persona_preset_id**，因为 `_execute_agent_stream` 是 agent stream 执行器，persona 已通过 `persona_system_prompt` 注入 agent。**persona_preset_id 只需到 PresenterConfig 用于 trace metadata，不需要传给 executor/agent**。

### 3. `PresenterConfig` dataclass 定义

**文件**：`src/infra/writer/presenter_config.py`

- 定义在 **presenter_config.py:56-68**，`@dataclass`。完整字段（按顺序）：
  - `session_id: Optional[str] = None`（:60）
  - `agent_id: Optional[str] = None`（:61）
  - `agent_name: str = "Agent"`（:62）
  - `user_id: Optional[str] = None`（:63）
  - `run_id: Optional[str] = None`（:64）
  - `trace_id: Optional[str] = None`（:65）
  - `chunk_delay: float = 0.0`（:66）
  - `max_result_length: int = 2000`（:67）
  - `enable_storage: bool = True`（:68）
- **import 需求**：`Optional` 已从 `typing` 导入（presenter_config.py:5 `from typing import Any, Dict, List, Optional`）。**无需新增 import**。
- **精确位置**：在 `enable_storage` 后追加 `persona_preset_id: Optional[str] = None`（:68 之后）。因为是 dataclass 且有默认值，加在末尾安全（不破坏位置参数构造——所有现有构造都用关键字参数，见下）。
- **所有 PresenterConfig 构造点都要检查**（grep `PresenterConfig(`）：除了 executor.py:95，还有 manager.py:133（`_persist_initial_user_message`）、chat.py:424（`_pre_presenter`，enable_storage=False）、chat.py:494（排队路径 QUEUED 时的 presenter）、recovery.py:170（recovery_trace，enable_storage=False）、recovery.py:265（trace_presenter）。**这些构造点只有 executor.py:95 和 recovery.py:265 会真正创建 trace（enable_storage=True 且调 _ensure_trace）**。chat.py:494 也调 `_ensure_trace`（排队时先写 user:message）。其他 enable_storage=False 的不创建 trace，无需传 persona_preset_id（传了也不落库）。但为一致性，建议 executor.py:95 必传，其余可传可不传（不传则为 None，metadata 不写）。

### 4. `presenter_storage._build_trace_metadata`

**文件**：`src/infra/writer/presenter_storage.py`

- `_build_trace_metadata` 在 **presenter_storage.py:78-84**：
  ```python
  async def _build_trace_metadata(self) -> Dict[str, Any]:
      metadata: Dict[str, Any] = {
          "agent_name": self.config.agent_name,
      }
      metadata.update(await self._build_identity_metadata())
      return metadata
  ```
- **精确写法**：在 `metadata.update(...)` 之后、`return` 之前加：
  ```python
  if self.config.persona_preset_id:
      metadata["persona_preset_id"] = self.config.persona_preset_id
  ```
- `self.config.persona_preset_id` 访问安全：PresenterConfig 加了字段后有默认值 None，所有已构造的 PresenterConfig 实例都能访问（dataclass 默认值）。
- `_build_identity_metadata`（presenter_storage.py:57-76）只写 `user_id`/`username`，不含 preset_id，不要动它。
- `build_langsmith_metadata`（presenter_storage.py:86-91）是 LangSmith 用的，**不要加 persona_preset_id**（避免泄露到外部 trace 系统，且 PR2 范围是 MongoDB traces）。

### 5. WeCom 入口链路

**文件**：`src/infra/agent/wecom/handler.py`

- WeCom 消息处理走 **`task_manager.submit`**（不是 submit_arq）。调用点在 **handler.py:391-404**：
  ```python
  run_id, _ = await task_manager.submit(
      session_id=session_id,
      agent_id=agent_to_use,
      message=content,
      user_id=session_owner_id,
      executor=executor,
      project_id=project_id,
      agent_options=None,
      session_name=session_title,
      display_message=content,
      write_user_message_immediately=True,
      enabled_skills=enabled_skills,
      persona_system_prompt=persona_system_prompt,
  )
  ```
- **WeCom 已有 `preset_id` 变量**（handler.py:199 `preset_id = manager.get_preset_id_for_aibotid(aibotid)`，handler.py:200 校验非空）。**直接在 submit 调用里加 `persona_preset_id=preset_id,`**（handler.py:404 之前）。
- **WeCom 不走 submit_arq**（grep 确认 handler.py 只用 submit）。但若部署用 `TASK_BACKEND=arq`，WeCom 的 submit 仍是本地直提交（handler 没分支判断 TASK_BACKEND），所以只改 submit 路径即可。
- **WeCom executor 闭包**（handler.py:354-386 `async def executor(...)`）和 `execute_wecom_agent`（handler.py:103-158）**不需要加 persona_preset_id 参数**——理由同上：persona_preset_id 只到 PresenterConfig，不到 agent stream。

### 6. chat.py 两个 submit 调用点

**文件**：`src/api/routes/chat.py`

- `request.persona_preset_id` 字段在 `AgentRequest`（**src/kernel/schemas/agent.py:47** `persona_preset_id: Optional[str] = Field(None, ...)`），已存在，确认。
- **submit_arq 调用**（chat.py:539-559）：在 `active_goal=active_goal_data,` 后（:557）加 `persona_preset_id=request.persona_preset_id,`。
- **submit 调用**（chat.py:562-582）：在 `active_goal=active_goal_data,` 后（:580）加 `persona_preset_id=request.persona_preset_id,`。
- **注意**：chat.py 里还有两处 `task_context` dict 构造（chat.py:435-451 排队路径的 task_context，和 recovery.py:184-202 的 task_context），这些是**排队 dispatch 路径**用的，见第 7 点。chat.py:435 的 task_context 也要加 `"persona_preset_id": request.persona_preset_id,`（:450 `active_goal` 后）。

### 7. 排队 dispatch 路径（concurrency）

**文件**：`src/infra/task/concurrency.py` + `src/api/routes/chat.py`

- chat.py 排队路径把 task_context 存进 Redis 队列（chat.py:435-451 `task_context = {...}`，经 `limiter.acquire(... task_context=task_context)`）。
- `_dispatch_queued_task`（concurrency.py:528-644）从 `queue_data["task_context"]`（concurrency.py:546 `task_ctx = queue_data.get("task_context")`）读取字段，**显式逐个提取**（concurrency.py:558-568），然后传给 `executor.run_task`（concurrency.py:602-623）。
- **改动点**：
  1. chat.py:435-451 的 `task_context` dict 加 `"persona_preset_id": request.persona_preset_id,`。
  2. concurrency.py:558-568 新增 `persona_preset_id = task_ctx.get("persona_preset_id")`。
  3. concurrency.py:602-623 的 `run_task(...)` 调用加 `persona_preset_id=persona_preset_id,`。
  4. concurrency.py:569-588 的 legacy fallback 分支（`pending = task_manager.pop_pending_task(run_id)`）也对应加 `persona_preset_id = pending.get("persona_preset_id")` 并传给 run_task。**但这条分支是"迁移前的内存格式"，现在 chat.py 已用 Redis task_context，实际走 :546 分支**。为稳妥，fallback 分支也加（否则旧格式排队任务恢复时 persona_preset_id 丢失，但不影响功能，只是该 trace 不计 preset 统计）。

### 8. arq 异步队列路径

**文件**：`src/infra/task/manager.py`（submit_arq）+ `src/infra/task/arq_worker.py`（run_agent_task）

- `submit_arq` 签名（manager.py:345-370）：与 submit 类似，**无 persona_preset_id 参数**。需在 `active_goal` 后（manager.py:368）加 `persona_preset_id: Optional[str] = None,`。
- submit_arq 把参数存进 `payload_store.save(run_id, {...})`（manager.py:403-425），dict 显式列举字段。**必须在 dict 里加 `"persona_preset_id": persona_preset_id,`**（manager.py:423 `active_goal` 后）。否则 arq worker 取不到。
- `run_agent_task`（arq_worker.py:63-143）从 payload 读字段，调 `task_executor.run_task(...)`（arq_worker.py:98-117）。**arq_worker.py:116 `active_goal=payload.get("active_goal"),` 后加 `persona_preset_id=payload.get("persona_preset_id"),`**。

### 9. recovery 恢复路径

**文件**：`src/infra/task/recovery.py` + `src/infra/task/manager.py`

- `submit_recovery_run`（recovery.py:148-319）构造 `task_context`（recovery.py:184-202）和调用 `_submit_recovery_task`（recovery.py:223-246）。
- `_submit_recovery_task`（manager.py:187-197）根据 `TASK_BACKEND` 分发到 `submit_arq`（manager.py:191）或 `submit`（manager.py:197），用 `**kwargs` 透传。
- **改动点**：
  1. recovery.py:184-202 的 `task_context` dict 加 `"persona_preset_id": session_metadata.get("persona_preset_id"),`（session metadata 已存 persona_preset_id，见 chat.py:243）。
  2. recovery.py:223-246 的 `_submit_recovery_task(...)` 调用加 `persona_preset_id=session_metadata.get("persona_preset_id"),`（:245 `team_id=` 后）。
  3. recovery.py:264-274 的 `trace_presenter = Presenter(PresenterConfig(...))`（恢复时写 user:message 的 presenter）可加 `persona_preset_id=session_metadata.get("persona_preset_id"),`（:272 `trace_id=` 后），使其 trace 也带 preset_id。**注意 recovery.py:170-179 的 `recovery_trace`（enable_storage=False）不创建 trace，可不加。**
- recovery.py:285-310 的 `self._storage.update(... metadata={...})` 已包含 `persona_preset_id`（recovery.py:296），session metadata 侧已正确。

### 10. trace_storage.create_trace 是否需要改

**文件**：`src/infra/session/trace_storage.py`

- `create_trace`（trace_storage.py:191-246）签名：`trace_id, session_id, agent_id, run_id, user_id, metadata`。**metadata 是整个 dict 透传**——trace_storage.py:228 `"metadata": metadata or {}`。
- `dual_writer.create_trace`（dual_writer.py:179-195）也只是透传 `metadata=metadata`。
- `presenter_storage._ensure_trace`（presenter_storage.py:121-129）调 `dual_writer.create_trace(..., metadata=metadata)`，metadata 来自 `_build_trace_metadata()`（presenter_storage.py:121）。
- **结论：create_trace 不用改签名**。只要 `_build_trace_metadata` 返回的 dict 多了 `persona_preset_id` key，它就会随 metadata 整体落库到 `traces.metadata.persona_preset_id`。

### 11. 兼容性

- 历史 trace 无 `metadata.persona_preset_id`，按角色统计时 `$match {"metadata.persona_preset_id": preset_id}` 自然过滤掉旧数据（字段不存在不匹配）。无需迁移。
- `traces.metadata` 现有 key：`agent_name`、`user_id`、`username`（来自 `_build_identity_metadata`）、`step_count`、`tool_calls`（complete_trace 时写入，presenter_storage.py:220-224）。加 `persona_preset_id` 不冲突。
- **索引建议**（PR2 后端聚合时）：在 `traces` 上加 `metadata.persona_preset_id` 索引可加速按 preset 过滤。`trace_storage._ensure_indexes`（trace_storage.py:127-173）目前无此索引。PR2 implement 可在 `AnalyticsStorage.ensure_indexes`（analytics/storage.py:81-96）补 `self.traces.create_index([("metadata.persona_preset_id", 1), ("started_at", -1)])`。

---

## 给 implement 的精确改动清单（按文件:行号）

### A. `src/infra/writer/presenter_config.py`
- **:68 后**追加字段：
  ```python
  persona_preset_id: Optional[str] = None  # Persona preset ID (for per-preset analytics)
  ```
  （`Optional` 已导入，无需新 import）

### B. `src/infra/writer/presenter_storage.py`
- **:83 `metadata.update(...)` 后、`:84 return` 前**插入：
  ```python
  if self.config.persona_preset_id:
      metadata["persona_preset_id"] = self.config.persona_preset_id
  ```

### C. `src/infra/task/executor.py`
- **run_task 签名 :80 `active_goal: Optional[Dict[str, Any]] = None,` 后**加：
  ```python
  persona_preset_id: Optional[str] = None,
  ```
- **PresenterConfig 构造 :102 `trace_id=existing_trace_id,` 后**加：
  ```python
  persona_preset_id=persona_preset_id,
  ```

### D. `src/infra/task/manager.py`
- **submit 签名 :250 `active_goal: Optional[Dict[str, Any]] = None,` 后**加：
  ```python
  persona_preset_id: Optional[str] = None,
  ```
- **submit 内 run_task 调用 :333 `active_goal=active_goal,` 后**加：
  ```python
  persona_preset_id=persona_preset_id,
  ```
- **submit_arq 签名 :368 `active_goal: Optional[Dict[str, Any]] = None,` 后**加：
  ```python
  persona_preset_id: Optional[str] = None,
  ```
- **submit_arq 内 payload_store.save dict :423 `"active_goal": active_goal,` 后**加：
  ```python
  "persona_preset_id": persona_preset_id,
  ```

### E. `src/infra/task/arq_worker.py`
- **run_task 调用 :116 `active_goal=payload.get("active_goal"),` 后**加：
  ```python
  persona_preset_id=payload.get("persona_preset_id"),
  ```

### F. `src/api/routes/chat.py`
- **task_context dict :450 `"active_goal": active_goal_data,` 后**加：
  ```python
  "persona_preset_id": request.persona_preset_id,
  ```
- **submit_arq 调用 :557 `active_goal=active_goal_data,` 后**加：
  ```python
  persona_preset_id=request.persona_preset_id,
  ```
- **submit 调用 :580 `active_goal=active_goal_data,` 后**加：
  ```python
  persona_preset_id=request.persona_preset_id,
  ```
  （`request.persona_preset_id` 来自 AgentRequest，agent.py:47，已存在）

### G. `src/infra/task/concurrency.py`
- **_dispatch_queued_task Redis 分支 :568 `active_goal = task_ctx.get("active_goal")` 后**加：
  ```python
  persona_preset_id = task_ctx.get("persona_preset_id")
  ```
- **run_task 调用 :622 `active_goal=active_goal,` 后**加：
  ```python
  persona_preset_id=persona_preset_id,
  ```
- **legacy fallback 分支 :588 `active_goal = pending.get("active_goal")` 后**加：
  ```python
  persona_preset_id = pending.get("persona_preset_id")
  ```
  并在对应 run_task 调用（同一 :622 处，两条分支共用）传 `persona_preset_id=persona_preset_id,`（已覆盖）。

### H. `src/infra/task/recovery.py`
- **submit_recovery_run task_context dict :202 `"team_id": session_metadata.get("team_id"),` 后**加：
  ```python
  "persona_preset_id": session_metadata.get("persona_preset_id"),
  ```
- **_submit_recovery_task 调用 :245 `team_id=session_metadata.get("team_id"),` 后**加：
  ```python
  persona_preset_id=session_metadata.get("persona_preset_id"),
  ```
- **trace_presenter PresenterConfig :272 `trace_id=recovery_trace_id,` 后**加：
  ```python
  persona_preset_id=session_metadata.get("persona_preset_id"),
  ```

### I. `src/infra/agent/wecom/handler.py`
- **submit 调用 :403 `persona_system_prompt=persona_system_prompt,` 后**加：
  ```python
  persona_preset_id=preset_id,
  ```
  （`preset_id` 变量在 handler.py:199 已存在且非空校验过）

### J.（可选，PR2 后端聚合时）`src/infra/analytics/storage.py`
- **ensure_indexes :93 `logger.info(...)` 前**加：
  ```python
  await self.traces.create_index(
      [("metadata.persona_preset_id", 1), ("started_at", -1)],
      background=True,
      name="metadata_preset_started_at_idx",
      sparse=True,
  )
  ```

---

## Caveats / Not Found

- **未连接真实 MongoDB 验证** trace 落库后 `metadata.persona_preset_id` 实际存在，结论基于写入链路代码（`_build_trace_metadata` → `create_trace` metadata 透传）推断，链路单一明确，可信度高。
- **`persona_preset_id` 传 None 时的行为**：`_build_trace_metadata` 的 `if self.config.persona_preset_id:` 守卫确保 None/空字符串不写 key（metadata 不含该 key），与历史 trace 一致，聚合 `$match` 自然过滤。这是期望行为。
- **team agent 的 persona_preset_id**：team agent（agent_id="team"）走 chat.py 同一 submit 链路，`request.persona_preset_id` 可能为 None（team 模式通常不带 persona），此时不写 key，符合预期。
- **recovery 路径的 persona_preset_id 来源**：从 `session_metadata.get("persona_preset_id")`（recovery.py:296 已确认 session metadata 有此 key）。若历史 session 无此 key（PR2 前创建），recovery trace 也不写，符合兼容性。
- **是否需要改 `_execute_agent_stream` 签名**：**不需要**。persona_preset_id 是 trace 元数据，与 agent stream 执行无关。agent 已通过 `persona_system_prompt` 拿到 persona 内容。
