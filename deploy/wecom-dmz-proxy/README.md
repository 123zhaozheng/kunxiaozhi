# 企业微信 DMZ HTTP CONNECT 正向代理

该代理用于 LambChat“系统设置 → 企业微信网络 → DMZ 正向代理”模式。
代理只建立 TCP 隧道，企业微信 TLS 连接仍是 LambChat 到
`openws.work.weixin.qq.com` 的端到端连接，因此代理本身不需要证书。

## 启动

```bash
cd deploy/wecom-dmz-proxy
docker compose up -d --build
docker compose ps
```

LambChat 直接运行在宿主机时填写：

- 模式：`DMZ 正向代理`
- 正向代理地址：`http://localhost:3128`
- 用户名：留空
- 密码：留空
- CA 证书路径：留空

先点击“测试网络”，成功后点击“保存并立即重连”。

如果 LambChat 也在 Docker 中，不能使用该容器自身的 `localhost`。
Windows/macOS Docker Desktop 可填写
`http://host.docker.internal:3128`。

## DMZ 部署

将该目录复制到 DMZ 主机后运行：

```bash
WECOM_PROXY_PORT=3128 docker compose up -d --build
```

在真实环境中，应在 `squid.conf` 中增加来源网段限制，并由防火墙仅允许
LambChat 服务器访问 DMZ 的 TCP 3128。当前目的地址白名单只放行
`openws.work.weixin.qq.com:443`，用于文字消息长连接测试。

企业微信入站图片、文件或语音的下载域名可能不同，当前会被白名单拒绝；
拿到实际回调域名后再经安全审核加入 `dstdomain` 白名单。
