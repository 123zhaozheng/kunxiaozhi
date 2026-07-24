# 企业微信内网网络与附件链路调研

调研日期：2026-07-24。

## 结论

企业微信智能机器人长连接不是单一网络请求：

1. 消息接收、被动回复、主动回复和媒体上传协议帧走 WebSocket，默认地址为 `wss://openws.work.weixin.qq.com`。
2. 用户发送的图片、文件等只在 WebSocket 回调中携带动态 HTTPS URL 和 AES key；应用还要发起独立 HTTP 请求下载并解密。
3. Agent 的 `reveal_file` 结果先从 LambChat 内部存储取回，再通过 WebSocket 媒体上传协议获得 `media_id` 并发送。

因此只替换 WebSocket URL 无法保证入站附件正常，必须把 WSS 与 HTTPS 下载作为同一企业微信网络策略管理。出站文件上传无需额外 HTTP 企业微信 API，但依赖 WSS 网关完整透传媒体上传帧和较大消息。

## 官方与 SDK 证据

- 企业微信长连接官方入口：<https://developer.work.weixin.qq.com/document/path/101463>
- 企业微信团队 Python SDK：<https://github.com/WecomTeam/wecom-aibot-python-sdk>
  - 文档公开 `ws_url` 自定义 WebSocket 地址。
  - `download_file(url, aes_key)` 明确使用回调 URL 下载并解密附件。
- 企业微信团队 OpenClaw 插件：<https://github.com/WecomTeam/wecom-openclaw-plugin>
  - 同时公开 `websocketUrl` 与 `network.egressProxyUrl`。
  - 代理优先级覆盖显式配置和 `HTTPS_PROXY` 等环境变量。
  - 官方插件媒体限制参考值：图片 10 MB、文件 20 MB。

## 当前项目证据

### 长连接

- `src/infra/agent/wecom/bot.py`
  - `WeComBot.__init__` 已有 `websocket_url`。
  - `start()` 将其传给 `WSClient(ws_url=...)`。
- `src/infra/agent/wecom/manager.py`
  - `_start_bot()` 会读取 raw config 的 `websocket_url`。
- `src/kernel/schemas/wecom.py`、`src/infra/agent/config_storage.py`
  - Persona schema、API view 和 raw config 均未定义/保存 `websocket_url`。

结果：底层预留参数目前没有可用的管理员配置入口，正常运行仍回退官方域名。

### SDK 版本与代理能力

- `pyproject.toml` 使用 `wecom-aibot-sdk>=1.0.7`。
- `uv.lock` 当前锁定社区增强包 `wecom-aibot-sdk==1.0.8`，来源为
  `xiaowangzhixiao/wecom-aibot-python-sdk`，不是企业微信团队同名官方包。
- 当前包的 `WSClient` 接受 `ws_url` 和 `ws_options`，后者透传给
  `websockets.asyncio.client.connect`。
- 当前包的附件 HTTP 下载使用内部 `httpx.AsyncClient`，没有项目级显式代理或
  CA 配置注入点。

结果：可以通过 `ws_options` 为 WSS 显式传代理和 TLS 参数；附件下载应由
LambChat 自己的受控网络适配器负责，不能只依赖 SDK 内部 HTTP client。

### 入站附件

- `src/infra/agent/wecom/handler.py`
  - `_build_wecom_attachments()` 覆盖 image、file、voice、mixed。
  - `_download_and_upload_media()` 经 bot 下载/解密，再上传内部 S3。
- 现有自动化测试主要覆盖解析和降级，未覆盖 DMZ 网关、显式代理、CA 和超时。

### 出站 `reveal_file`

- `src/infra/agent/wecom/collector.py`
  - 每轮最多保留第一个 reveal 文件。
  - 发送前校验 `user_id + session_id + trace_id + file_key` 归属。
  - 从内部存储下载后调用 `upload_and_send_media()`。
- `src/infra/agent/wecom/bot.py`
  - `upload_media → media_id → reply_media/send_media_message` 全部复用 WSS。

结果：安全和单文件产品边界已经实现，本任务重点是保证新网络传输配置不破坏它，并补充真实边界测试与运维说明。

## 设置系统约束

- 现有 `/api/settings` 写操作由 `settings:manage` 权限保护。
- 配置按 MongoDB > 环境变量 > 默认值加载，并通过 Redis pub/sub 通知多实例。
- `is_sensitive=True` 会把整个值替换为字符串 `********`。
- 通用 JSON 编辑器不支持“只掩码对象中的一个字段并在空值时保留旧密码”。

因此企业微信网络设置需要专用的聚合读写接口/组件，或批量设置服务：

- 公共字段正常回显；
- 密码只返回 `has_proxy_password`；
- 密码为空表示保留，显式清除需单独标志；
- 一次保存产生一个 revision、一次广播和一次机器人重启。

## 安全边界

- 代理只注入企业微信专用 WSS/HTTP client，不修改进程全局代理环境，避免 S3、
  MongoDB、Redis、模型服务误走 DMZ。
- TLS 校验默认开启；行内 CA 用挂载路径或 CA bundle 配置。
- 反向附件网关不得成为任意 URL 开放代理。LambChat 只转发企业微信回调提供的
  HTTPS URL；DMZ 侧还必须限制允许的企业微信媒体域名、端口和方法。
- 日志不得输出 bot secret、代理密码或带签名参数的完整附件 URL。

