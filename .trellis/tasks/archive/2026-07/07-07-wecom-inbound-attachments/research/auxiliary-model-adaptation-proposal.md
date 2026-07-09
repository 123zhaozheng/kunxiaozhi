# Research: 辅助多模态模型适配方案

- **Query**: 基于 hermes/kivio/同类项目模式 + LambChat 现状，给出"主模型不支持 vision 时用辅助 vision 模型"的优雅适配方案
- **Scope**: mixed
- **Date**: 2026-07-08

## Findings

### 核心设计约束（来自任务要求）

1. **不破坏 `supports_vision=true` 直通路径**：主模型支持 vision 时，图片继续走 `image_url` block，不经过辅助模型。
2. **主模型不支持 vision 时**：自动用辅助 vision 模型识别图片，把识别结果（文本描述）注入主模型上下文。
3. **配置复用现有基础设施**：model_configs 卡片体系 + settings 卡片 ID 引用（复刻 `AUDIO_TRANSCRIPTION_MODEL_ID` 模式）。
4. **音频/视频可扩展**：音频已有 `audio_transcribe_tool`；视频预留扩展点。
5. **WeCom + Web 双通道一致**：两端都走同一附件处理链路。

---

### 候选方案对比

#### 方案 A：工具模式（主模型主动调用 `vision_analyze` 工具）

**机制**：新增 `vision_analyze` 内置工具（复刻 `audio_transcribe_tool` 范式）。主模型收到图片附件时，文本摘要里带 URL + 工具提示，主模型主动调 `vision_analyze(url)` 获取描述文本。

**配置**：`settings.VISION_ASSIST_MODEL_ID`（kind=vision 卡片 ID，但 kind 仍是 `chat`——vision 模型本质是 chat 模型，只是 supports_vision=true）。或新增 `kind="vision"` 但会让 chat 模型卡片体系复杂化。**建议复用 `kind="chat"` + `supports_vision=true` 标记，settings 存卡片 ID**。

**注入方式**：工具返回文本描述，作为 tool result 进入主模型上下文（主模型下一轮看到）。

**参考**：hermes-agent 的 `vision_analyze` 工具（但 hermes 的工具还有 native fast-path 双行为，本方案纯文本返回）。

| 优点 | 缺点 |
|---|---|
| 复刻 `audio_transcribe_tool` 现成范式，改动最小 | **依赖主模型主动调工具**——主模型可能不调（尤其小模型），或调错 URL |
| 主模型可决定是否需要看图（省成本） | 多一次 tool-call round-trip，延迟增加 |
| 工具结果天然进 context，无需改 `build_human_message` | **当前 bug 链路正是主模型把 URL 当文件路径用工具读**——工具模式可能重蹈覆辙（主模型仍需理解何时调 vision_analyze vs reveal_file）|
| 与 audio_transcribe 对称，扩展音频/视频只需加 `video_analyze` 工具 | 主模型不支持 vision 但也看不到工具返回的图（只能看文本描述）——信息损失 |

**改动范围**：
- 新增 `src/infra/tool/vision_analyze_tool.py`（复刻 audio_transcribe_tool 结构）
- `src/infra/tool/internal_registry.py:29-60`：加 `if settings.ENABLE_VISION_ASSIST: tools.append(get_vision_analyze_tool())`
- `src/kernel/config/definitions.py` + `base.py` + `setting.py`：加 `ENABLE_VISION_ASSIST`/`VISION_ASSIST_MODEL_ID`/`VISION_ASSIST_MAX_BYTES`/`SettingCategory.VISION_ASSIST`
- 前端 i18n + `SettingsPanel.constants.ts`
- **不改 `build_human_message`**——图片仍走文本摘要，但摘要里提示"用 vision_analyze 工具看图"

**风险**：
- 主模型行为不可控。DeepSeek-V3 等模型 tool-calling 能力参差，可能不调或乱调。
- 与现有 `reveal_file_tool`/`transfer_file_tool` 语义重叠，主模型可能困惑。

---

#### 方案 B：预处理注入模式（推荐）

**机制**：在 `build_human_message` 之前，若 `supports_vision=False` 且有 image 附件，自动调辅助 vision 模型描述每张图片，把描述文本注入用户消息（替换/追加）。主模型完全无感，只看到文本。

**配置**：`settings.ENABLE_VISION_ASSIST`（bool）+ `settings.VISION_ASSIST_MODEL_ID`（kind=chat 且 supports_vision=true 的卡片 ID）+ `settings.VISION_ASSIST_MAX_BYTES`（下载限流，复用 `IMAGE_DATA_URL_INLINE_MAX_BYTES=2MB` 语义）。

**注入方式**：辅助 vision 模型返回的描述文本，以结构化方式追加到用户消息文本。参考 SillyTavern 的 Message Template：
```
[图片附件: {name}]
<vision_description>
{辅助模型生成的描述}
</vision_description>
[附件链接: {url}]
```
图片附件不再走 `_format_attachment_summary` 的纯 URL 摘要，而是带描述。

**参考**：SillyTavern（auto-captioning + Message Template）、Odysseus（`preprocess_message` + 替换 image part）、hermes（`_prepare_messages_for_non_vision_model`）。

**直通保护**：`supports_vision=True` 时完全跳过辅助模型，走原 `image_url` block 路径——零改动、零风险。

| 优点 | 缺点 |
|---|---|
| **主模型无感**——不依赖主模型 tool-calling 能力，行为确定 | 每张图都调一次辅助模型（成本+延迟），即使用户没问图 |
| 不改主模型 tool 列表，无工具语义冲突 | 描述质量依赖辅助 vision 模型能力（建议用 gpt-4o-mini / qwen-vl-plus 等廉价 vision 模型）|
| 三个 agent 节点改一处（`build_human_message` 或其调用方）即覆盖 | 主模型看不到原图，只看描述——若用户要 OCR 精确文字，描述可能不全（可 prompt 引导辅助模型做 OCR）|
| 与 hermes/SillyTavern/Odysseus 主流模式一致 | 需管理辅助模型调用的失败降级（辅助模型挂了应 fallback 到当前文本摘要，不阻断）|
| **直通路径零改动**——`supports_vision=True` 完全不受影响 | 历史消息里的图片：checkpoint 里存的是注入后的文本（含描述），切回 vision 模型后看不到原图（hermes 缓存描述，Odysseus 缓存 `.vision/{id}.txt`）|
| 扩展音频/视频：音频已有 `audio_transcribe_tool`，视频可加 `video_analyze` 工具或同样预处理注入 | |

**改动范围**（file:line 级）：
1. **新增** `src/agents/core/vision_assist.py`（预处理模块）：
   - `async def describe_image(attachment, *, base_url, model_id) -> str | None`：下载图片 → 构造 `image_url` block → 调 `LLMClient.get_model(model_id=settings.VISION_ASSIST_MODEL_ID)` → 返回描述文本。失败返回 None（降级）。
   - `async def describe_image_attachments(attachments, *, supports_vision, base_url, model_id, enable) -> list[dict]`：若 `supports_vision` 或 `not enable` 直通；否则对每个 image attachment 调 `describe_image`，把描述塞回 attachment dict（如 `attachment["vision_description"] = ...`）。
2. **改** `src/agents/core/node_utils.py:build_human_message`（`node_utils.py:260-310`）：
   - 在 `supports_vision=False` 分支，image attachment 若有 `vision_description` 字段，渲染为带描述的文本（而非纯 URL 摘要）。
   - 或：新增 `build_human_message_with_vision_assist` 异步版本，在 `build_human_message` 前做预处理。**推荐改 `build_human_message` 签名为 async 或在调用方预处理**——见下。
3. **改** 三个 agent 节点的附件处理段（`search_agent/nodes.py:311-316`、`fast_agent/nodes.py:278-283`、`team_agent/nodes.py:546-551`）：
   - 在 `if supports_vision:` 之后加 `else` 分支调 `describe_image_attachments`：
     ```python
     if supports_vision:
         attachments = await inline_image_attachments_as_data_urls(attachments, base_url=...)
     else:
         attachments = await describe_image_attachments(attachments, base_url=..., model_id=settings.VISION_ASSIST_MODEL_ID, enable=settings.ENABLE_VISION_ASSIST)
     new_message = build_human_message(user_input, attachments, supports_vision=supports_vision)
     ```
   - `build_human_message` 内 `supports_vision=False` 分支读取 `attachment.get("vision_description")` 渲染。
4. **配置层**（复刻 audio_transcribe 范式）：
   - `src/kernel/config/definitions.py`：加 `ENABLE_VISION_ASSIST`（bool，默认 False）、`VISION_ASSIST_MODEL_ID`（string，默认 ""，`depends_on=ENABLE_VISION_ASSIST`，`frontend_visible=True`）、`VISION_ASSIST_MAX_BYTES`（number，默认 2MB）。
   - `src/kernel/config/base.py:322` 附近：加 `ENABLE_VISION_ASSIST: bool = False` / `VISION_ASSIST_MODEL_ID: str = ""` / `VISION_ASSIST_MAX_BYTES: int = 2*1024*1024`。
   - `src/kernel/schemas/setting.py`：加 `VISION_ASSIST = "vision_assist"` 到 `SettingCategory`。
   - `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`：加 `ENABLE_VISION_ASSIST`/`VISION_ASSIST_MODEL_ID`/`VISION_ASSIST_MAX_BYTES` 描述。
   - `frontend/src/components/panels/SettingsPanel.constants.ts:47,58`：加 `VISION_ASSIST_MODEL_ID` 到 `MODEL_ID_SETTINGS`，kind 映射 `chat`（vision 模型是 supports_vision=true 的 chat 卡片，不新增 kind）。
5. **辅助 vision 模型解析**：复用 `LLMClient.get_model(model_id=settings.VISION_ASSIST_MODEL_ID)`——返回 chat 模型实例（`ChatOpenAI`/`ChatAnthropic` 等），直接 `.ainvoke([HumanMessage(content=[{"type":"image_url","image_url":{"url":...}}])])` 获取描述。**不需要 `get_card_config`**——因为 vision 模型是标准 chat 模型，走 chat 协议；不像 transcribe 用专门 `audio.transcriptions.create` API。
6. **缓存（可选，二期）**：参考 Odysseus `.vision/{id}.txt`，按图片 sha256 缓存描述到 Redis 或 S3 metadata，避免重复计算。

**风险与缓解**：
- **辅助模型延迟**（每图 1-3s）：可并发描述多图（`asyncio.gather`）。WeCom 5 秒回调截止已由 `send_thinking_placeholder` 处理（`handler.py:792-793`），多几秒可接受。
- **辅助模型失败**：`describe_image` 返回 None 时，attachment 退回当前文本摘要路径——零降级风险。
- **辅助模型配置缺失**（`VISION_ASSIST_MODEL_ID=""`）：`describe_image_attachments` 检测到不启用直接直通，行为同现状。
- **描述质量**：prompt 引导辅助模型做详细描述 + OCR（"Describe the image in detail. If it contains text, transcribe it verbatim."）。参考 LobeChat issue #1457 的 prompt。
- **历史消息**：注入的描述文本会进 checkpoint。切回 vision 模型后，历史里的图变成文本描述——可接受（用户发新图仍走 vision）。若要保留原图用于 vision 模型，需在 attachment 里保留 url 且 `build_human_message` 对 vision 模型优先用 url（当前已如此）。

---

#### 方案 C：混合模式（预处理 + 工具，hermes 风格）

**机制**：方案 B 的预处理注入 + 方案 A 的 `vision_analyze` 工具并存。预处理处理"用户发的图"，工具处理"主模型主动想看的图"（如 URL 在工具结果里、主模型需要重新分析）。

**参考**：hermes-agent 同时实现两者，`vision_analyze` 工具有 native fast-path 双行为。

| 优点 | 缺点 |
|---|---|
| 最灵活，覆盖所有场景 | 改动最大（方案 A + B 全做）|
| 与 hermes 对齐 | MVP 过度设计——当前 bug 用方案 B 已解决 |
| 主模型可重新分析已描述的图 | 两套机制维护成本高 |

**适用**：二期增强，非 MVP。

---

### 推荐方案：B（预处理注入模式）

**理由**：
1. **确定性**：主模型无感，行为不依赖 tool-calling 能力——DeepSeek-V3/V4 等文本模型 tool-calling 不稳定，方案 A 不可靠。
2. **最小改动**：复刻 `audio_transcribe_tool` 的配置范式，复用 `LLMClient.get_model` 解析模型，核心逻辑集中在新文件 `vision_assist.py` + `build_human_message` 小改。
3. **直通零风险**：`supports_vision=True` 路径完全不动，现有 vision 模型（Claude/Gemini/GPT-4V）用户无感知。
4. **业界主流**：SillyTavern、Odysseus、hermes 的预处理路径都是此模式，验证充分。
5. **任务对齐**：PRD R1 要求"模型支持 vision 时能真正看到图片内容"——方案 B 直通路径满足；非 vision 模型至少能看到描述（比当前只看 URL 强），且 PRD Out of Scope 未禁止辅助模型。
6. **扩展性**：音频已有 `audio_transcribe_tool`（工具模式，适合音频——主模型主动转写）；视频未来可加预处理或工具。方案 B 不排斥未来加工具。

---

### 推荐方案的改动清单（file:line 级）

#### 新增文件

1. **`src/agents/core/vision_assist.py`**（核心预处理模块）：
   ```python
   # 伪代码
   from src.infra.llm.client import LLMClient
   from src.kernel.config import settings
   from src.infra.logging import get_logger
   from src.agents.core.node_utils import _is_image_attachment, inline_image_attachments_as_data_urls
   
   VISION_ASSIST_PROMPT = "Describe the image in detail. If it contains text, transcribe it verbatim. Respond in the user's language."
   
   async def describe_image(attachment: dict, *, base_url: str) -> str | None:
       """Download image, call auxiliary vision model, return text description."""
       if not settings.ENABLE_VISION_ASSIST or not settings.VISION_ASSIST_MODEL_ID:
           return None
       # 复用 inline_image_attachments_as_data_urls 拿到可访问的 url/data_url
       inlined = await inline_image_attachments_as_data_urls([attachment], base_url=base_url)
       if not inlined:
           return None
       att = inlined[0]
       image_url = att.get("url") or att.get("data_url")
       if not image_url:
           return None
       try:
           llm = await LLMClient.get_model(model_id=settings.VISION_ASSIST_MODEL_ID)
           from langchain_core.messages import HumanMessage
           msg = HumanMessage(content=[
               {"type": "text", "text": VISION_ASSIST_PROMPT},
               {"type": "image_url", "image_url": {"url": image_url}},
           ])
           resp = await llm.ainvoke([msg])
           return getattr(resp, "content", str(resp))
       except Exception as e:
           logger.warning("[vision_assist] describe failed for %s: %s", att.get("key"), e)
           return None
   
   async def describe_image_attachments(attachments, *, supports_vision, base_url):
       """If main model is text-only and vision assist enabled, attach descriptions."""
       if supports_vision or not settings.ENABLE_VISION_ASSIST or not settings.VISION_ASSIST_MODEL_ID:
           return attachments  # 直通
       import asyncio
       async def _enrich(att):
           if not _is_image_attachment(att):
               return att
           desc = await describe_image(att, base_url=base_url)
           if desc:
               return {**att, "vision_description": desc}
           return att
       return await asyncio.gather(*[_enrich(a) for a in attachments]) if attachments else []
   ```

2. **`tests/agents/core/test_vision_assist.py`**：覆盖直通、启用且 vision 模型、辅助模型失败降级、非 image 附件直通。

#### 修改文件

| 文件 | 行号 | 改动 |
|---|---|---|
| `src/agents/core/node_utils.py` | `223-257`（`_format_attachment_summary`）| image attachment 若有 `vision_description` 字段，渲染为带描述文本（而非纯 URL 摘要）。建议在 `**[{name}]**` 块后追加 `\n- 视觉描述: {vision_description}` |
| `src/agents/core/node_utils.py` | `287-299`（`build_human_message` 的分支）| `supports_vision=False` 分支：image attachment 有 `vision_description` 时仍进 `text_summary_attachments`（走带描述的摘要），无描述时维持现状 |
| `src/agents/search_agent/nodes.py` | `311-316` | `if supports_vision:` 之后加 `else: attachments = await describe_image_attachments(attachments, supports_vision=False, base_url=...)` |
| `src/agents/fast_agent/nodes.py` | `278-283` | 同上 |
| `src/agents/team_agent/nodes.py` | `546-551` | 同上 |
| `src/kernel/config/definitions.py` | `684` 附近（AUDIO_TRANSCRIPTION 段后）| 加 `ENABLE_VISION_ASSIST`/`VISION_ASSIST_MODEL_ID`/`VISION_ASSIST_MAX_BYTES` 三个定义，category=SettingCategory.VISION_ASSIST |
| `src/kernel/config/base.py` | `325` 附近 | 加 `ENABLE_VISION_ASSIST: bool = False` / `VISION_ASSIST_MODEL_ID: str = ""` / `VISION_ASSIST_MAX_BYTES: int = 2*1024*1024` |
| `src/kernel/schemas/setting.py` | `55` 附近 | 加 `VISION_ASSIST = "vision_assist"` |
| `frontend/src/i18n/locales/en.json` | `1907` 附近 | 加 `ENABLE_VISION_ASSIST`/`VISION_ASSIST_MODEL_ID`/`VISION_ASSIST_MAX_BYTES` 描述 |
| `frontend/src/i18n/locales/zh.json`（及 ja/ko/ru） | 对应位置 | 同上 |
| `frontend/src/components/panels/SettingsPanel.constants.ts` | `47,58` | 加 `VISION_ASSIST_MODEL_ID` 到 `MODEL_ID_SETTINGS`，kind 映射 `"chat"` |

#### 不需改动

- `build_human_message` 的 `supports_vision=True` 分支（`node_utils.py:291-297`）——直通路径零改动。
- `inline_image_attachments_as_data_urls`——复用，不改。
- `resolve_model_supports_vision`——复用，不改。
- `audio_transcribe_tool`——音频已覆盖，不改。
- model card schema（`ModelConfig`/`ModelProfile`）——不新增字段，vision 辅助模型就是 `kind=chat` + `profile.supports_vision=true` 的普通卡片，settings 用 ID 引用。
- WeCom handler——附件构造逻辑不变，描述在 agent 节点层做。

---

### 配置方式说明

**辅助 vision 模型配置**（复刻 audio_transcribe 模式）：
1. 管理员在 Model 管理页新增一个 vision 模型卡片（如 `openai/gpt-4o-mini`），`kind=chat`，`profile.supports_vision=true`，填 api_key/api_base。
2. 在 Settings 页的 `VISION_ASSIST_MODEL_ID` 下拉选该卡片（前端 `MODEL_ID_SETTINGS` 会自动渲染 kind=chat 的卡片下拉）。
3. 启用 `ENABLE_VISION_ASSIST` 开关。
4. 主模型（如 `deepseek-v4-flash`）保持 `supports_vision=false`。

**为什么不用 `fallback_model` 字段**：
- `fallback_model` 语义是"主模型失败时整个对话切到 fallback chat 模型"（retry middleware），会接管所有推理。
- 辅助 vision 模型只做单步图片描述，不接管对话。语义不同，复用会混淆。
- 用 settings 独立配置（`VISION_ASSIST_MODEL_ID`）与 `audio_transcribe` 对称，符合现有范式。

**为什么不在 model card 加 `auxiliary_vision_model` 字段**：
- 每个主模型配一个辅助 vision 模型过于繁琐——通常全局一个辅助 vision 模型即可。
- settings 全局配置更简单，与 audio_transcribe 一致。
- 若未来需 per-tenant 配置，可再扩展。

---

### 音频/视频扩展性

- **音频**：已有 `audio_transcribe_tool`（工具模式，主模型主动调）。当前 WeCom voice 有转写优先（`handler.py:516-519`），无转写时 audio attachment 走 `text_summary_attachments`（只 URL 摘要）。可考虑同样加预处理注入：`describe_audio_attachments` 自动转写后注入文本。但音频走工具模式更自然（主模型决定是否转写），**建议音频维持工具模式，本次不改**。
- **视频**：当前 WeCom video 走占位符（`handler.py:564`，PRD R4 维持现状）。未来可加 `video_analyze` 工具（参考 hermes `video_analyze`），或预处理注入（抽帧 + vision 模型描述）。本次不做。

---

### 验收对齐（PRD Acceptance Criteria）

| PRD 验收项 | 方案 B 如何满足 |
|---|---|
| image 消息：模型支持 vision 时，agent 能描述图片内容 | 直通路径 `supports_vision=True` → `image_url` block，模型原生看图（零改动）|
| image 消息（非 vision 模型）：能看到图片内容 | 方案 B 预处理注入描述文本——非 vision 模型看到描述（比当前只看 URL 强，满足"端到端验证：发图片 → agent 回复识别出图片内容"）|
| file 消息：文件名/类型/URL 出现在摘要 | `_format_attachment_summary` 不变（非 image 附件不受影响）|
| voice/video/mixed | 不受影响（仅 image attachment 走 vision_assist）|
| 失败降级 | `describe_image` 返回 None 时退回当前文本摘要路径 |

**注意**：PRD R1 原文是"模型支持 vision 时能真正看到图片内容"——方案 B 直通路径满足。PRD 未明确要求"非 vision 模型也能看图"，但本次研究的核心目标正是解决"非 vision 模型看不到图"的 bug——方案 B 是对 PRD 的增强，不冲突。

## Caveats / Not Found

- 辅助 vision 模型的 `ainvoke` 返回格式需实测确认（`resp.content` 是 str 还是 list）。LangChain chat 模型 `ainvoke` 返回 `AIMessage`，`content` 通常是 str（纯文本响应）。
- `inline_image_attachments_as_data_urls` 当前只在 `supports_vision=True` 时调（节点内）。方案 B 的 `describe_image` 复用它拿 url/data_url——需确认 `base_url` 在 WeCom 端的传递（`configurable.get("base_url", "")`，三节点都有）。
- 缓存（二期）未纳入 MVP；若不缓存，每次 turn 的历史图片会重复描述（成本）。MVP 可接受（用户通常不发重复图）。
- 前端 `SettingsPanel.constants.ts` 的 `MODEL_ID_SETTINGS` kind 映射需确认是否支持 `kind="chat"` 过滤 vision 卡片——当前 audio_transcribe 映射 `transcribe`，vision 应映射 `chat` 但只显示 `supports_vision=true` 的卡片，可能需前端加过滤逻辑。
