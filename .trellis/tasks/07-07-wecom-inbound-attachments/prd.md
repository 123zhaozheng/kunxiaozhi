# WeCom inbound attachments download and attachment passthrough

## Goal

企业微信渠道入站的图片/文件/语音/混合消息，当前只把 `[image]`/`[file: xxx]`/`[voice]`/`[video]` 等占位符作为消息文本传给 agent，模型完全看不到图片内容、读不到文件。本次任务让 WeCom 入站附件走与 Web 端一致的处理链路：下载媒体 → 落 S3 → 构造标准 attachment → 通过 `task_manager.submit(attachments=...)` 透传给 agent，使模型能真正看到图片（vision），文件类附件与 Web 端 document 行为一致（摘要 + URL）。

## Background

### 当前处理逻辑（根因）

- `_on_image_message`（`src/infra/agent/wecom/bot.py:417-466`）：`content` 硬编码为 `"[image]"`，`pic_url`/`aes_key` 塞进 metadata，下游无人消费。
- `_on_file_message`（`src/infra/agent/wecom/bot.py:468-521`）：`content` 为 `"[file: {file_name}]"`，`file_url`/`file_name`/`aes_key` 塞 metadata，下游无人消费。
- `_on_voice_message`（`src/infra/agent/wecom/bot.py:523-576`）：WeCom 自动转写文本优先做 content，否则 `[voice]`；`voice_url`/`aes_key` 塞 metadata，下游无人消费。
- `_on_video_message`（`src/infra/agent/wecom/bot.py:578-620`）：`content` 硬编码 `"[video]"`，连 `video_url` 都不提取。
- `_on_mixed_message`（`src/infra/agent/wecom/bot.py:622-684`）：文本子项拼进 content，图片子项用 `[image]` 占位，不提取子项 URL。
- `wecom_message_handler`（`src/infra/agent/wecom/handler.py:289-583`）：调 `task_manager.submit(...)` 时**只传 `message=content`，不传 `attachments=`**（handler.py:517-531）。
- `WeComBot.download_media_file`（`src/infra/agent/wecom/bot.py:1157-1182`）：已实现下载 + AES 解密，但**全仓无人调用**。

### Web 端对齐契约（研究确认）

Web 端上传附件流程（`src/api/routes/upload.py:388-552`）：分类 → S3 上传 → 写 file_record → 返回 `{key, url, name, type, mime_type, size}`。

AttachmentSchema（`src/kernel/schemas/agent.py:14-25`）所有字段必填：`id`/`key`/`name`/`type`/`mime_type`/`size`/`url`。

agent 链路消费（`src/agents/core/node_utils.py:152-309`）：
- `inline_image_attachments_as_data_urls`：image attachment 有 `key` 即可，按需生成 `url` 或 `data_url`。
- `build_human_message`：image + vision → 多模态 `image_url` block；非 image → `_format_attachment_summary` 生成文本摘要（文件名+类型+URL），**不读文件内容**。

S3 上传方法（`src/infra/storage/s3/service.py`）：`upload_stream_to_key`（指定 key）/`upload_bytes`（自动 key）/`upload_to_key`（bytes 到指定 key）。

WeComBotManager 访问 bot 实例：`find_bot(aibotid)`（`src/infra/agent/wecom/manager.py:268-270`）。

## Requirements

### R1 image 消息：下载落 S3 + attachment 透传
- `_on_image_message` 收到图片消息后，下载 `pic_url`（用 `aes_key` 解密）→ 上传 S3 → 构造 image attachment（含 `key`/`name`/`type=image`/`mime_type`/`size`/`url`）→ 透传给 `task_manager.submit(attachments=...)`。
- 模型支持 vision 时能真正看到图片内容。

### R2 file 消息：下载落 S3 + attachment 透传（不读内容）
- `_on_file_message` 收到文件消息后，下载 `file_url`（用 `aes_key` 解密）→ 上传 S3 → 构造 document attachment → 透传给 `task_manager.submit(attachments=...)`。
- 模型看到文件名/类型/URL 摘要，与 Web 端 document 附件行为一致。**不新增文件内容解析逻辑**（PDF/文本模型仍只看 URL，本次接受该限制）。

### R3 voice 消息：转写优先，无转写才下载
- 有 WeCom 自动转写文本：用转写文本做 content，**不下载**语音。
- 无转写文本：下载 `voice_url`（用 `aes_key` 解密）→ 上传 S3 → 构造 audio attachment → 透传。

### R4 video 消息：不动
- 维持现状（`[video]` 占位），不下载、不落 S3、不构造 attachment。

### R5 mixed 消息：文本拼接 + 图片/文件子项下载
- 文本子项拼进 content（现状）。
- 图片子项：下载落 S3 → image attachment。
- 文件子项：按 R2 规则下载落 S3 → document attachment。
- 多个子项的 attachments 合并为一个 list 透传。

### R6 handler 透传 attachments
- `wecom_message_handler` 把构造好的 attachments 传给 `task_manager.submit(attachments=...)`（handler.py:517-531 当前缺失）。

### R7 失败降级
- 媒体下载失败、S3 上传失败、构造 attachment 异常时：降级为现有占位符行为（`[image]`/`[file: xxx]` 等），不阻断消息处理流程，记录日志。

## Acceptance Criteria

- [ ] image 消息：模型支持 vision 时，agent 能描述图片内容（端到端验证：发图片 → agent 回复识别出图片内容，而非"我收到一张图片"）。
- [ ] file 消息：文件名/类型/URL 出现在 agent 消息摘要中；S3 中存在对应文件；file_record 记录写入。
- [ ] voice 消息（有转写）：行为与现状一致（转写文本做 content），不下载语音。
- [ ] voice 消息（无转写）：下载语音落 S3，audio attachment 透传。
- [ ] video 消息：行为不变（`[video]` 占位）。
- [ ] mixed 消息：文本拼进 content，图片/文件子项生成对应 attachment 透传。
- [ ] 媒体下载/S3 上传失败时：降级为占位符，消息不中断，有 error 日志。
- [ ] 现有 WeCom 测试通过；新增测试覆盖 image/file/voice(无转写)/mixed 的下载→S3→attachment 链路。

## Out of Scope

- video 消息处理（维持占位）。
- PDF/文本类文件内容解析（模型仍只看 URL 摘要，与 Web 端 document 行为一致）。
- 文件类型限制（"仅图片+PDF+文本类"原需求降级为"对齐 Web 行为"，不区分文件类型）。
- WeCom 出站（回复）侧的附件处理——本次只改入站。

## Technical Notes

- 复用 `WeComBot.download_media_file(url, aes_key)`（bot.py:1157）下载，返回 `(bytes, md5|None)`。
- 复用 S3 storage（`src/infra/storage/s3/service.py`）上传 bytes。
- 复用 Web 端 attachment 字段结构（`{id, key, name, type, mime_type, size, url}`）。
- `WeComBotManager.find_bot(aibotid)`（manager.py:268）获取 bot 实例调 `download_media_file`。
- WeCom 媒体 URL 有时效（约 3 天），下载须在消息处理时立即进行，不能延迟。
- mime_type 判定：图片按扩展名/默认 `image/jpeg`；文件按 `file_name` 扩展名映射；语音按 `audio/amr` 或类似。
- 下载应在 handler 层做（不在 bot 回调线程阻塞 SDK），或 bot 层做异步下载后把 bytes 放 metadata——design.md 定。
