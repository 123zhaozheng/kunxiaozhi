# 企业微信数字人目标设计

## 1. 设计原则

1. persona 决定“谁在说话”，channel context 决定“通过什么媒介交付”，二者分离。
2. 模型提示用于引导，渠道适配层负责强制安全和协议限制。
3. stream 是首屏体验机制，segment 是消息交付机制，不混为一谈。
4. 所有路由、会话、重连和文件操作都以可审计的确定性身份为基础。

## 2. 目标数据流

```text
WeCom frame
  → ChannelEnvelope(channel=wecom, account=aibotid, chat_type, chat_id,
                    sender_id, msg_id, attachments)
  → PersonaBinding(aibotid → preset snapshot → preferred agent)
  → SessionIdentity(wecom:aibotid:chat_type:chat_id)
  → RunContext(persona + skills + delivery_context + attachments)
  → Agent execution
  → ReplyPlan(text segments + at most one revealed file)
  → Ordered WeCom delivery queue
  → status/audit events
```

## 3. SessionIdentity 与迁移

新 Redis key：`wecom:session:v2:{aibotid}:{chat_type}:{chat_id}`；session id 使用这些字段的稳定、可日志定位的编码或哈希。`/new`、cancel、feedback mapping 和 project binding 都使用同一 identity。

迁移策略：首次访问 v2 key 时，仅在确认旧 key 尚未被另一个 aibotid claim 的情况下迁移旧 session；在 Redis 记录 legacy owner aibotid。无法唯一归属时创建新 session，保留旧数据只读，避免把已污染历史继续扩散。发布期同时记录 v1/v2 命中指标，稳定后移除 v1 fallback。

## 4. 跨渠道 Persona 同构

企微不注入任何 channel-specific system section。`resolve_persona_request` 仍作为 Web/企微共享的权威解析入口，产出相同的 persona snapshot、system prompt、skills、Dify 知识库范围和 preferred agent。

企微 `task_manager.submit` 后必须将以下配置写入 session metadata，与 Web `build_conversation_config` 契约一致：

- `agent_id`：底层执行器，仅用于运行；
- `persona_preset_id` / `persona_preset_name` / `persona_snapshot`：用于 UI 恢复 Persona；
- `agent_options` / skills / MCP 工具配置；
- `project_id`：机器人 channel 项目。

前端恢复时由 `persona_snapshot` 显示 Persona chip；`agent_id` 不作为 Persona 身份展示。

## 5. 长文本 ReplyPlan

新增纯函数 `plan_wecom_text_delivery(text, policy) -> list[Segment]`：

- hard limit 按官方/SDK实测确定，内部目标建议 1200–1800 UTF-8 bytes；
- 边界优先级：双换行 → 换行 → 中文/英文句末 → 空格 → Unicode 安全硬切；
- 代码围栏尽量整体保留；跨段时自动闭合并在下一段重开；
- 每段可加轻量 `(2/4)`，但不污染代码；
- 默认最多 6 段，超过时最后一段说明内容已转为附件/截断，具体策略由产品确认；
- delivery id = run_id + segment index，Redis 记录成功索引以支持幂等重试。

交付状态机：thinking stream → 收集/规划 → 第一段 finalize 被动 stream → 后续段按序主动推送。若平台限制不允许该组合，则回退为结束 thinking stream 后全部主动推送。段间只做小幅可配置节奏（如 200–600ms），并由 rate limiter 统一调度，不使用随机行为影响测试。

## 6. 单文件 reveal 交付

不新增企微工具。事件层只接受 `tool_name == reveal_file` 的成功结果；忽略 `reveal_project` 和其他工具碰巧返回的 `key/url`。

强制策略：

1. 只选择本轮第一个成功、合格的 reveal 记录；
2. 以 `revealed_files` 索引校验 `user_id + session_id + trace_id + key`；
3. 拒绝目录、无记录 key、远程 URL raw key、工作区外/其他用户产物；
4. 按媒体类型与官方上限预检，读取采用流式/spool，避免 50MB 全量驻留内存；
5. `upload_media → media_id → reply_media`，无原 frame 时 `send_media_message`；
6. 将成功/失败/忽略额外文件写入 delivery audit，并给用户发送简短回执。

“单文件”是本需求的产品边界，不是 SDK 限制。未来需要多文件时应显式提升 policy，而不是悄悄遍历所有结果。

## 7. 分布式重连控制面

POST 重连不直接假设当前 API 节点是 owner。建议：

- 写 `ReconnectCommand(request_id, preset_id, aibotid, requested_by, created_at)` 到 Redis stream/queue；
- 持有 lease 的 owner 消费命令，停止旧连接、重新读取 secret、启动连接并写 ack/status；
- API 可短等 ack 后返回，超时则返回 202 accepted，而非 success；
- UI 依据 request id 轮询终态。

状态记录加入 `heartbeat_at`、`lease_owner`、`generation`。resolver 将超过新鲜度阈值、owner node 不活跃或 lease 不匹配的 connected 解释为 stale/disconnected。bot 正常活动/周期心跳更新状态，不依赖 7 天 TTL 表示在线。

## 8. 权限与可观测性

建议新增 `channel:operate` 或仅管理员持有重连权限；普通用户可按需拥有只读 `channel:status:read`。所有重连和文件交付记录 actor、preset/aibotid、owner node、request/run id、结果码和耗时，日志不包含 secret、文件内容或完整敏感路径。

## 9. 兼容与回滚

- session v2 通过 feature flag 双读/单写切换，可回退旧 key，但不删除新 session。
- 不存在 channel prompt，Web 与企微提示完全同构。
- segmented delivery 可按 persona config/全局 flag 关闭，回到现有 stream。
- reconnect command bus 可先单节点 direct + 统一结果类型，随后启用分布式消费。
- 文件新策略上线前保持“文本正常、附件失败不阻断回答”；可通过 flag 关闭企微附件投递。

## 10. 企微渠道虚拟层级

MongoDB `projects` 暂不增加 `parent_id`。现有每机器人一个 `type=channel` 项目已经提供稳定的 session 过滤和历史兼容；前端将所有 channel 项目映射为虚拟树：

```text
企微渠道（虚拟、不可编辑）
  ├─ Persona A（现有 channel project）
  │   └─ sessions
  └─ Persona B（现有 channel project）
      └─ sessions
```

普通项目列表只渲染 `type=custom`；`type=channel` 只出现在企微渠道组。父级和子级均使用 Lucide 线性图标与紧凑导航行，不新增卡片组件。
