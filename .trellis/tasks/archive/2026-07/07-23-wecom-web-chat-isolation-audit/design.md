# Design: 企微运行时与 Web 聊天强隔离

## Decision

采用独立企微 runtime 进程作为目标架构。FastAPI 不再承载
`wecom-aibot-sdk` 的连接、重连和回调任务。

在进程拆分前先修复当前连接生命周期与状态机缺陷，建立可验证基线。

## Boundaries

### API process

- 提供 Web HTTP、SSE 和 WebSocket。
- 读取 `wecom:status:{preset_id}` 展示企微状态。
- 配置保存后向企微 control channel 发布 reload/reconnect 命令。
- 不导入或实例化 `WSClient`。

### WeCom runtime process

- 初始化日志、数据库设置、Redis 和 Agent/LLM 所需服务。
- 持有 `WeComBotManager` 和所有 SDK client。
- 继续使用现有 Redis node membership 与 per-aibotid lease。
- 订阅 control channel，执行 reload/reconnect 并写回 command result。
- 独立处理 shutdown，必须 await 所有 SDK disconnect 和内部 tasks。

### Agent execution

- Web 和企微都继续通过共享 task backend 提交 run。
- 生产模式建议 `ARQ_EMBEDDED_WORKER=False` 并运行独立 ARQ worker，使 API、
  企微 transport 和 Agent execution 分属不同进程。
- 此项不是企微隔离的替代条件，但能进一步减少 Web API 故障域。

## Control Contract

建议 Redis stream/pub-sub 消息：

```json
{
  "command_id": "uuid",
  "action": "reload_preset",
  "preset_id": "preset-id",
  "requested_at": "iso8601",
  "requested_by": "user-id"
}
```

结果键或 reply stream：

```json
{
  "command_id": "uuid",
  "status": "ok|not_owner|failed|timeout",
  "node_id": "runtime-node-id",
  "detail": "optional"
}
```

API 应在有限时间内等待 owner ack；超时返回 503，不能误报重连成功。

## Connection State Contract

- `CONNECTING`：已创建 client，尚未收到 authenticated。
- `CONNECTED`：只由 SDK authenticated 事件进入。
- `RECONNECTING`：SDK 明确安排下一次连接。
- `FAILED`：当前尝试失败；如果仍会重连，状态可随后进入 reconnecting。
- `DISCONNECTED`：显式停止、lease 丢失、被新连接替换或重试耗尽。

`WSClient.connect()` 返回不代表认证成功。

## Lifecycle Contract

- start 创建并保存 client，再调用 connect。
- stop 必须 await SDK disconnect。
- 所有项目创建的 background tasks 必须被跟踪、限量并在 stop 时 drain/cancel。
- reload 必须在启动新 client 前确认旧 client 已完全停止。
- shutdown 应设置有限超时；超时后记录并退出进程，不阻塞 API shutdown。

## Compatibility

- Persona 企微配置、Redis status key 和 aibotid lease key保持不变。
- 管理端状态读取接口保持不变。
- reconnect API 的内部实现从本地 manager 调用改为 control channel，响应 schema
  尽量保持兼容。
- 开发环境可保留显式 `embedded` 模式，但默认/生产推荐 `external`。

## Rollout

1. 修复 await/status/task lifecycle，加入故障注入测试。
2. 增加 runtime mode 配置和独立入口，默认保持 embedded 以验证兼容。
3. 部署外部企微 runtime，API 切换 external，验证状态/reconnect。
4. 生产稳定后将 external 设为推荐默认。
5. 可选：关闭 embedded ARQ，进一步拆分 Agent worker。

## Rollback

- 保留 `embedded` runtime mode 作为短期回滚开关。
- control channel 不可用时，状态读取仍从 Redis 工作；重连 API 返回明确 503。
- 回滚不得恢复未 await disconnect 或错误 connected 状态机。

