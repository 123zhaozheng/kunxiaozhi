# LambChat 企业微信本地代码审计

审计日期：2026-07-23。结论基于当前工作树静态代码与相关自动化测试，不等价于真实企微环境验收。

## 1. 端到端数据流

```text
企微 AI Bot WebSocket push
  → WeComBot 按 message.text/image/file/voice/video/mixed 解析
  → msgid 本地 + Redis NX 去重
  → handler 从 aibotid 查 preset_id/config
  → userid 查 LambChat user_id
  → resolve_persona_request 解析 persona prompt/skills/snapshot
  → preferred_agent_id 选择 fast/search/team
  → chat_id 查 Redis session（当前缺少 aibotid 维度）
  → 入站媒体 download/decrypt → S3 → attachments
  → TaskManager.submit → execute_wecom_agent
  → Redis 事件流 → WeComResponseCollector
  → reply_stream 或完整/分段主动消息
  → reveal_file 结果 → S3 download → upload_media → media_id → 企微媒体消息
```

## 2. Persona 路由：已接通，但会话隔离不完整

- `src/infra/agent/wecom/handler.py:570-585`：`aibotid → preset_id/config`，缺映射时直接 return。
- `handler.py:617-656`：构造 `AgentRequest(persona_preset_id=...)`，复用 Web 的 `resolve_persona_request`，得到 persona snapshot、system prompt、skills、Dify KB scope，并通过 `resolve_persona_agent_id` 使用 persona 的 `preferred_agent_id`。
- `handler.py:795-809`：submit 时实际传入 agent、persona prompt、skills、preset id、attachments。
- `tests/infra/agent/wecom/test_preferred_agent_resolve.py` 和 `test_wecom_dify_kb_agent_options.py` 验证局部契约。

关键 P1：`handler.py:49-66` 的 Redis key 是 `wecom:session:{chat_id}`，默认 session id 是 `wecom_{chat_id}`。`aibotid`、`preset_id` 和 `chat_type` 均不在 key 中。单聊按 `bot.py:352-364` 将缺失 chatid 回退为 sender userid，所以同一员工与多个数字人交谈会共享 session；`/new`（`handler.py:69-87`）、旧 run 取消（`:710-740`）和项目绑定也随之串线。

完成度判断：消息级 persona 路由约 80%；满足“多个数字人独立交流”的端到端用户故事约 55%。

## 3. 用户与会话归属

- `handler.py:587-614` 以企微 userid 作为 LambChat username 查用户，成功后使用 Mongo user id。
- 查不到或异常时退回原始 sender id，并继续创建/提交任务。这能维持可用性，但可能产生 Web 用户无法归属/查看的数据。
- `handler.py:148-215` 会迁移 channel project 和绑定 session；这改善了历史数据，但无法修复跨 persona session key 碰撞。

## 4. 刷新/重连真实语义与缺陷

- UI `WeComConnectionIndicator.tsx:50-68` 的刷新图标只在 disconnected/failed/unknown 显示。
- `useWeComStatusPoll.ts:83-99` 调 `POST /{preset}/wecom/reconnect` 后立即 refresh；任何 2xx 都返回 true。
- `src/api/routes/persona_preset.py:303-321` 调 `manager.reload_preset`，但忽略其 bool 结果，然后立即读取 Redis status 返回。
- `manager.py:169-225` 停旧 bot、重读配置、做 owner 选择并启动；缺 secret、owner 在其他节点等分支也可能返回 true 或被路由层忽略。
- 多节点下 API 请求若落在非 owner 节点，当前节点不会向 owner 发重连命令；20 秒 reconcile（`manager.py:29, 322-326`）仅重新对齐配置，已运行的 owner bot 会因 `replace_existing=False` 保持原样。
- 状态 Redis TTL 为 7 天（`status.py:20-21`），`resolve_wecom_status` 只读状态，不交叉验证 lease、node membership 或 heartbeat。进程崩溃时可能长期假绿。
- `preset_has_wecom`（`config_storage.py:455-460`）只检查非空 aibotid，不检查 secret/可连接性。
- 默认 user 角色包含 `channel:manage`（`src/infra/auth/rbac.py:159-160`），所以普通登录用户可看状态并重连。

完成度判断：单节点、正常配置时可工作；可靠控制面约 50%，多节点和故障状态不足。

## 5. 长文本分段

- `collector.py:37-81` 有 UTF-8 2048 字节切分，优先段落和换行，最后按字符边界硬切。
- 默认 `stream_reply=true`（`handler.py:659-661`）。正常路径通过 `append_stream_chunk → reply_stream` 持续发送完整累计文本（`collector.py:169-210, 312-334`），最终仍是一个 stream 气泡（`:500-542`）。
- 切分只在 6 分钟超时主动回退（`:544-591`）或非流式/流式失败发送（`:593-650`）中调用。
- 现有切分不识别中文句末标点、Markdown 代码围栏/链接，不加段序，不限总段数，不做重试/幂等，也无节奏延迟。
- tests 中没有 collector、分段器或正常流式超长回复测试；相关 handler 测试反而把 `segmented_reply` 设为 false 并 mock collector。

完成度判断：辅助函数存在约 40%；默认用户故事约 20%，当前不能宣称已实现“像人一样自动分段”。

## 6. 入站附件

- `bot.py:234-239` 注册 image/file/voice/video/mixed。
- `handler.py:282-530` 对 image、file、无转写 voice、mixed 子项执行 SDK 下载、S3 上传、file record 和 attachment 构造。
- video 当前明确不构造 attachment；测试 `test_video_message_no_attachment` 固化的是占位降级。
- `tests/infra/agent/wecom/test_inbound_attachments.py` 覆盖 image/file/voice/mixed、下载失败、S3 失败、无 bot 和 video 降级，但均为 mock，不验证真实 URL/AES 解密、S3/模型可访问性。

完成度判断：主要类型约 70%，真机协议和 video 缺口仍在。

## 7. 出站附件

- `handler.py:907-946` 从所有 tool result 中提取 `images`/`blocks`，或任何带 `key + url + type/mime` 的 reveal 风格结果，未限定 tool_name。
- `collector.py:392-488` 遍历本轮所有 `files_to_reveal`，从 S3 下载到临时文件，image 之外统一按 file，逐一发送。
- `bot.py:1083-1138` 调 SDK `upload_media`，取 media_id，再优先 `reply_media`，否则 `send_media_message`。
- SDK 1.0.8 的 `upload_media` 是 init → 512KB chunks（最多 100）→ finish，理论约 50MB；项目也以 50MB 为内部下载上限。
- 当前没有“只发一个”限制，没有验证 key 属于当前用户/session/trace，没有企微媒体类型上限预检；远程 URL reveal 把 URL 当 storage key，实际会失败。
- 没有自动化测试覆盖 `upload_and_send_files` 或真实 SDK 方法调用。

完成度判断：主干链路约 55%，安全与产品语义闭环约 30%。

## 8. 渠道提示词

- 当前企微仅把 persona system prompt 传给 agent；未检索到 wecom/channel-specific prompt 注入。
- `persona_system_prompt` 已是 run 参数（`TaskManager.submit:232-340`），agent 每次构建 prompt 时消费，因此具备实现 run-scoped section 的基础。
- 不建议把渠道文字拼进 persona 本体。应新增结构化 run context，并由 Fast/Search/Team 共用 middleware 动态生成 section；Web run 不传即可自然移除。

完成度判断：显式企微渠道感知约 0%；底层 run 参数通道已具备。

## 9. 风险优先级

1. P1：session key 缺 aibotid，跨数字人串上下文。
2. P1：正常流式长文本绕过分段。
3. P1：重连在多节点可能假成功，connected 状态可陈旧 7 天。
4. P1：出站 reveal 发送全部文件且缺归属校验。
5. P2：企微 userid 映射失败仍写入伪 user id。
6. P2：video 入站、远程 reveal、媒体类型/大小反馈不完整。

