# PicoClaw / OpenClaw 企业微信实现对比

访问日期：2026-07-23。仓库通过 Valyu 定位，由 DeepWiki 做仓库级问答；DeepWiki 结论是二手代码解释，涉及安全边界时必须在实施前复核源码和测试，不能替代官方协议。

## 对比对象

- [sipeed/picoclaw](https://github.com/sipeed/picoclaw)，[企微渠道文档](https://github.com/sipeed/picoclaw/blob/main/docs/channels/wecom/README.zh.md)
- [WecomTeam/wecom-openclaw-plugin](https://github.com/WecomTeam/wecom-openclaw-plugin)（企业微信团队维护）
- [sunnoy/openclaw-plugin-wecom](https://github.com/sunnoy/openclaw-plugin-wecom)（社区增强）

## 路由与隔离

PicoClaw 将 `req_id/chat_id/chat_type/stream_id` 保存为每次 turn 的上下文，并通过 session dimensions/dispatch rule 隔离会话。官方 OpenClaw 插件生成确定性 `wecom-{accountId}-{type}-{peerId}` agent/session key；社区插件类似生成 DM/group 独立 agent，并用 dispatch lock 串行同一会话。

对 LambChat 的启示：不必为每个企微用户复制 persona/agent workspace，但 session key 至少必须包含 `aibotid + chat_type + chat_id`。当前仅 chat_id 明显弱于这些实现。

## 渠道上下文

OpenClaw 插件构造每条消息的 ctx payload，携带 account、chat type、sender、session、attachments、quoted message 和授权状态，再交给统一 reply dispatcher。它不是把“你正在企微交流”永久写进 agent persona。

对 LambChat 的启示：采用 run-scoped `ChannelRunContext`，由共享 prompt middleware 注入，Web 不传企微字段即可移除。这个模式比拼接 persona prompt 更干净，也更容易测试跨渠道不污染。

## 流式与长文本

三者都利用企微 stream 先显示 thinking/内容并在过期时主动推送。DeepWiki 明确指出 PicoClaw 的正常 stream 也不自动拆成多个消息；这与 LambChat 当前问题相同，说明“用了 stream”本身不能解决人类化分段。

对 LambChat 的启示：需要产品层 segmenter + delivery queue，而不能照搬单 stream 更新。

## 附件

PicoClaw 的入站媒体会下载/解密并存入 media store；出站 `SendMedia` 解析 http(s)、media 和 local refs，分片上传后被动或主动发送。其代码包含按媒体类型大小限制，DeepWiki 报告 file 20MB、image/voice 2MB、video 10MB；实施前须以当前官方文档复核。

官方 OpenClaw 插件采用 `mediaLocalRoots` 和默认 state/workspace/sandbox roots 校验本地媒体，再逐个上传发送；社区增强插件也暴露 `mediaLocalRoots`，并对图片格式/大小做处理。相比之下，LambChat 当前 collector 只信任 S3 key，未验证 key 的用户/trace 归属。

对 LambChat 的启示：LambChat 已经有更合适的 reveal + storage index，不应重新开放本地路径。应把 `revealed_files` 中的 user/session/trace 归属作为 allowlist，并在 collector 强制只选一个文件。

## 值得采用与不应照搬

建议采用：

- account/bot + chat type + peer 的确定性 session key；
- 每消息结构化 channel context；
- 同一会话 delivery lock/queue；
- thinking stream + 主动消息/媒体 fallback；
- allowlist、大小/格式预检和用户可见降级。

不建议照搬：

- 为每个用户动态创建持久化 agent/persona；LambChat 的 persona 与用户会话已经解耦。
- 允许模型直接提供任意绝对本地路径；LambChat 应只接受已登记 reveal 产物。
- 把 stream update 当作“自动分段”；它仍是一个气泡。
- 自动发送多个 project/folder 文件；本需求明确只交付单个 reveal 文件。

