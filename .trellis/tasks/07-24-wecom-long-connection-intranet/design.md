# 企业微信长连接与内网部署支持设计

## 1. 设计目标

通过一套部署级、管理员可维护的企业微信网络配置，使同一套代码支持：

- `direct`：直接访问企业微信公网；
- `reverse_gateway`：WSS 和附件 HTTPS 通过 DMZ 反向网关；
- `forward_proxy`：WSS 和附件 HTTPS 通过显式 HTTP(S) CONNECT 代理。

保存配置后只重建企业微信连接，不重启 Web/API 服务。所有已启用机器人均无法
鉴权时，自动回滚上一版有效配置。

## 2. 配置契约

定义 `WeComNetworkConfig`，由专用 API 原子读写：

```python
class WeComNetworkMode(str, Enum):
    DIRECT = "direct"
    REVERSE_GATEWAY = "reverse_gateway"
    FORWARD_PROXY = "forward_proxy"

class WeComNetworkConfig(BaseModel):
    mode: WeComNetworkMode = WeComNetworkMode.DIRECT
    websocket_url: str = "wss://openws.work.weixin.qq.com"
    media_gateway_url: str = ""
    forward_proxy_url: str = ""
    forward_proxy_username: str = ""
    forward_proxy_password: SecretStr | None = None
    ca_bundle_path: str = ""
    connect_timeout_seconds: int = 10
    media_download_timeout_seconds: int = 30
    media_max_bytes: int = 20 * 1024 * 1024
```

API 响应不返回密码，只返回 `has_forward_proxy_password`。更新请求中：

- 密码字段缺失或空字符串：保留旧密码；
- `clear_forward_proxy_password=true`：显式清除；
- URL 中禁止内嵌用户名/密码，凭证必须使用独立字段。

模式校验：

- `direct` 强制使用官方 WSS，忽略网关/代理字段；
- `reverse_gateway` 要求合法 `wss://` 网关和 HTTPS 媒体网关；
- `forward_proxy` 保持官方企业微信业务 URL，要求合法 `http://` 或 `https://`
  代理地址；
- TLS 验证始终开启，`ca_bundle_path` 必须指向所有运行节点均已挂载的可读文件。

配置作为单一 revision 保存。保留 `current` 与 `last_known_good`，并记录
`revision`、`updated_by`、`updated_at`，不记录明文密码到日志或审计响应。

## 3. 页面与 API

在系统设置中新增“企业微信网络”分类和专用 `WeComNetworkSettings` 组件。只有
`settings:manage` 用户可编辑。

页面行为：

1. 选择三种模式，只显示相关字段。
2. 密码框空白表示不修改；旁边显示“已配置/未配置”。
3. 点击一次“保存并重连”提交整个对象。
4. 展示 `validating → reconnecting → connected / rolled_back / partial_failure`。
5. 提供“测试配置”操作；测试不持久化、不切换正式连接。

建议 API：

```text
GET  /api/settings/wecom-network
POST /api/settings/wecom-network/test
PUT  /api/settings/wecom-network
GET  /api/settings/wecom-network/operations/{operation_id}
```

`PUT` 在重连仍进行时返回 operation id；若能在短等待窗口完成，也同时返回终态。
并发保存由 Redis 锁/CAS revision 拒绝为 HTTP 409，避免较晚的回滚覆盖较新的配置。

## 4. 网络适配层

新增 `WeComNetworkTransport`，成为 WSS 与附件 HTTP 的唯一配置解析点：

```text
WeComNetworkConfig
   ├─ build_ws_client_options() -> ws_url, proxy, SSLContext, timeout
   └─ download_media(callback_url) -> bytes/tempfile + filename
```

### 4.1 WebSocket

- `direct`：官方 WSS，无 proxy。
- `reverse_gateway`：连接管理员配置的 WSS URL。
- `forward_proxy`：连接官方 WSS，通过 `ws_options["proxy"]` 和 SSLContext 使用
  显式代理。
- 心跳、鉴权、回复、主动消息和 `upload_media` 均继续使用现有 SDK。

不得通过修改 `HTTP_PROXY/HTTPS_PROXY` 进程环境实现，避免污染其他客户端。

### 4.2 入站附件下载

不再依赖 SDK 内部不可配置的 `httpx.AsyncClient`。项目网络适配层负责：

1. 只接受消息回调中提取出的 HTTPS URL；
2. `direct` 直接下载；
3. `forward_proxy` 使用显式 `httpx.AsyncClient(proxy=..., verify=SSLContext)`；
4. `reverse_gateway` 把原 URL 进行 URL-safe 编码，提交给配置的媒体网关端点；
5. 流式读取并在 `media_max_bytes` 处中止；
6. 使用现有 SDK兼容 AES 解密逻辑或项目封装的等价 helper；
7. 返回给现有 S3/file-record/attachment 构造链路。

反向媒体网关协议固定为：

```text
GET {media_gateway_url}?target=<percent-encoded-original-https-url>
```

DMZ Nginx/网关必须再次校验目标 host 白名单，禁止通用开放代理；只允许 GET/HEAD，
拒绝非 HTTPS、私网/回环目标和非企业微信媒体域名。若银行网关采用固定 path 映射，
可以在部署文档中提供等价适配示例，但应用契约保持明确。

### 4.3 出站文件

保持现有安全链路：

```text
reveal_file event
  → 首个候选
  → current user/session/trace/key 归属校验
  → 内部存储临时文件
  → WSS upload_media
  → reply_media / send_media_message
```

网络配置只改变 WSS transport，不新增企微工具，不放宽文件归属或数量限制。

## 5. 保存、重连与回滚状态机

```text
管理员保存
  → 校验 + 获取 distributed lock
  → 保存 candidate revision（保留 last_known_good）
  → Redis 广播 revision
  → 各节点 lease owner 停止并重建自己持有的 bots
  → status 写入 network_revision + authenticated/failed
  → 聚合结果
       ├─ 至少一个成功 / 部分失败：保留新配置，报告失败 bots
       ├─ 全部成功：标记 last_known_good
       └─ 所有已启用 bot 均失败：CAS 回滚旧 revision，广播并恢复旧连接
```

没有已启用机器人时，保存只做结构、DNS/TCP/TLS/WSS 握手预检，标记配置已保存但
“尚未用机器人鉴权验证”。

重连期间 Web/API 和 Web 聊天不受影响。旧连接先停再建，避免企业微信
`disconnected_event` 互踢；状态需带 revision，防止旧连接迟到事件覆盖新状态。

## 6. 错误与可观测性

统一 reason code：

- `invalid_config`
- `proxy_connect_failed`
- `gateway_connect_failed`
- `tls_verify_failed`
- `ws_auth_failed`
- `media_download_failed`
- `media_too_large`
- `media_decrypt_failed`
- `rolled_back`
- `partial_failure`

日志允许记录 mode、脱敏 host、preset/aibotid hash、revision、耗时和 reason code；
禁止记录 secret、代理密码、完整签名 URL、文件内容。

## 7. 兼容与发布

- 默认 `direct`，旧部署行为不变。
- 现有 Persona 的 bot id/secret 与回复设置不迁移。
- `.env` 可作为首次启动/灾备配置来源，但管理员页面保存值优先。
- 提供 Kubernetes Secret/ConfigMap、CA volume mount 和 Nginx WSS/media 示例。
- 可通过把 mode 改回 `direct` 手工回退；自动回滚只回滚最近一次失败的 candidate。

## 8. 关键取舍

- 使用专用聚合设置 UI/API，而不是通用敏感 JSON：保证原子保存和密码保留语义。
- 使用项目自有附件 downloader，而不是改 SDK 私有字段：代理、CA、大小和测试边界
  均可控。
- 代理只作用于企业微信客户端，而不是全局环境变量：隔离 Web、存储和模型流量。
- 部分机器人成功时不自动回滚：避免一个失效 bot secret 阻断其他正常机器人；
  页面明确列出失败项。

