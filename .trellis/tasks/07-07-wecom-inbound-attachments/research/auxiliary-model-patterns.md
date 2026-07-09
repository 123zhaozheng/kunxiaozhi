# Research: 辅助多模态模型架构模式（外部项目调研）

- **Query**: hermes / kivio 项目如何做"主模型 + 辅助多模态模型"，同类开源 AI agent 项目的图片/音频/视频识别架构
- **Scope**: external
- **Date**: 2026-07-08

## Findings

### 关键结论

业界已有成熟模式解决"主文本模型不支持 vision 时如何看图"的问题。两个核心参考实现：

1. **NousResearch/hermes-agent**（GitHub 211k★）—— 最完整的"主模型 + auxiliary.vision 辅助模型"实现，**用户提到的 "hermes" 即此项目**。
2. **ZMGID/kivio**（GitHub 303★，Tauri v2 桌面 AI 助手）—— **用户提到的 "kivio" 即此项目**，其 v2.6.5 release notes 明确提到 "Mixer auxiliary model routing"。

其余参考：open-webui（仅有 feature request，未实现）、SillyTavern（成熟的 image captioning 扩展）、Odysseus（预处理注入模式）、LobeChat/Dify（假设模型原生支持 vision，无 fallback）。

---

### 1. NousResearch/hermes-agent —— 权威参考

仓库：https://github.com/NousResearch/hermes-agent
文档：`website/docs/user-guide/features/vision.md`、`website/docs/user-guide/configuration`（#auxiliary-models 段）

#### 核心机制：双路径自动路由

当用户附带图片时，Hermes 根据**当前主模型是否支持 vision**自动选路径（`website/docs/user-guide/features/vision.md`）：

| 主模型类型 | 图片处理方式 |
|---|---|
| **Vision-capable**（GPT-4V、Claude with vision、Gemini、Qwen-VL 等）| 直接以 provider 原生 `image_url` content block 发送**真实像素**，无文本摘要层 |
| **Text-only**（DeepSeek V3、小模型、老 chat endpoint）| 路由到 `vision_analyze` **辅助工具**——由辅助 vision 模型描述图片，文本描述注入对话 |

> "You don't configure this — Hermes looks up your current model's capability in the provider metadata and picks the right path automatically. The practical effect: you can switch between vision and non-vision models mid-session and image handling 'just works' without changing your workflow."

#### 配置：`auxiliary` 区段（config.yaml）

辅助模型在 `config.yaml` 的 `auxiliary` 区段配置，**与主模型完全独立**。`auxiliary.vision` 是图片描述路径用的辅助模型，可配 `provider`/`model`/`base_url`/`api_key`。例如 `auxiliary.vision.model: "openai/gpt-4o"`。也可用 `hermes model` 命令交互配置（选 "Configure auxiliary models"）。

`auxiliary` 是一个**通用机制**，不止 vision——还涵盖 `web_extract`、`compression`、`tts_audio_tags` 等副任务，由 `auxiliary_client.py` 模块统一解析。

#### 实现细节（DeepWiki 确认）

- **预处理路径**：`AIAgent._prepare_messages_for_non_vision_model` 在主模型不支持 vision 时，把消息中的 image part 替换为缓存的 `vision_analyze` 文本描述，确保主模型收到纯文本输入。
- **`vision_analyze` 工具的双行为**：`_handle_vision_analyze` 先查 `_should_use_native_vision_fast_path()`：
  - 主模型支持 vision **且** provider 支持 tool result 里带图（Anthropic/OpenAI/Azure-OpenAI/Gemini 3.x）→ 走 `_vision_analyze_native`，直接返回 image bytes 作为 multimodal tool-result envelope，主模型下一轮原生看到像素（**无信息损失、无额外延迟**）。
  - 否则 → 走 legacy 路径，调辅助 vision 模型描述图片，返回纯文本。
- **配置开关**：`agent.image_input_mode` 可设 `native` 强制直传图。
- **视频**：有独立的 `video_analyze` 工具，分析 URL/文件路径的视频，返回 captions/scene breakdown/key timestamps/visual descriptions。**未发现 `auxiliary.transcribe` 键**（音频转写不在 auxiliary 体系内）。

#### 注入方式

辅助 vision 模型生成的描述以**文本**形式注入主模型上下文。`_preprocess_anthropic_content` 方法把 image part 替换为缓存的描述文本，合并成 single text 或 structured content。

---

### 2. ZMGID/kivio —— "Mixer auxiliary model routing"

仓库：https://github.com/ZMGID/kivio（Tauri v2 + Rust + React/TS 桌面 AI 助手，GPL-3.0）

#### 核心机制：per-feature model routing（按功能路由）

Kivio 的 README 明确阐述设计：

> "**Per-feature routing:** the translator, screenshot translation, Lens, and each chat conversation can each use a different provider and model; **separate default slots exist for vision, title summarization, compaction, and image generation.**"

v2.6.5 release notes 提到 "Mixer auxiliary model routing"。即每个功能模块（翻译、截图翻译、Lens 视觉问答、聊天）可独立指定 provider + model，且**为 vision / title summarization / compaction / image generation 各设独立的默认模型槽位**——这就是"辅助模型"在 Kivio 里的体现：vision 槽位 = 辅助 vision 模型，compaction 槽位 = 辅助压缩模型，等等。

#### OCR 引擎选择（截图翻译场景）

Kivio 截图翻译的 OCR 三选一（Settings 切换），体现了"辅助识别"的多策略：
- **Cloud vision model**（默认）—— 一次多模态调用同时做 OCR + 翻译。
- **System OCR** —— macOS Apple Vision（Swift sidecar）/ Windows.Media.Ocr。
- **RapidOCR** —— 完全离线 PaddleOCR（PP-OCRv5）ONNX 管线。

#### 架构特点

- 四种原生协议适配器：OpenAI Chat Completions / OpenAI Responses / Anthropic Messages / Google Gemini `generateContent`——各为一等适配器，不经有损兼容层。
- 多模型并排：一个问题 fan-out 给多个模型，标签页/分栏对比，互不影响。
- 外部 CLI agent 委托：可把对话交给 Claude Code、codex、cursor、opencode、gemini、kimi、pi、**hermes** 等终端 agent 接管。

#### 注入方式

Kivio 是桌面客户端，vision 模型用于 Lens/截图翻译等**独立功能槽位**，不是在 chat 主对话里做"主模型不支持 vision 时自动注入描述"。但 per-feature routing 的思想（vision 独立槽位）与 hermes 的 `auxiliary.vision` 一致。

---

### 3. open-webui —— 未实现的 feature request（社区需求佐证）

仓库：https://github.com/open-webui/open-webui（145k★）

- **Issue #16864** "feat: Vision filter - Route images to a configured vision model while keeping the conversation on the original model"（2025-08）——精确描述了我们需要的模式：
  1. 检测消息中的图片附件
  2. 若当前模型不支持 vision（或用户启用"always pre-process images"），调配置的 vision 模型
  3. 接收结构化结果（caption、OCR text、可选 objects/layout JSON）
  4. 注入到原模型的 prompt/context（**不转发图片内容**）
  5. 标记图片为已处理，避免后续 turn 重复调 vision 模型
  
  作者明确指出痛点："Many strong chat models (coding/agentic) are text-only. Today, if the user adds an image, they must switch models or lose vision altogether. Switching models mid-thread breaks continuity (different system prompts, safety profiles, tools)."

- **Issue #12389** "feat: set default model for image description"——同类需求：管理员设默认 image-understanding 模型，当 chat LLM 无 vision 能力时，用户上传图片自动由该 vision LLM 描述，原 LLM 继续工作。

- **Issue #20129** 揭示 open-webui 当前行为的坑：vision capability check 会**阻断** follow-up message——一旦对话历史里有图，而模型 vision capability=false，后端校验直接报错。这正是 LambChat 当前 `supports_vision=false` 降级路径要避免的问题。

**结论**：open-webui 目前**无内置辅助 vision 模型**，社区强烈需求但未实现。

---

### 4. SillyTavern —— 成熟的 Image Captioning 扩展

文档：https://docs.sillytavern.app/extensions/captioning/

SillyTavern 的 Image Captioning 扩展是"预处理注入模式"的成熟实现：

- **Source 选择**：Multimodal（cloud：OpenAI/Anthropic/Google/MistralAI 等；local：Ollama/llama.cpp/KoboldCpp 等）、Local（transformers.js，零配置）、Horde（众包）、Extras（已废弃）。
- **Message Template**：可自定义注入模板，用 `{{caption}}` 宏插入描述。默认 `[{{user}} sends {{char}} a picture that contains: {{caption}}]`。
- **Auto-captioning**：勾选 "Automatically caption images" 后，图片粘贴/附件/发送消息时自动生成 caption 注入 prompt。
- **Refine Mode**："Edit captions before saving"——生成 caption 后弹窗供用户编辑。
- **Secondary endpoints**：Multimodal source 默认用主 API endpoint，也可为 captioning 配独立的 secondary endpoint（支持 KoboldCpp/llama.cpp/Ollama/oobabooga/vLLM）。**这正是"辅助模型独立配置"的体现**。
- **Slash command**：`/caption [quiet=true|false]? [mesId=number]? [prompt]` 可对历史消息里的图重新 caption。

**注入方式**：caption 作为文本插入 prompt（Message Template 渲染），主模型只看文本。

---

### 5. Odysseus (pewdiepie-archdaemon/odysseus) —— 预处理注入模式

仓库：https://github.com/pewdiepie-archdaemon/odysseus

DeepWiki 确认的实现细节：

- **核心逻辑**：`ChatHandler.preprocess_message` 方法在主模型收到消息前做预处理。用 `model_supports_vision` 判断主模型是否 text-only；若 text-only，调 `src/document_processor.py` 的 `analyze_image_with_vl_result` 用 VL 模型生成描述。
- **配置**：`vision_model`、`vision_enabled` 设置项（admin UI 可配，`static/js/settings.js`、`static/index.html`）。`_load_vl_setting` 加载设置，`_resolve_vl_model` 解析实际模型（含 auto-detect vision-capable 模型），`vision_model_fallbacks` 提供降级链，`resolve_vision_fallback_candidates` 处理。
- **注入**：text-only 主模型时，**生成的 vision 描述替换图片**进入 context（`enhanced_message` 更新为图片 name + VL 描述，传给 `build_user_content`）。若主模型 vision-capable，图片直传，用户校正过的 caption 作为 explicit hint 追加到 `enhanced_message`。
- **缓存**：vision 描述缓存到 `UPLOAD_DIR/.vision/{id}.txt`，避免重复计算，且用户可通过 vision editor modal 编辑缓存文本。
- **能力探测**：`_VISION_MODEL_KEYWORDS` 和 `_VISION_VL_RE`（`src/chat_helpers.py`）做 best-effort 模型能力探测。`tests/test_vision_model_detection.py` 覆盖。
- **音频**：`build_user_content` 中，`upload_handler.is_audio_file` 识别的音频附件被 base64 编码后以 `type: "audio"` 字典追加到 content。
- **视频**：代码片段未明确，但 YouTube URL 走单独的 transcript + comment 提取路径。

---

### 6. LobeChat / Dify —— 假设主模型原生支持 vision，无辅助 fallback

- **LobeChat**（lobehub/lobe-chat，79.6k★）：vision 是模型能力之一，`@lobechat/model-runtime` 适配 30+ provider。Issue #1457 "Set the Vision model as a tool"（2024-03）提议把 vision 模型做成工具，让 text-only 模型也能间接用图——**被关闭**，理由 "gpt-4-turbo 已同时支持 fc 和 vision，未来模型都会原生支持"。即 LobeChat 无辅助 vision 模型机制。
- **Dify**（langgenius/dify）：LLM 节点直接用多模态模型处理图片，假设模型 vision-capable。Multimodal Knowledge Base 用 vision model 描述图片存为纯文本再做 chunking/embedding——这是 RAG 索引阶段的描述，不是 chat 实时 fallback。

---

### 跨项目模式总结

| 维度 | hermes-agent | kivio | open-webui | SillyTavern | Odysseus |
|---|---|---|---|---|---|
| 辅助 vision 模型独立配置 | ✅ `auxiliary.vision` | ✅ 独立 vision 槽位 | ❌（未实现，仅 feature request）| ✅ secondary endpoint | ✅ `vision_model` |
| 主模型 vision-capable 时直通 | ✅ native fast-path | ✅（chat 用 vision 模型时）| ✅（原生）| ✅（Multimodal source 直传）| ✅ 直传图片 |
| 主模型 text-only 时自动描述 | ✅ 预处理替换 | ⚠️（per-feature 路由，非 chat 内自动）| ❌（未实现）| ✅ auto-captioning | ✅ `preprocess_message` |
| 描述注入方式 | 替换 image part 为文本 | N/A（独立功能槽）| N/A | Message Template 文本注入 | 替换 image 为文本 |
| 作为工具被主模型调用 | ✅ `vision_analyze` 双行为 | ❌ | N/A | ❌（自动）| ❌（自动）|
| 描述缓存 | ✅（cached vision_analyze）| N/A | N/A | ❌ | ✅ `.vision/{id}.txt` |
| 音频处理 | `video_analyze` 工具（视频）| N/A | N/A | N/A | base64 audio 字典 |
| 配置粒度 | 全局 `auxiliary.*` | per-feature + 全局默认槽 | N/A | per-source + secondary endpoint | 全局 `vision_model` |

**两种主流实现范式**：
1. **预处理注入模式**（SillyTavern / Odysseus / hermes 的 `_prepare_messages_for_non_vision_model`）：在主模型推理前，自动用辅助模型描述图片，把文本描述替换/追加进 context。对主模型透明，无额外 tool-call round-trip。
2. **工具模式**（hermes `vision_analyze` 工具）：主模型主动调用 `vision_analyze` 工具，工具内部根据主模型能力决定返回像素还是文本描述。主模型需发起 tool call，有额外 round-trip，但主模型可决定是否需要看图。

hermes 是唯一同时实现两种范式并自动切换的项目（预处理用于历史消息里的图，工具用于主模型主动查询）。

## Caveats / Not Found

- hermes-agent 的具体源码行号未直接读取（DeepWiki 给出方法名，未给 file:line）。如需精确锚点需 clone 仓库或读 GitHub raw 文件。
- kivio 的 `auxiliary.vision` 等配置项具体 key 名未从源码确认（README 仅描述 "separate default slots for vision"）。kivio 是 Rust + TS 桌面应用，架构与 LambChat（Python 后端）差异大，主要参考其 per-feature routing 思想。
- 用户提到 "hermes" 和 "kivio" 有"混音响模型"——"混音响"可能是 "混音" + "响(应)" 的笔误或口误，实际指 hermes 的 `auxiliary` 多模型混合 + kivio 的 Mixer auxiliary model routing。未发现专门处理"音频混合"的辅助模型。
- 未找到名为 "Hermes AI" 或 "Kivio" 的其他主流项目匹配用户描述；NousResearch/hermes-agent 和 ZMGID/kivio 是最精确匹配。
