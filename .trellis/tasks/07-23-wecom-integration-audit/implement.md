# 实施计划

用户已于 2026-07-23 批准实施；当前任务已进入 `in_progress`。

## 推荐任务拆分与依赖

### A. 会话身份与渠道上下文（优先）

依赖：无。后续 B/C/D 的测试都依赖稳定的 run/session identity。

- 删除企微 channel prompt 与 Fast/Search/Team 注入点，验证 Web/企微 prompt 同构。
- 将 session key 升级为 aibotid + chat type + chat id，设计 legacy claim/migration。
- 修复用户映射失败策略和可观察错误。
- 企微提交后持久化与 Web 一致的 persona session metadata，确保历史会话恢复 Persona。
- 测试同一 userid 两个 aibotid、同 bot 单聊/群聊、`/new`、cancel 和 project binding。

### B. 连接状态与分布式重连

依赖：可独立于 A 开发，但集成验收使用 A 的稳定 account identity。

- 先定义 reconnect command/result API，修复 route 忽略 bool 和前端立即成功。
- 加 status heartbeat/staleness/lease-node 交叉验证。
- 实现 Redis command/ack 到 owner；覆盖非 owner API 节点。
- 拆分只读状态和重连操作权限；更新 UI 文案与错误反馈。
- 故障注入：错误 secret、owner 崩溃、lease lost、server replaced、命令超时。

### C. 正常流式长文本分段

- [x] 在 Persona 企业微信设置中增加每段大致字符数（300/500/600），贯通 API、Mongo、热重载和 collector，并为字符目标与字节硬上限补回归测试。

依赖：A 的 run context/delivery identity。

- 从 collector 抽出纯 segment planner 与 ordered delivery queue。
- 覆盖中文标点、emoji、多字节、无标点、Markdown code fence/链接和段数上限。
- 把 normal stream 纳入 ReplyPlan，验证第一段 stream + 后续 proactive 的客户端行为。
- 添加 per-chat rate limit、segment delivery id、有限重试和幂等。
- 真机确认字节限制、气泡顺序、feedback 和 6 分钟 fallback。

### D. 单文件 reveal 企微投递

依赖：A 的 trace/session/channel context；建议在 C 的 ordered delivery queue 后接入。

- event processor 只接收成功 `reveal_file`，并只选择一个。
- 用 revealed-file index 校验 user/session/trace/key；拒绝 project/folder/raw URL/无归属 key。
- 按类型和大小预检，采用流式/spooled S3 下载，调用 SDK 上传/发送。
- 给额外文件与失败情况生成用户回执和 audit event。
- 测试 image/file、多个 reveal、超限、不支持类型、跨用户 key、SDK 无能力、上传/发送失败。

### E. 企微渠道侧栏聚合

- 普通项目过滤器只保留 `type=custom`，channel 项目不再平铺。
- 增加“企微渠道”虚拟父级，子级复用每机器人 channel project 的懒加载 session 列表。
- channel 机器人目录采用只读、紧凑、Lucide 线性图标样式；禁用编辑、删除、拖放和手工新会话。
- 增加项目过滤、层级渲染、i18n 和 Persona metadata 恢复测试。

## 预计风险文件

- `src/infra/agent/wecom/handler.py`
- `src/infra/agent/wecom/collector.py`
- `src/infra/agent/wecom/bot.py`
- `src/infra/agent/wecom/manager.py` / `status.py`
- `src/infra/task/manager.py` / `executor.py` / `arq_worker.py` / recovery paths
- Fast/Search/Team graph/nodes 和共享 prompt middleware
- `src/api/routes/persona_preset.py`
- persona 企微状态前端 hook/components/API/types
- `src/infra/tool/reveal_file_tool.py` 与 revealed-file storage

## 验证命令基线

```powershell
uv run ruff check src tests
uv run mypy src
uv run pytest tests/infra/agent/wecom tests/api/test_persona_wecom_status_routes.py -q
uv run pytest tests/infra/task tests/agents -q
cd frontend
npm run type-check
npm run lint
npm test
```

实施时按每个子任务收窄测试，再在最终集成门执行全量相关测试。必须增加 collector/segment/file-delivery 测试，不能继续只 mock collector。

## 真机验收矩阵

- 手机单聊两个数字人，交替提问验证上下文不串。
- 群聊 mention/open policy、两个 bot 同群、重复消息和快速连续消息。
- 1KB、2KB、8KB、含代码块/emoji 的回复，观察独立气泡、顺序和频控。
- reveal 一个文件、多个文件、图片、20MB 边界附近文件、失败回执。
- 入站 image/file/voice/mixed/video。
- 单节点重连、多节点 API 命中非 owner、错误 secret、owner 进程强杀和恢复。
- 同一用户从 Web/PC 再发消息，抓取最终 prompt 证明不含企微 section。

## 审阅门禁

1. 用户确认本规划，尤其是“单文件”“默认最多 6 段”“重连权限收紧”和跨渠道 session 预期。
2. 再决定是否创建 A/B/C/D 四个独立 Trellis 子任务。
3. 仅在选定的实现任务 artifacts 完整后运行 `task.py start`。
