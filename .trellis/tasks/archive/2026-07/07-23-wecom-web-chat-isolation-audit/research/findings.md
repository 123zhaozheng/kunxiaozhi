# 企微连接故障与 Web 聊天隔离调研

## 结论摘要

当前实现已经避免了“企微握手完成之前 FastAPI 不对外服务”的直接启动阻塞，但没有达到强隔离：

1. 企微运行时通过 `asyncio.create_task()` 启动在 FastAPI 进程中，与 HTTP、SSE、WebSocket 管理器和默认的嵌入式 ARQ worker 共享同一个 asyncio 事件循环。
2. `wecom-aibot-sdk==1.0.8` 的握手和退避使用异步 I/O 与 `asyncio.sleep()`；受控超时探针中，握手等待期间其他协程持续运行，因此单次 `opening handshake timeout` 本身没有证据会阻塞 Web 聊天。
3. 项目封装存在两个已确认缺陷：
   - SDK 在握手失败时吞掉异常、安排重连后正常返回；`WeComBot.start()` 随后错误地标记 `CONNECTED` 并返回成功。
   - `WeComBot.stop()` 调用异步 `WSClient.disconnect()` 时未 `await`，导致 SDK 的接收、重连和回调任务可能未被取消。配置重载或停启后可能累积后台任务。
4. 用户日志中的 Web run 停止有明确的独立触发源：`POST /api/chat/sessions/.../cancel`、`User cancel event recorded` 和随后的 `Task cancelled`。现有日志不能证明企微握手超时触发了该取消。
5. 因为企微 SDK 与 Web 链路共享事件循环，未来 SDK 中的同步阻塞、任务泄漏、回调风暴或大量机器人重连仍能拖慢 HTTP/SSE/嵌入式 ARQ。若业务要求“企微无论如何故障都不能影响 Web”，必须增加进程级隔离，不能只依赖后台协程。

## 启动与运行时调用链

### API 启动

1. `src/api/main.py:376` 进入 FastAPI lifespan。
2. `src/api/main.py:439-441` 启动分布式运行时服务。
3. `src/infra/runtime_services.py:80-89` 启动事件循环延迟监控、任务 pub/sub 和 ARQ runtime。
4. `src/infra/task/arq_runtime.py:19-49` 在默认
   `TASK_BACKEND=arq`、`ARQ_EMBEDDED_WORKER=True` 下，通过
   `asyncio.create_task(self._worker.async_run())` 把 ARQ worker 嵌入 FastAPI
   进程。
5. `src/api/main.py:470-480` 通过 `asyncio.create_task(_start_wecom())`
   后台启动企微，不等待企微连接成功后才 `yield` 应用。

结论：企微不是 FastAPI 启动门闩，但 API、SSE、企微和 Agent 执行默认共享同一事件循环。

### 企微连接

1. `src/infra/agent/wecom/handler.py:1224-1236` 创建 handler 并启动 manager。
2. `src/infra/agent/wecom/manager.py:69-83` 加载所有 Persona 企微配置。
3. `src/infra/agent/wecom/manager.py:103-167` 串行处理每个配置。
4. `src/infra/agent/wecom/manager.py:356-405` 获取独立 Redis lease 后等待
   `bot.start()`。
5. `src/infra/agent/wecom/bot.py:202-260` 构造 SDK 客户端并等待
   `client.connect()`。
6. SDK `wecom_aibot_sdk/ws.py:90` 使用
   `websockets.asyncio.client.connect()`；当前 websockets 15.0.1 默认
   `open_timeout=10`、`proxy=True`。
7. SDK `wecom_aibot_sdk/ws.py:327` 使用指数退避，最大 30 秒，默认最多
   10 次重连。

结论：每个握手可能让企微初始化任务等待约 10 秒，但等待是异步的；多个 bot
串行启动会拖慢企微自身就绪时间，不直接阻止 API 接受请求。

## 已确认问题

### F1. Web 与企微没有硬隔离

证据：

- `src/api/main.py:479`：企微 task 位于 API 事件循环。
- `src/infra/task/arq_runtime.py:49`：默认 ARQ worker 也位于同一事件循环。
- `src/kernel/config/base.py:132-135`：默认 `TASK_BACKEND=arq`、
  `ARQ_EMBEDDED_WORKER=True`。

影响：

- 正常异步握手不会阻塞 Web。
- SDK 或回调一旦执行同步阻塞、产生大量 ready tasks、CPU 密集处理或任务泄漏，
  HTTP、SSE 和 Agent worker 都可能出现延迟。
- 这只能称为逻辑并发，不能称为故障域隔离。

分类：**已确认架构风险；尚无证据证明本次单次握手超时已造成 Web run 中断。**

### F2. 停止连接时没有 await

证据：

- `src/infra/agent/wecom/bot.py:262-272` 中
  `self._ws_client.disconnect()` 未 `await`。
- SDK `WSClient.disconnect()` 是异步函数，负责取消重连、接收、心跳和 handler
  tasks（`wecom_aibot_sdk/client.py:203`、
  `wecom_aibot_sdk/ws.py:543`）。
- 受控探针结果：`disconnect_call_count=1`，
  `disconnect_await_count=0`。

影响：

- stop/reload 后旧客户端仍可能继续重连。
- 多次 Persona 配置重载可积累连接管理器、回调 tasks 和日志。
- 应用关闭不能保证释放企微资源。

分类：**已确认缺陷，可能影响共享事件循环稳定性。**

### F3. 握手失败被错误报告为启动成功

证据：

- SDK `WsConnectionManager.connect()` 捕获握手异常后触发 error、安排重连，
  但不重新抛出。
- `src/infra/agent/wecom/bot.py:245-250` 在 `await client.connect()` 返回后无条件
  保存 client、设置 `CONNECTED` 并返回 `True`。
- 受控探针结果：
  - `start_result=True`
  - `bot_state=connected`
  - `sdk_socket_connected=False`

影响：

- 健康状态在失败/重连期间可能出现错误翻转。
- manager 会保存一个未认证但 `is_running=True` 的 bot。
- 状态观测不可靠，排障时容易误判。

分类：**已确认状态机缺陷；主要影响诊断与恢复，不会直接取消 Web run。**

### F4. 状态发布是未跟踪的 fire-and-forget task

证据：

- `src/infra/agent/wecom/bot.py:170-188` 每次状态变化通过
  `asyncio.ensure_future()` 写 Redis，未保存、限流或在 shutdown 时 drain。

影响：

- 正常数量下风险较低。
- 多 bot、状态抖动或 Redis 变慢时会增加同一事件循环中的待处理任务。

分类：**理论风险，需要压力测试量化。**

## 用户日志事件解释

| 时间 | 事件 | 结论 |
|---|---|---|
| 11:42:56 | SDK opening handshake timeout | 企微外连失败，SDK 进入第 6 次退避重连 |
| 11:43:12 | `GET /api/agents` 200，22.61ms | API 事件循环仍能正常处理请求 |
| 11:43:15 | SDK 发起下一次连接 | 企微后台重连 |
| 11:43:17 | `POST .../cancel` | Web 客户端或用户明确发起取消请求 |
| 11:43:17 | memory/Redis interrupt、Mongo trace 更新 | 正常取消传播 |
| 11:43:18 | ARQ `Task cancelled`、payload 删除 | Agent run 正常完成取消清理 |

这段日志支持“企微失败与 Web API 同时运行”，不支持“企微超时导致 Web run
被取消”。若用户没有点击停止，需要另查前端为何发送 `/cancel`；那是独立问题。

## 受控探针

### 握手超时是否阻塞事件循环

方法：将 SDK 底层 connect 替换为 `await asyncio.sleep(0.2)` 后抛
`TimeoutError`，同时每 10ms 运行 ticker。

结果：

- `ticker_ticks=15`
- `reconnect_attempts=1`

结论：超时等待期间其他协程获得调度；单次异步握手超时不是事件循环硬阻塞。

### 停止与状态机

- AsyncMock 证明 `disconnect()` 被调用但从未 await。
- 模拟握手失败证明 `WeComBot.start()` 返回成功且状态为 connected，而底层 socket
  为未连接。

## 风险矩阵

| 耦合点 | 当前状态 | 风险 | 结论 |
|---|---|---:|---|
| FastAPI 启动门闩 | 企微在后台 task | 低 | 已隔离 |
| asyncio 事件循环 | API、SSE、企微、嵌入式 ARQ 共享 | 高 | 未硬隔离 |
| 握手 I/O | websockets 异步 connect，10s timeout | 低 | 本身不会阻塞 |
| 重连任务 | 每 bot 最多 10 次、30s 上限 | 中 | 正常有界；stop 泄漏会破坏边界 |
| Redis lease | `isolated_pool=True` | 低 | 与通用 Redis 连接池隔离 |
| 状态 Redis 写入 | 未跟踪 task | 中 | 抖动时可能堆积 |
| Agent 并发槽/取消 | run_id 驱动，无企微连接状态依赖 | 低 | 未发现直接传播路径 |
| 日志 | 每次失败和状态变化均记录 | 低到中 | 多 bot/泄漏时可能放大 |
| 连接健康状态 | 握手失败后可能误报 connected | 高 | 已确认缺陷 |

## 推荐方案

### 推荐：企微 runtime 独立进程

将企微连接管理、SDK 回调和企微消息处理放到独立 runtime 进程；FastAPI 只提供
管理 API、读取 Redis 状态并通过 Redis control channel 请求 reload/reconnect。

理由：

- 只有进程边界能隔离 SDK 的同步阻塞、GIL/CPU、任务泄漏和崩溃。
- 现有 Redis lease/status 已经提供跨进程协调基础。
- Web API、SSE 和 Agent worker 不再与企微 SDK 共用事件循环。

### 必须先修的局部缺陷

无论是否立即拆进程，都应先完成：

1. `await self._ws_client.disconnect()`，并增加 shutdown/reload 回归测试。
2. 以 SDK `authenticated` 事件作为 connected 真值；connect 返回不等于认证成功。
3. 跟踪并 drain 状态发布 tasks，设置上限或合并同一 bot 的状态更新。
4. 增加握手超时期间 HTTP/SSE/Agent 调度仍可工作的故障注入测试。

### 不推荐方案

- 仅保留 `asyncio.create_task()`：不能隔离同步阻塞或任务泄漏。
- 仅把 ARQ worker 改成外部进程：能保护 Agent 执行，但 API/SSE 仍与企微共享循环。
- 单独线程运行企微：跨事件循环 client/lock 容易误用，且不能隔离 GIL/进程崩溃。

## 验证矩阵

| 场景 | 故障注入 | Web 验证 |
|---|---|---|
| 应用启动 | connect 永久等待或连续 timeout | health/API 在目标时间内 ready |
| 正常聊天 | 企微每次握手 timeout | 创建 run、SSE 首包和完成不超基线阈值 |
| 用户取消 | 企微处于 reconnect sleep/handshake | cancel 200，run/payload/并发槽正确清理 |
| 会话读取 | 企微 Redis 状态写入变慢/失败 | session GET/列表正常 |
| 配置重载 | 连续 reload 20 次 | 旧 SDK reconnect/handler tasks 数不增长 |
| 多 bot | N 个 bot 同时失败 | API p95、事件循环 lag、内存和 task 数受控 |
| 企微进程崩溃 | kill runtime | Web 全链路无错误，状态变 stale/disconnected |

