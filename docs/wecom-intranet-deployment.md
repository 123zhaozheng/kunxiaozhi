# 企业微信内网与 DMZ 网关部署

LambChat 的企业微信网络配置位于管理员的“系统设置 → 企业微信网络”，它只影响企业微信长连接和企业微信入站附件下载，不会改写进程级 `HTTP_PROXY`，也不会影响 LLM、S3、MCP 等其他请求。

## 连接模式

| 模式 | LambChat 配置 | DMZ 能力 | 适用场景 |
| --- | --- | --- | --- |
| 直接访问 | `direct` | 无 | 服务可直接访问企业微信 |
| DMZ 反向网关 | `reverse_gateway` | 提供一个 WSS 入口和一个 HTTPS 附件入口 | 内网服务只能访问固定的 DMZ 地址 |
| DMZ 正向代理 | `forward_proxy` | 支持 HTTP CONNECT | DMZ 允许按目标域名代理外网 |

银行内网只允许访问 DMZ Nginx 地址时，应选择“DMZ 反向网关”，并填写：

- 长连接网关地址，例如 `wss://dmz.example.bank/wecom/ws`
- 附件下载网关地址，例如 `https://dmz.example.bank/wecom/media`
- 若 DMZ 使用银行私有 CA，填写容器内 CA 文件绝对路径

不需要在每个机器人里配置网关地址。机器人页面仍只维护 `aibotid`、`secret` 等机器人级凭证。

## 请求链路

```text
LambChat / wecom-runtime
  ├─ WSS 会话、消息、Agent 文件分片上传
  │    └─ DMZ WSS 网关 ──> openws.work.weixin.qq.com
  └─ 企业微信入站附件下载
       └─ DMZ HTTPS 媒体网关 ──> 企业微信回调中的 HTTPS 下载地址
```

Agent 的 `reveal_file` 回传会继续执行现有的单文件、所有权和大小校验；文件内容通过已经建立的企业微信 WebSocket 分片上传，因此自然复用同一条 WSS 网关或正向代理连接。

## DMZ Nginx 的 WSS 参考配置

下面是长连接入口的最小示例。生产环境应使用银行签发的服务端证书，并由网络团队配置 DNS、审计和访问控制。

```nginx
location = /wecom/ws {
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host openws.work.weixin.qq.com;

    proxy_ssl_server_name on;
    proxy_ssl_name openws.work.weixin.qq.com;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    proxy_pass https://openws.work.weixin.qq.com;
}
```

不要在此路径启用响应缓存、请求体改写或短连接超时。DMZ 到企业微信必须放通 TCP 443 和 DNS。

## 附件媒体网关约定

LambChat 会这样调用媒体网关：

```http
GET /wecom/media?target=<URL-encoded 企业微信 HTTPS 下载地址>
```

媒体网关需要：

1. URL 解码 `target`，只接受 `https`。
2. 按企业微信实际返回的媒体域名建立严格 allowlist；拒绝 IP 字面量、内网地址、回环地址和非标准端口。
3. 每次重定向后重新执行相同校验，限制重定向次数。
4. 不记录完整 `target` 查询串，因为其中可能含短期下载凭证。
5. 流式转发响应，保留 `Content-Length` 和 `Content-Disposition`，并限制响应体大小。
6. 仅允许 LambChat 所在网段或工作负载身份访问该入口。

标准 Nginx 变量形式的任意 `proxy_pass $arg_target` 容易形成 SSRF，不能直接用于生产。建议网络团队使用带 URL 校验逻辑的网关模块、OpenResty/njs 服务或已有的银行出网网关实现上述约束。

如果 DMZ 已提供合规的 HTTP CONNECT 正向代理，优先选择“DMZ 正向代理”，无需建设动态媒体反向入口。

## 管理员保存行为

“测试网络”只探测 WSS/TLS，不保存配置，也不会发送机器人凭证。

“保存并立即重连”会：

1. 原子保存候选配置。
2. 只重启企业微信连接，不重启 Web API 或整个应用。
3. 等待所有已配置机器人的认证结果。
4. 全部成功时确认配置；部分失败时保留新配置并逐个显示失败机器人；全部失败时自动恢复上一版配置并再次重连。

如果当前没有企业微信机器人，配置会标记为“已保存、未验证”。

## 容器和 Kubernetes

环境变量仅用于 MongoDB 尚无管理员配置时的初始默认值：

```dotenv
WECOM_NETWORK_MODE=reverse_gateway
WECOM_WEBSOCKET_URL=wss://dmz.example.bank/wecom/ws
WECOM_MEDIA_GATEWAY_URL=https://dmz.example.bank/wecom/media
WECOM_CA_BUNDLE_PATH=/etc/lambchat/certs/bank-ca.pem
```

使用正向代理时：

```dotenv
WECOM_NETWORK_MODE=forward_proxy
WECOM_FORWARD_PROXY_URL=http://dmz-proxy.example.bank:3128
WECOM_FORWARD_PROXY_USERNAME=lambchat
WECOM_FORWARD_PROXY_PASSWORD=replace-with-secret
WECOM_CA_BUNDLE_PATH=/etc/lambchat/certs/bank-ca.pem
```

外置企业微信运行时（`WECOM_RUNTIME_MODE=external`）部署时，Web API 和 `wecom-runtime` 必须：

- 连接同一个 MongoDB 和 Redis；
- 都能访问 DMZ 地址；
- 都挂载相同路径的 CA 文件。Web API 用它执行“测试网络”，运行时用它建立实际连接；
- 配置相同且稳定的 `MCP_ENCRYPTION_SALT`，代理密码会用它派生的密钥加密后存入 MongoDB；
- 仅把初始代理密码和加密盐放入 Secret，不放入 ConfigMap、镜像或日志。

Kubernetes 挂载示例：

```yaml
volumes:
  - name: wecom-bank-ca
    secret:
      secretName: wecom-bank-ca
containers:
  - name: kunxiaozhi
    volumeMounts:
      - name: wecom-bank-ca
        mountPath: /etc/lambchat/certs
        readOnly: true
  - name: wecom-runtime
    volumeMounts:
      - name: wecom-bank-ca
        mountPath: /etc/lambchat/certs
        readOnly: true
```

## 验收清单

- 管理员“测试网络”成功，证书校验未被关闭。
- 保存后 Web API 进程未重启，企业微信连接立即变为已连接。
- 企业微信发送图片、文件和语音后，LambChat 会话中附件可正常读取。
- Agent 使用 `reveal_file` 返回一个文件时，企业微信收到对应附件。
- 人为配置一个错误机器人凭证时，新网络配置被保留且页面列出该机器人。
- 让全部机器人失败时，页面显示已回滚，上一版连接自动恢复。
