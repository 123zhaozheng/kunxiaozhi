# 企业微信数字人接入审计与改造规划

## Goal

在不修改业务代码的规划阶段，确认 LambChat 当前企业微信 AI Bot 接入的真实完成度，并形成可实施方案，使用户能从手机企业微信与指定 persona 数字人稳定交流；连接状态可观测且可恢复；长回复能按人类阅读节奏安全分段；智能体明确 reveal 的单个文件可由企微渠道投递；企微渠道提示仅对当前企微 run 生效，不污染 PC/Web 会话。

## Background

当前代码已经包含企微 WebSocket、persona 映射、入站附件、流式回复、连接状态和 reveal 文件发送等部件，但“存在函数”不等于默认端到端链路已满足用户故事。2026-07-23 的静态审计、历史会话检索、Git 历史、24 个相关测试、Valyu 官方资料检索和 DeepWiki 开源仓库对比得到以下结论：

- 每个 `aibotid` 能映射到一个全局 persona，并在每条消息上解析 persona prompt、skills、Dify 知识库范围和 `preferred_agent_id`。
- 会话 Redis key 仅包含 `chat_id`，不包含 `aibotid`/persona；同一用户与多个数字人单聊会发生会话碰撞。
- UI 刷新图标执行的是“重启服务端 AI Bot WebSocket”，不是激活 persona 或用户重新授权。
- 默认流式回复不经过现有长文本切分函数；该函数只在非流式/流式失败和 6 分钟超时回退路径生效。
- 出站附件已复用 `reveal_file` 事件和企微 SDK 媒体上传，但会发送本轮收集到的所有文件，缺少单文件、归属和企微类型/大小的强制边界。
- 企微 run 尚未注入专门的渠道上下文；当前只有通用 reveal 提示和 persona prompt。

详细证据见 `research/local-code-audit.md`、`research/official-wecom.md`、`research/open-source-comparison.md` 和 `research/history-and-tests.md`。

## Requirements

### R1. Persona 路由与会话隔离

- 入站消息必须按 `aibotid` 解析到唯一 `preset_id`，再解析 persona snapshot、允许 skills/知识库和首选 agent。
- 会话身份必须至少包含 `channel=wecom + aibotid + chat_type + chat_id`；不得让同一用户与不同数字人共享历史、current run、`/new` 指针或项目绑定。
- 单聊与群聊必须使用确定性、可迁移且无碰撞的 session key。
- persona 不存在、已停用、配置缺 secret 或路由缺失时，必须有可观察失败，不得静默丢消息。
- 企微 userid 到 LambChat user id 映射失败时须执行明确策略；不得长期以企微 userid 伪装合法 Mongo user id 而产生不可归属数据。

### R2. 连接状态与刷新/重连

- UI 必须准确表述为“企微连接重连”，不得与 persona 激活混淆。
- 重连操作必须抵达当前租约 owner 节点；API 落在非 owner 节点时不能返回假成功。
- API 必须返回 `request_id`、接受/执行状态、owner/节点信息和失败原因；前端不能只以 HTTP 2xx 显示成功。
- `connected` 状态必须结合心跳、租约和节点存活判定新鲜度；进程异常退出后不能保绿 7 天。
- 重连后应轮询到终态或超时，并向用户区分鉴权失败、配置缺失、被另一连接替换、租约丢失和网络失败。
- 权限须明确：当前默认 `user` 拥有 `channel:manage`，因此普通登录用户可重连；目标方案建议收紧为管理员/运维权限，而不是只隐藏按钮。

### R3. 长文本分段

- 默认配置下的正常流式回复也必须经过长度治理，不能只覆盖失败回退路径。
- 分段按 UTF-8 字节计数，并优先在段落、句号/问号/叹号、换行边界切分；超长无标点文本按 Unicode 字符安全硬切。
- Markdown 代码围栏、链接和列表应尽量保持完整；无法保持时采用明确的续段策略。
- 多段发送必须顺序串行、可重试、可去重，并受企微单会话 `30 条/分钟、1000 条/小时`约束。
- “流式刷新同一气泡”和“多个独立消息气泡”必须区分：目标是多个可读气泡，而不只是协议层多次覆盖同一 stream。
- 默认最多发送有限段数；超出上限时提供摘要/文件交付或明确截断提示。

### R4. 入站附件

- 图片、普通文件、语音、混合消息按官方长连接字段下载/解密、存储并作为本轮 attachment 交给 agent。
- 视频、失败降级、文件名/MIME、大小、恶意 URL/AES key 和外部模型可访问 URL 必须有明确契约和测试。
- 入站文件记录必须绑定真实用户、session、trace/project，供权限审计和生命周期清理。

### R5. 出站附件与 harness

- 不新增企微专属“上传文件”工具；继续复用 `reveal_file` 作为跨渠道的用户交付语义。
- 为企微 run 注入提示：明确当前回复将送达企业微信；只有本轮显式 `reveal_file` 的单个文件会被尝试推送；`reveal_project`、目录、整个项目和未 reveal 路径不会推送。
- 渠道适配层必须强制只发送一个合格文件，不能只依赖模型遵守提示词。
- 文件须属于当前用户/session/trace 的 reveal 记录；路径、目录、工作区外文件、任意已知 S3 key 和未经控制的远程 URL 不得绕过校验。
- 发送前按 SDK/企微媒体类型校验大小和格式；上传采用 `upload_media → media_id → reply_media/send_media_message`。
- 额外文件、过大/不支持类型、上传失败和发送失败必须产生用户可见反馈及结构化审计日志。

### R6. 渠道上下文隔离

- 新增 run-scoped、结构化 `delivery_context`/`channel_context`，不要把企微说明写回 persona preset。
- Fast/Search/Team 三条 agent 构建路径都在当前 run 的 system section 中消费该上下文。
- Web/PC run 使用 `channel=web` 或无企微 section；即使复用同一 checkpoint，也要在下一次 prompt 构建时移除企微 section。
- 渠道上下文不得进入长期记忆、persona snapshot 或跨 run 的 session metadata；恢复/队列执行必须显式携带当前 run 的上下文。

### R7. 证据与验证

- 官方协议结论引用企业微信开发者中心或官方 SDK；开源实践与本项目推断分开标注。
- 单元测试覆盖分段器、文件选择/安全边界和状态新鲜度；集成测试覆盖路由、跨 persona 隔离、重连 owner、正常流式长回复和 reveal 文件发送。
- 有测试企微账号时完成真机验收；无账号时必须标注静态/模拟验证不能证明真实平台行为。

## Acceptance Criteria

- [ ] 同一企微用户依次向两个不同 `aibotid` 发送消息，产生不同 session，历史、run 取消和项目互不串线。
- [ ] 每个消息的 persona prompt、skills、知识库和 preferred agent 与该 `aibotid` 对应 preset 一致。
- [ ] 重连请求在单节点和多节点部署均抵达 owner；UI 只在实际进入连接流程后显示已接受，并最终显示真实终态。
- [ ] 模拟节点崩溃或状态超过新鲜度阈值后，UI 不继续显示 connected。
- [ ] 默认 `stream_reply=true` 且回复超长时，用户收到多个有序、限长、格式尽量完整的独立消息，而非一个无限增长 stream。
- [ ] 同一 run reveal 多个文件时只推送一个合格文件，并对其余文件给出明确反馈。
- [ ] 目录、`reveal_project`、其他用户/trace 的 key、超限文件和不支持类型均不能被推送。
- [ ] 企微 run 能看到渠道交付说明；随后 Web/PC run 的最终 system prompt 不含任何企微专属说明。
- [ ] 入站 image/file/voice/mixed 和出站 image/file 至少各有自动化契约测试；视频能力明确为支持或明确拒绝。
- [ ] 真机验收记录包含单聊、群聊、长回复、文件、断线重连和多数字人隔离。

## Out of Scope

- 本规划阶段不修改业务代码、不部署企微应用、不变更线上凭据。
- 不把目录或完整项目作为企微附件自动发送。
- 不在缺少授权时向真实企业微信用户发送测试消息。
- 不承诺企微未公开接口或第三方插件的非官方行为。

