# feat: Persona 点赞/点踩通知（Web 实时 + 企业微信主动推送）

## Goal

当某个 Persona 被企业微信用户点赞或点踩时，通知该 Persona 配置的"通知对象"（指定的人）。
通知走两个渠道：
1. **Web 实时通知**：通过现有 WebSocket 通道实时推送到目标用户的浏览器（浏览器通知 + 站内提示）。
2. **企业微信主动推送**：通过对应 bot 的长连接（`aibot_send_msg`）主动推送消息给目标用户（仅对已绑定用户）。

Persona 未配置通知对象时，不产生任何通知。通知对象配置 UI 仅对 admin 开放。

## Background / Context

- 企业微信 AI Bot 通过 WebSocket 长连接接入（`src/infra/agent/wecom/`），点赞点踩回调已打通：`_handle_wecom_feedback`（handler.py）将 WeCom `feedback_event` 同步写入昆小智 `FeedbackManager`。
- `WeComBot.send_message(chat_id, content)` 已支持主动推送（`aibot_send_msg`），单聊 `chat_id` = 用户 userid（= 昆小智 username）。
- 官方限制：**用户必须先给 bot 发过消息（建立会话），之后才能主动推送**；频率 30 条/分钟、1000 条/小时每会话。因此需要"绑定"机制：目标用户给 bot 发送指定指令完成绑定后，系统才能向该用户主动推送。
- Web 实时通道已存在：`WebSocketManager.send_to_user_with_broadcast(user_id, msg)`（Redis pub/sub 分布式定向投递），前端 `useWebSocket` 已处理 `task:complete` 事件，浏览器通知走 `appNotificationService`。
- Persona WeCom 配置存于 MongoDB `persona_wecom_config`（aibotid、secret、stream_reply 等），API 已在 `src/api/routes/persona_preset.py`（`GET/PUT/DELETE /{preset_id}/wecom`），权限 `channel:manage`。

## Requirements

### R1: Persona 通知对象配置（admin）
- `persona_wecom_config` 增加 `feedback_notify_targets: list[str]`（企业微信 userid / 昆小智 username 列表）。
- 空列表或未配置 = 不通知。
- 配置 API 仅 admin（沿用 `channel:manage` 权限，与现有 wecom 配置路由一致）。
- 前端配置 UI 仅对 admin 可见（按权限渲染），置于 persona 的 WeCom 配置区域。

### R2: 绑定机制
- 目标用户在企业微信中给对应 bot 发送指定绑定指令（建议固定关键字，如 `绑定通知`），bot 识别后：
  - 记录绑定关系（aibotid + username → 绑定时间）。
  - 回复确认消息（含成功/失败提示）。
- 绑定记录持久化（MongoDB 新 collection `wecom_notify_bindings`，唯一索引 `(aibotid, username)`）。
- 绑定状态可在 admin 配置界面查询展示（已绑定 / 未绑定）。
- 绑定指令在 persona 路由**之前**识别处理，不进入 AI 会话（避免污染 persona 对话）。

### R3: 通知触发（点赞/点踩后）
- 触发点：`_handle_wecom_feedback` 成功写入反馈之后（type=1/2 且非取消）。
- 读取该 persona 的 `feedback_notify_targets`；无目标则不通知。
- 通知内容包含：persona 名称、操作者（userid）、rating（赞/踩）、点踩原因/评论（如有）。
- 分发：
  - **Web 渠道**：对每个目标（username → user_id 解析成功者），`send_to_user_with_broadcast` 推送 `notification:feedback` 事件。
  - **WeCom 渠道**：对每个已绑定目标（aibotid + username 有绑定记录者），`WeComBotManager.send_message(aibotid, username, content)` 主动推送。
- 任一渠道失败（用户未绑定、未解析、发送异常）不影响其他渠道，记录日志，不阻塞反馈写入。

### R4: 前端 Web 实时通知接收
- `useWebSocket` 增加 `notification:feedback` 事件处理 → 通过 `appNotificationService` 触发浏览器通知（含标题/内容/路由）。
- 不做新的持久化站内信（MVP 实时推送即可；通知中心后续可选扩展）。

### R5: 边界与容错
- 反馈取消（type=3）不触发通知。
- 通知目标中的未知 username：忽略并记录日志，不报错。
- WeCom 主动推送目标未绑定：跳过 WeCom 渠道，Web 渠道照常。
- 通知发送失败不影响反馈主流程（try/except 包裹，日志记录）。

## Acceptance Criteria

- [ ] admin 可在 persona WeCom 配置中设置/清空通知对象列表；非 admin 无此配置入口（前端隐藏 + 后端权限拦截）。
- [ ] 目标用户给 bot 发送绑定指令后收到确认回复，绑定记录写入 `wecom_notify_bindings`。
- [ ] admin 配置界面能显示每个目标的绑定状态。
- [ ] persona 被点赞后，绑定的目标用户在企业微信收到主动推送；Web 端在线时收到浏览器/站内实时通知。
- [ ] persona 被点踩后同样通知，内容含点踩原因/评论。
- [ ] persona 未配置通知对象时，点赞点踩不产生任何通知。
- [ ] 目标用户未绑定时，Web 渠道正常送达，WeCom 渠道跳过。
- [ ] 反馈取消不触发通知。
- [ ] 绑定指令不进入 AI 会话（persona 不回复该消息，由绑定逻辑处理）。
- [ ] 现有反馈写入、流式回复、分段回复等功能不受影响（回归通过）。
- [ ] ruff / mypy / pytest（后端）、tsc / eslint（前端）通过。

## Out of Scope

- 通知中心持久化列表（MVP 仅实时推送）。
- 通知偏好设置（按用户自定义开关渠道）。
- 邮件/短信等其他渠道。
- Web 端点赞/点踩触发通知（触发源仅企业微信反馈）。
- 群聊消息中点赞点踩的处理（企业微信反馈事件本身单聊/群聊均可，按事件现有逻辑处理即可）。
