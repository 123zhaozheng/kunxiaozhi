# 企业微信长连接与内网部署支持执行计划

## 1. 配置与持久化

- [ ] 新增 `WeComNetworkMode`、配置/更新/脱敏响应/operation schema，完成 URL、
  timeout、大小、CA 和模式相关校验。
- [ ] 新增单一 revision 的企业微信网络配置 storage/service，支持
  `current + last_known_good`、密码保留/清除和 CAS 回滚。
- [ ] 增加 `.env` 首次导入与默认 `direct` 兼容，确保配置不会出现在日志和普通
  settings export 中。
- [ ] 为并发保存增加 Redis 锁或 revision compare-and-set。

## 2. 企业微信网络适配层

- [ ] 新增 `WeComNetworkTransport`，集中构建 WSS URL、显式 proxy、SSLContext 和
  timeout。
- [ ] 将 manager/bot 启动改为读取部署级网络配置，不再从 Persona raw config 期待
  `websocket_url`。
- [ ] 实现受控的流式附件 downloader，覆盖 direct、reverse_gateway、
  forward_proxy；限制 HTTPS、大小、超时和日志脱敏。
- [ ] 复用/封装 AES 解密逻辑，保持 image/file/voice/mixed 的现有附件结构。
- [ ] 验证出站 `upload_media`、流式回复、主动回复继续复用同一 WSS transport。

## 3. 即时重连与回滚

- [ ] 增加网络配置 revision 广播和 operation 状态存储。
- [ ] manager 实现“只重启本节点 lease owner bots”，停止旧连接后按新 revision
  建立连接。
- [ ] WeCom status 增加 `network_revision`，忽略旧 revision 的迟到状态。
- [ ] API 聚合全部已启用 bot 的鉴权结果；全失败时 CAS 回滚并再次广播恢复。
- [ ] 部分失败保留新配置并返回逐 bot 脱敏结果；无 bot 时返回未鉴权验证状态。

## 4. 管理 API 与前端

- [ ] 新增管理员专用 get/test/update/operation API，权限统一为 `settings:manage`。
- [ ] 在 SettingsPanel 增加“企业微信网络”分类与专用配置组件。
- [ ] 根据 mode 条件显示字段，密码空白保持、显式清除，按钮文案为“保存并重连”。
- [ ] 展示测试、重连、成功、部分失败和已回滚状态；不得把持久化成功等同于连接成功。
- [ ] 补齐前后端类型与中英日韩俄 i18n。

## 5. 测试

- [ ] schema/storage：默认 direct、三模式校验、密码脱敏/保留/清除、revision/CAS。
- [ ] transport：WSS URL/proxy/CA 组合，HTTP direct/proxy/reverse URL 构造，超时、
  大小限制、签名 URL 日志脱敏。
- [ ] manager：单节点重启、多节点 lease owner 隔离、旧 revision 状态忽略。
- [ ] rollback：全失败回滚、部分失败保留、并发保存 409、无 bot 预检。
- [ ] inbound：image/file/voice/mixed 经新 downloader 后仍上传 S3 并传给 Agent。
- [ ] outbound：`reveal_file` 仍只发首个且必须通过 user/session/trace/key 校验，
  媒体上传走配置后的 WSS。
- [ ] frontend：模式字段、权限、密码语义、单次保存、operation 结果与回滚提示。

建议验证命令：

```powershell
uv run pytest tests/kernel/config tests/infra/agent/wecom tests/api -q
uv run ruff check src tests
uv run mypy src
cd frontend
pnpm test -- --run
pnpm lint
pnpm build
```

## 6. 文档与部署验证

- [ ] 更新 `.env.example`、README 和 Kubernetes 示例，加入三模式、CA mount 和
  secret 配置说明。
- [ ] 提供 WSS Upgrade、长超时、buffering、媒体目标白名单和大小限制的 Nginx
  示例；明确示例不是开放代理。
- [ ] 编写生产验收清单：WSS 鉴权/心跳、文字、企微上传图片/文件、Agent
  `reveal_file` 回传、错误配置自动回滚、Web 聊天不受影响。

## 7. 风险与回滚点

- 修改 SDK transport 前先保留 direct 模式回归测试；失败可切回旧构造路径。
- 配置 candidate 必须保留 last_known_good 后才能广播。
- 新附件 downloader 失败只降级附件，不得阻断文本消息处理。
- 不执行数据库破坏性迁移；删除新配置文档即可恢复默认 direct。
- 发布前进行一次真实 DMZ 环境 smoke test，mock 测试不能替代 TLS、Upgrade、
  动态附件 URL 和 AES 解密验证。

