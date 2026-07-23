# 企业微信官方协议与 SDK 调研

访问日期：2026-07-23。检索工具：Valyu Search/Contents；代码核验：本地安装的 `wecom-aibot-sdk 1.0.8`。官方网页抓取包含大量导航文本，因此只把官方搜索摘要、SDK 公共签名和源码能共同支持的事实作为硬结论。

## 官方长连接模式

- 官方入口：[智能机器人长连接](https://developer.work.weixin.qq.com/document/path/101463)。管理后台启用 API 模式并选择长连接；服务端连接 `wss://openws.work.weixin.qq.com`，以 bot id/secret 鉴权。
- 长连接同时承载消息/事件推送、被动回复和主动推送。当前项目使用官方/兼容 Python SDK 的 `reply`、`reply_stream`、`send_message`，方向正确。
- 官方资料说明单会话无论被动回复还是主动推送，合计限制为 30 条/分钟、1000 条/小时。任何“像人一样分段”都必须限制段数和节奏。
- [接收消息](https://developer.work.weixin.qq.com/document/path/100719) 说明同一机器人可并行处理有限数量的用户交互，流式交互最长等待约 6 分钟；当前 collector 的 360 秒超时与此一致。
- 长连接媒体 URL 使用每个 URL 独立 aeskey，AES-256-CBC/PKCS#7；当前项目曾修正 `url/aeskey` 字段并交给 SDK download/decrypt，仍需真机回归。

## 流式回复

SDK 公共签名：

```python
reply_stream(frame, stream_id, content, finish=False,
             msg_item=None, feedback=None)
```

SDK 将每次调用封装为 `msgtype=stream`，内容是该 stream 当前完整 `content`，不是增量 delta。`finish=true` 结束同一个气泡；多次 update 不等于多个独立消息。这正是当前“看起来没有分段”的协议层原因。

建议：第一个独立气泡可以使用被动 stream（保留快速首屏和 feedback）；后续语义段通过主动消息依次发送。若只需正确性，也可在发现需分段时结束占位 stream，再发送全部独立段。

## 主动回复与主动推送

- 官方回调模式还提供 [response_url 主动回复](https://developer.work.weixin.qq.com/document/path/101138)；当前项目使用长连接 `aibot_send_msg`，无需混用 response_url。
- 主动推送适合超时后的完整结果、分段后的后续气泡和媒体；必须复用 chatid 并遵守频控。

## 媒体上传与发送

官方 Node SDK 示例和 Python SDK 都采用两步业务语义：先上传素材取得 `media_id`，再被动回复或主动发送媒体。

Python SDK 1.0.8 实际为三阶段传输：

1. `upload_media_init`：类型、文件名、总大小、分片数和 MD5；
2. `upload_media_chunk`：每片最多 512KB，最多 100 片，失败重试 2 次；
3. `upload_media_finish`：返回 `media_id`。

随后调用：

```python
reply_media(frame, media_type, media_id)
send_media_message(chatid, media_type, media_id)
```

SDK 的协议上限约 50MB，但这不意味着所有媒体类型都可用到 50MB。目标实现仍应按企业微信官方媒体类型限制和格式做更保守预检；不能只依赖 SDK 最后报错。

## 对本项目的直接约束

- WebSocket 连接和 SDK 选型正确，不需要改回公网 callback 才能实现用户故事。
- stream 更新必须与独立消息分段分开设计。
- 文件发送需要 upload → media_id → reply/send，不是把本地 URL 文本发给用户。
- 30 条/分钟意味着自动分段不能无限细；建议默认 1.2–1.8KB UTF-8 目标、最多 6 段，并预留附件/错误消息额度。
- 真实媒体限制、客户端渲染和多段节奏必须通过企微测试账号确认。

