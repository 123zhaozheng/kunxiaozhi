# 辅助 vision 模型预处理注入（方案 B）

## Goal

主对话模型不支持 vision（如 `deepseek-v4-flash`，`profile.supports_vision=false`）时，入站图片附件当前被降级为纯 URL 文本摘要，主模型看不到图片内容，还会把 URL 当文件路径用工具去读——全部失败。

本次任务实现"主模型 + 辅助 vision 模型"架构：主模型不支持 vision 且启用辅助模型时，在消息进主模型之前，自动用辅助 vision 模型描述每张图片，把描述文本注入用户消息。主模型无感，只看到文本。主模型支持 vision 时直通 `image_url` block，零改动。

## Background

### 研究依据（已持久化）

- `.trellis/tasks/07-07-wecom-inbound-attachments/research/auxiliary-model-patterns.md`：hermes（`NousResearch/hermes-agent`）、kivio、SillyTavern、Odysseus 均采用"预处理注入"模式——主模型不支持 vision 时，在消息预处理阶段用辅助 vision 模型描述图片，替换/注入 image part。
- `.trellis/tasks/07-07-wecom-inbound-attachments/research/lambchat-attachment-architecture.md`：LambChat 已具备辅助模型基础设施——`ModelConfig.kind`（chat/embedding/rerank/transcribe）、`LLMClient.get_model(model_id=...)`、`audio_transcribe_tool`（辅助模型工具范式）、`supports_vision` 直通路径。
- `.trellis/tasks/07-07-wecom-inbound-attachments/research/auxiliary-model-adaptation-proposal.md`：方案 B（预处理注入）vs 方案 A（工具模式）对比，推荐方案 B。

### 关键现状（file:line）

- `src/agents/core/node_utils.py:260-310`（`build_human_message`）：`supports_vision=False` 时 image attachment 走 `text_summary_attachments`，`_format_attachment_summary`（`node_utils.py:223-257`）只拼文件名+类型+URL，模型看不到图。
- `src/agents/search_agent/nodes.py:311-316`、`src/agents/fast_agent/nodes.py:278-283`、`src/agents/team_agent/nodes.py:546-551`：三节点附件处理代码重复，`if supports_vision:` 调 `inline_image_attachments_as_data_urls`，无 `else` 分支。
- `src/infra/tool/audio_transcribe_tool.py`：辅助模型工具范式——用 `settings.AUDIO_TRANSCRIPTION_MODEL_ID`（kind=transcribe 卡片 ID）解析辅助模型。
- `src/kernel/config/definitions.py` + `base.py` + `src/kernel/schemas/setting.py`：settings 注册范式（`SettingCategory`）。
- `src/infra/llm/client.py:588-631`（`LLMClient.get_card_config`）/ `get_model`：按 model_id 解析 chat 模型实例。
- `src/kernel/schemas/model.py:39-43`：`ModelConfig.kind` 已支持 `chat`/`embedding`/`rerank`/`transcribe`。
- `src/agents/core/node_utils.py:134-149`（`_download_image_as_data_url`）：storage 下载 + base64 编码底层函数，vision assist 复用。

### 不复用 `fallback_model` 字段的原因

`fallback_model` 语义是"主模型失败时整个对话切到 fallback chat 模型"（retry middleware），会接管所有推理。辅助 vision 模型只做单步图片描述，不接管对话。语义不同，复用会混淆。与 `audio_transcribe` 对称，用 settings 独立配置。

### 不走 URL fetch、始终 base64 的原因（关键架构决策）

部署环境是内网 + k8s 存储，图片在内部存储（MinIO/PVC），vision 模型是外部 API（mixroute/OpenAI/Claude），**fetch 不到内部 URL**。当前 `inline_image_attachments_as_data_urls`（`node_utils.py:152`）优先 URL（有 `url` 字段或 `base_url` 就拼 URL），在内网环境对外部 vision 模型是死的。

vision assist 路径**始终从 storage 按 key 下载 → base64 编码 → data_url 喂给辅助模型**，不依赖 URL fetch、不依赖 `APP_BASE_URL`、不依赖图片是否公开可访问。

## Requirements

### R1 核心预处理注入（始终 base64）

- 主模型 `supports_vision=False` 且 `settings.ENABLE_VISION_ASSIST=true` 且 `settings.VISION_ASSIST_MODEL_ID` 非空时，在 `build_human_message` 之前，对每个 image attachment：
  1. 按 `key` 从 storage 下载图片 → base64 编码为 data_url（**不走 URL fetch**）。
  2. 预检 `attachment.size`，超 `VISION_ASSIST_MAX_BYTES` 直接跳过（降级）。
  3. 调辅助 vision 模型（`LLMClient.get_model(model_id=VISION_ASSIST_MODEL_ID)`），用硬编码 prompt 生成描述文本。
  4. 描述塞回 `attachment["vision_description"]`。
- 描述文本以结构化方式注入用户消息：`[图片附件: {name}]\n<vision_description>\n{描述}\n</vision_description>\n[附件链接: {url}]`。
- 多张图片并发描述（`asyncio.gather`）。

### R2 直通路径零改动

- 主模型 `supports_vision=True` 时，完全跳过辅助模型，走原 `image_url` block 路径（`inline_image_attachments_as_data_urls` + `build_human_message` 的 vision 分支）——零改动、零风险。
- `ENABLE_VISION_ASSIST=false` 或 `VISION_ASSIST_MODEL_ID=""` 时，行为同现状（文本摘要）。

### R3 失败降级

- 辅助 vision 模型调用失败（网络/模型错误/超时）、返回空描述、图片超限、下载失败时：该图片退回当前文本摘要路径（纯 URL），不阻断消息处理，记 warning 日志。
- 非图片附件不受影响。

### R4 配置层（复刻 audio_transcribe 范式）

- 新增 settings：`ENABLE_VISION_ASSIST`（bool，默认 false）、`VISION_ASSIST_MODEL_ID`（string，默认 ""）、`VISION_ASSIST_MAX_BYTES`（number，默认 10MB）。
- 新增 `SettingCategory.VISION_ASSIST`。
- 辅助 vision 模型用 `kind=chat` + `profile.supports_vision=true` 的普通 model card，settings 存卡片 ID（不新增 kind，不新增 model card 字段）。
- 描述 prompt 硬编码常量，不做 settings 配置化（二期再说）。

### R5 注入点

- 三个 agent 节点（search/fast/team）的附件处理段，在 `if supports_vision:` 之后加 `else` 分支调 `describe_image_attachments`。
- `build_human_message` 的 `supports_vision=False` 分支读取 `attachment["vision_description"]` 渲染带描述的文本摘要。

### R6 描述 prompt（硬编码）

- 常量：`"Describe the image in detail. If it contains text, transcribe it verbatim. Respond in the user's language."`

### R7 Web + WeCom 双通道一致

- 两端都走同一附件处理链路（agent 节点层），不区分通道。base64 路径对两端一致生效。

### R8 历史图片不缓存

- MVP 不缓存描述，每次 turn 重新描述历史图片。二期可加 Redis/S3 metadata 缓存（参考 Odysseus `.vision/{id}.txt`）。

## Acceptance Criteria

- [ ] 主模型 `supports_vision=False` + 启用 vision assist：发图片，agent 回复能识别出图片内容（非"我看不到图片"），描述包含图片关键信息。
- [ ] 主模型 `supports_vision=True`：直通 `image_url` block，模型原生看图，行为与现状一致（回归测试通过）。
- [ ] `ENABLE_VISION_ASSIST=false`：行为同现状（文本摘要），无辅助模型调用。
- [ ] `VISION_ASSIST_MODEL_ID=""`：行为同现状，无辅助模型调用。
- [ ] 辅助模型调用失败/超时：降级为纯 URL 文本摘要，消息不中断，有 warning 日志。
- [ ] 图片超 `VISION_ASSIST_MAX_BYTES`：跳过描述，降级纯 URL 摘要。
- [ ] 多张图片并发描述。
- [ ] vision assist 路径始终走 base64 data_url，不依赖 `APP_BASE_URL` / URL fetch（内网可用）。
- [ ] Web 端 + WeCom 端均验证通过。
- [ ] 新增测试覆盖：直通、启用且描述成功、辅助模型失败降级、超限降级、非 image 附件直通、多图并发。
- [ ] 配置项在 Settings 页可配置（前端 i18n + SettingsPanel.constants.ts）。

## Out of Scope

- 主模型直接 vision 路径（`supports_vision=True` 时 `image_url` block）改 base64 优先——另一个范围，本次不动。
- 音频附件预处理注入（音频已有 `audio_transcribe_tool` 工具模式，维持现状）。
- 视频附件处理（维持占位符，PRD R4 of wecom-inbound-attachments）。
- 描述缓存（Odysseus `.vision/{id}.txt` 模式）——二期增强。
- 工具模式 `vision_analyze`（方案 A）——不实现，方案 B 已覆盖 MVP。
- per-tenant / per-model 辅助模型配置——全局 settings 即可。
- PDF/文档内容解析（模型仍只看 URL 摘要，与 Web 端 document 行为一致）。
- 描述 prompt 配置化——硬编码 MVP。

## Technical Notes

- 复用 `LLMClient.get_model(model_id=settings.VISION_ASSIST_MODEL_ID)` 解析辅助 vision 模型（chat 模型实例）。
- 复用 `_download_image_as_data_url`（`node_utils.py:134-149`）做 storage 下载 + base64 编码，传 `max_bytes=settings.VISION_ASSIST_MAX_BYTES`。
- **不**复用 `inline_image_attachments_as_data_urls`（它优先 URL fetch，内网不可达）。
- 辅助模型 `ainvoke` 返回 `AIMessage`，`content` 通常是 str。
- base64 膨胀：10MB 图 → ~13MB payload，主流 vision API 可接受；失败有 R3 降级。
- WeCom 端 `wecom_agent_options`（`handler.py:682-685`）不经 `validate_agent_model_access`，`_resolved_supports_vision` 可能未预解析，节点内会 fallback 查库（功能正确但多一次 DB 查询）——可接受。
