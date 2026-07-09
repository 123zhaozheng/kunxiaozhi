# Research: LambChat 当前附件/模型架构梳理

- **Query**: LambChat 模型配置存储、附件处理链路、工具体系、多 agent 架构现状（含 file:line 锚点）
- **Scope**: internal
- **Date**: 2026-07-08

## Findings

### 关键结论

LambChat 已具备"主模型 + 辅助模型"架构的**全部基础设施**，只差临门一脚：

1. **模型卡片体系已支持多 kind**（`chat`/`embedding`/`rerank`/`transcribe`），`LLMClient.get_card_config(kind=...)` 可解析非 chat 卡片——`audio_transcribe_tool` 已用此机制。
2. **`supports_vision` 直通路径已实现**（`build_human_message` + `inline_image_attachments_as_data_urls`），主模型 vision-capable 时图片走 `image_url` block。
3. **`fallback_model` 字段已存在**，但语义是"主模型失败时降级到另一个 chat 模型"（retry middleware），**与"辅助 vision 模型"语义不同**，需新增字段或复用 settings。
4. **`audio_transcribe_tool` 是现成的辅助模型工具范式**：用 `settings.AUDIO_TRANSCRIPTION_MODEL_ID`（kind=transcribe 卡片 ID）解析辅助模型，主模型以工具调用方式使用。可直接复制此模式做 `vision_analyze` 工具或预处理注入。
5. **三个 agent 节点（search/fast/team）的附件处理代码完全重复**，改一处即可覆盖全部。

---

### 1. 模型配置与存储

#### ModelConfig schema（`src/kernel/schemas/model.py`）

| 字段 | 行号 | 说明 |
|---|---|---|
| `value` | `model.py:34` | 模型标识符（如 `anthropic/claude-3-5-sonnet`）|
| `provider` | `model.py:35-38` | 显式 provider（openai/anthropic/google/deepseek 等）|
| `kind` | `model.py:39-43` | 模型能力类型：`chat`/`embedding`/`rerank`/`transcribe`，默认 `chat`（`ModelKind`，`model.py:12`）|
| `api_key` | `model.py:50` | 每模型 API key 覆盖（加密存储）|
| `api_base` | `model.py:51` | 每模型 API base URL 覆盖 |
| `temperature` | `model.py:52` | 每模型温度覆盖 |
| `max_tokens` | `model.py:53` | 每模型 max tokens 覆盖 |
| `profile` | `model.py:54` | `ModelProfile`：`max_input_tokens` + `supports_vision` |
| `fallback_model` | `model.py:55-57` | **主模型失败时降级的模型 ID（UUID）**——语义是 retry fallback，不是辅助 vision |
| `enabled` | `model.py:58` | 是否启用 |
| `order` | `model.py:59` | 显示顺序 |

`ModelProfile`（`model.py:16-25`）：
```python
class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    max_input_tokens: Optional[int] = Field(None, ...)
    supports_vision: Optional[bool] = Field(False, description="Whether this model accepts image input")
```

**关键**：`supports_vision` 是 `ModelProfile` 的字段，存在 model card 上，按模型维度配置。

#### ModelStorage（`src/infra/agent/model_storage.py`）

- 集合名：`model_configs`（`model_storage.py:20`）。
- `get(model_id)`（`model_storage.py:210-224`）：按 ID 查模型，解密 api_key。
- `get_by_value(value)`（`model_storage.py:226-247`）：按 value 查（优先 enabled）。
- 索引：`id`（unique）、`value`（非 unique，同模型可多渠道）、`(value, provider, api_base)`（unique）、`enabled`、`order`（`model_storage.py:55-71`）。
- 全局单例：`get_model_storage()`（`model_storage.py:516-521`）。

#### LLMClient（`src/infra/llm/client.py`）

- `LLMClient.get_model(...)`（`client.py:363-586`）：解析 chat 模型。优先级：`model_config`（已解析 ModelConfig）> `model_id`（DB 查）> `model`（value 串）> 默认模型。带 LRU 缓存（`_model_cache`，`client.py:243`）。
- `LLMClient.get_card_config(model_id, *, kind)`（`client.py:588-631`）：**解析非 chat 模型卡片**（embedding/rerank/transcribe）。返回 `{api_base, api_key, model}` dict，**不构造 chat 模型**——因为转写/embedding/rerank 用不同 API。会校验 `stored.kind == kind`（legacy 卡片默认 `chat`，只匹配 `kind="chat"`）。
- provider 路由：`PROVIDER_REGISTRY`（`client.py:32-65`）映射 provider → (protocol, prefixes)。`_resolve_protocol`（`client.py:68-71`）返回 `anthropic`/`google`/`openai`。`_parse_provider`（`client.py:74-95`）从 `provider/model-name` 或前缀推断。

**关键**：`get_card_config` 是辅助模型解析的现成入口——`audio_transcribe_tool` 已用它解析 kind=transcribe 卡片。辅助 vision 模型可走类似路径（见方案文档）。

#### `fallback_model` vs 辅助 vision 模型 —— 语义区分

| 维度 | `fallback_model`（已有）| 辅助 vision 模型（待引入）|
|---|---|---|
| 触发时机 | 主模型**请求失败**时（retry middleware）| 主模型**不支持 vision** 且用户发了图片时 |
| 模型 kind | `chat`（另一个 chat 模型接管整个对话）| `chat`（vision-capable，仅描述图片，不接管对话）|
| 使用方式 | retry middleware 内部切换 LLM 实例 | 预处理阶段调一次，结果注入主模型 context |
| 消费方 | `resolve_fallback_model`（`node_utils.py:25-79`）→ `create_retry_middleware`（`retry.py:281-294`）| 待实现 |
| 配置位置 | model card 的 `fallback_model` 字段 | 见方案文档（建议 settings 或 model card 新字段）|

---

### 2. 附件处理链路

#### 入口：`build_human_message`（`src/agents/core/node_utils.py:260-310`）

```python
def build_human_message(text, attachments, *, supports_vision=False) -> HumanMessage:
    if not attachments:
        return HumanMessage(content=text)
    multimodal_images: list[dict] = []
    text_summary_attachments: list[dict] = []
    for attachment in attachments:
        url = attachment.get("url")
        data_url = attachment.get("data_url")
        image_url = url or data_url
        if supports_vision and _is_image_attachment(attachment) and image_url:
            multimodal_images.append({"type": "image_url", "image_url": {"url": image_url}})
        elif url:
            text_summary_attachments.append(attachment)
    enhanced_text = _format_attachment_summary(text, text_summary_attachments)
    if not multimodal_images:
        return HumanMessage(content=enhanced_text)
    return HumanMessage(content=[{"type": "text", "text": enhanced_text}, *multimodal_images])
```

**分支逻辑**（`node_utils.py:287-299`）：
- `supports_vision=True` + image attachment + 有 url/data_url → 走 `image_url` multimodal block。
- 否则（含 `supports_vision=False`）→ image 走 `_format_attachment_summary`（只生成文件名/类型/URL 文本摘要，**不读图片内容**）。

**这就是当前 bug 根因**：`deepseek-v4-flash` 的 `supports_vision=False`，图片附件被降级为文本摘要（只有 URL），模型看不到图片内容，只能尝试用工具读 URL——全部失败。

#### `resolve_model_supports_vision`（`node_utils.py:82-109`）

```python
async def resolve_model_supports_vision(model_id, selected_model, *, log_prefix="") -> bool:
    if not model_id and not selected_model:
        return False
    storage = get_model_storage()
    db_model = None
    if model_id:
        db_model = await storage.get(model_id)
    elif selected_model:
        db_model = await storage.get_by_value(selected_model)
    if not db_model or not getattr(db_model, "profile", None):
        return False
    return bool(getattr(db_model.profile, "supports_vision", False))
```

从 model card 的 `profile.supports_vision` 解析。三个 agent 节点都会调（或读预解析的 `_resolved_supports_vision`）。

#### `inline_image_attachments_as_data_urls`（`node_utils.py:152-220`）

vision 模型需要可访问的图片 URL。此函数把 image attachment 补全：
- 已有 `url` 或 `data_url` → 直通（`node_utils.py:170-172`）。
- 有 `key` + `base_url` → 构造 `{base_url}/api/upload/file/{key}`（`node_utils.py:179-186`）。
- 有 `key` 无 `base_url` → 从 S3 下载并 base64 编码为 data_url（`node_utils.py:188-218`，受 `max_inline_bytes=2MB` 限制）。

**只在 `supports_vision=True` 时调用**（见各 agent 节点）。

#### `_format_attachment_summary`（`node_utils.py:223-257`）

非 image 附件或非 vision 模型的降级路径：把附件渲染成文本（文件名/类型/MIME/大小/URL），追加到用户文本后。**不读文件内容**。

#### 三个 agent 节点的附件处理（完全重复）

| 节点 | 文件 | 附件处理行号 |
|---|---|---|
| SearchAgent | `src/agents/search_agent/nodes.py` | `nodes.py:311-316` |
| FastAgent | `src/agents/fast_agent/nodes.py` | `nodes.py:278-283` |
| TeamAgent | `src/agents/team_agent/nodes.py` | `nodes.py:546-551` |

三处代码几乎一字不差：
```python
user_input = state.get("input", "")
if supports_vision:
    attachments = await inline_image_attachments_as_data_urls(
        attachments, base_url=configurable.get("base_url", ""))
new_message = build_human_message(user_input, attachments, supports_vision=supports_vision)
```

`supports_vision` 来源（三处一致）：
```python
supports_vision = agent_options.get("_resolved_supports_vision")
if supports_vision is None:
    supports_vision = await resolve_model_supports_vision(model_id, selected_model, log_prefix="[Agent]")
supports_vision = bool(supports_vision)
```

**关键**：改 `build_human_message` 或在调用前加预处理步骤，即可同时覆盖三个 agent。

#### 入口预解析：`_attach_resolved_model_options`（`src/api/routes/chat.py:109-133`）

Web 端 chat 入口在 `validate_agent_model_access` 里预解析模型信息塞进 `agent_options`，避免节点内重复查库：
```python
async def _attach_resolved_model_options(agent_options: dict, model: ModelConfig) -> None:
    agent_options["model_id"] = model.id
    agent_options["model"] = model.value
    agent_options["_resolved_model_config"] = _safe_model_config_dict(model)
    agent_options["_resolved_supports_vision"] = bool(getattr(model.profile, "supports_vision", False))
    # ... fallback_model 解析 ...
    agent_options["_resolved_fallback_model"] = fallback_id
    agent_options["_resolved_model_profile"] = _model_profile_dict(model)
```

`validate_agent_model_access`（`chat.py:136-181`）在请求入口校验模型权限并填充上述字段。**WeCom 端 `execute_wecom_agent` 走 `agent.stream(...)`，是否经过 `validate_agent_model_access` 取决于 wecom_agent_options 构造路径**——见下。

---

### 3. 入站附件来源

#### Web 端：`src/api/routes/upload.py`

- `upload_file`（`upload.py:392-553`）：分类 → S3 上传 → 写 file_record → 返回 `{key, url, name, type, mime_type, size}`。
- `_build_upload_response`（`upload.py:109-132`）：构造响应，`url = {base_url}/api/upload/file/{key}`。
- `_get_base_url`（`upload.py:94-106`）：优先 `settings.APP_BASE_URL`（须合法 http(s)），fallback `request.base_url`。
- 文件分类：`get_file_category`（`upload.py:415`）→ `FileCategory`（image/video/audio/document/unknown）。
- `get_file_proxy`（`upload.py:945-1034`）：`/api/upload/file/{key}` 端点，local 直读/S3 presigned redirect。

#### WeCom 端：`src/infra/agent/wecom/handler.py`

- `_build_single_attachment`（`handler.py:405-468`）：下载 WeCom 媒体 → S3 上传 → 构造 attachment dict（与 Web 端 AttachmentSchema 对齐）。`attachment_type`: `image`/`document`/`audio`。
- `_build_wecom_attachments`（`handler.py:471-574`）：按 `msg_type`（image/file/voice/mixed）构造 attachment 列表。video 和未知类型不构造（保持占位符）。
- `url` 字段（`handler.py:467`）：`APP_BASE_URL` 配置时填 `{base_url}/api/upload/file/{key}`，否则留空（让 `inline_image_attachments_as_data_urls` 用 live base_url 重建或 data_url 兜底）。
- `_download_and_upload_media`（`handler.py:344-402`）：`bot.download_media_file(url, aes_key)` 下载 + AES 解密 → `storage.upload_bytes` 上传 S3。
- voice 特殊：有 WeCom 自动转写文本时返回 `None`（不走附件链路，转写文本做 content，`handler.py:516-519`）。
- 透传：`task_manager.submit(attachments=attachments or None, ...)`（`handler.py:846`）。

**注意**：WeCom 端 `wecom_agent_options`（`handler.py:682-685`）由 `apply_dify_kb_dataset_ids_to_agent_options` 构造，**不经过 `validate_agent_model_access`**——所以 `_resolved_supports_vision` 可能未预解析，agent 节点内会 fallback 到 `resolve_model_supports_vision` 查库（`nodes.py:116-121`）。

---

### 4. 现有工具体系

#### 工具注册机制

- `ToolRegistry`（`src/infra/tool/registry.py:10-71`）：通用工具注册表，`register`/`get`/`execute`。全局单例 `get_global_registry()`（`registry.py:77-79`）。
- **internal tools**（`src/infra/tool/internal_registry.py`）：`build_internal_tools()`（`internal_registry.py:29-60`）聚合所有内置工具，按 settings 开关加载：
  - `image_generate`（`ENABLE_IMAGE_GENERATION`，`internal_registry.py:33-34`）
  - `audio_transcribe`（`ENABLE_AUDIO_TRANSCRIPTION`，`internal_registry.py:36-37`）
  - `dify_kb`（多个条件，`internal_registry.py:42-49`）
  - `env_var`（`ENABLE_SANDBOX`，`internal_registry.py:55-56`）
  - `persona_preset`、`team`（无条件，`internal_registry.py:58-59`）
- `get_internal_tools_for_user`（`internal_registry.py:174-203`）：按 per-tool policy（`MCPToolPolicy`）过滤 + `MCPToolWithRetry` 包装。policy 存 MongoDB `MCPToolPolicy`，支持 `disabled`/`allowed_roles`/`role_quotas`。
- 工具挂载到 agent：`SearchAgentContext.setup()`（`src/agents/search_agent/context.py:172-267`）和 `FastAgentContext.setup()` 调 `get_internal_tools_for_user`（`context.py:205-213`），结果 extend 进 `self.tools`。

#### `audio_transcribe_tool` —— 辅助模型工具的现成范式

文件：`src/infra/tool/audio_transcribe_tool.py`

```python
@tool
async def audio_transcribe(url, model=None, language=None, prompt=None, runtime=...) -> str:
    """Download one audio file by URL and transcribe it into text."""
    resolved_url = _resolve_url(url, runtime)
    client, card_model = await _build_client()
    if client is None:
        return await _json_dumps_result({"error": "AUDIO_TRANSCRIPTION_MODEL_ID is not configured"})
    resolved_model = model or card_model or "gpt-4o-mini-transcribe"
    # ... 下载音频到 SpooledTemporaryFile（限 _MAX_DOWNLOAD_BYTES=50MB）...
    result = await client.audio.transcriptions.create(file=(filename, file_obj), model=resolved_model, ...)
    # 返回 {success, text, url, filename, model, language?, duration?}
```

**关键设计点**（可作为 vision_analyze 工具的模板）：
- `_build_client`（`audio_transcribe_tool.py:85-104`）：用 `LLMClient.get_card_config(model_id, kind="transcribe")` 解析 kind=transcribe 卡片，拿 `api_key`/`api_base`/`model`，构造 `AsyncOpenAI` client。
- `settings.AUDIO_TRANSCRIPTION_MODEL_ID`（`audio_transcribe_tool.py:94`）：存卡片 ID，留空则禁用工具（返回明确 error）。
- 主模型以**工具调用**方式使用——主模型看到工具描述，决定是否调。
- 工具接收 URL（绝对 URL 或 `/api` 路径，`_resolve_url` 用 runtime base_url 补全，`audio_transcribe_tool.py:56-63`），自行下载（限流 `AUDIO_TRANSCRIPTION_MAX_DOWNLOAD_BYTES`）。

#### 设置层配置（`src/kernel/config/`）

- `definitions.py:664-684`：`ENABLE_AUDIO_TRANSCRIPTION`（bool，默认 False）、`AUDIO_TRANSCRIPTION_MODEL_ID`（string，默认 ""，`depends_on=ENABLE_AUDIO_TRANSCRIPTION`，`frontend_visible=True`）、`AUDIO_TRANSCRIPTION_MAX_DOWNLOAD_BYTES`（number，默认 50MB）。
- `base.py:322-325`：`Settings` 类字段定义。
- `SettingCategory.AUDIO_TRANSCRIPTION`（`src/kernel/schemas/setting.py:55`）：独立设置分类。
- 前端 i18n（`frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`）：`AUDIO_TRANSCRIPTION_MODEL_ID` 描述。
- 前端 `SettingsPanel.constants.ts:47,58`：`AUDIO_TRANSCRIPTION_MODEL_ID` 在 `MODEL_ID_SETTINGS` 列表，kind 映射 `transcribe`。
- 迁移：`src/kernel/config/service.py:89-93` 有 `AUDIO_TRANSCRIPTION_MODEL` → `AUDIO_TRANSCRIPTION_MODEL_ID` 的 backfill migration。

**这是辅助模型的完整配置范式**——vision 辅助模型可完全复刻：加 `ENABLE_VISION_ASSIST`/`VISION_ASSIST_MODEL_ID`/`VISION_ASSIST_MAX_BYTES` + `SettingCategory.VISION_ASSIST` + 前端 i18n。

#### 其他相关工具

- `reveal_file_tool`（`src/infra/tool/reveal_file_tool.py`）：读文件内容给模型。
- `transfer_file_tool`（`src/infra/tool/transfer_file_tool.py`）：文件传输。
- `image_generation_tool`（`src/infra/tool/image_generation_tool.py`）：图片生成（不是识别）。
- `upload_url_tool`（`src/infra/tool/upload_url_tool.py`）：沙箱上传 URL。
- 未发现 OCR 工具、vision_analyze 工具、image describe 工具（grep `transcribe`/`ocr`/`vision`/`whisper`/`image_describe` 仅命中 audio_transcribe_tool 和 image_generation_tool）。

---

### 5. 多 agent / 子 agent 架构

#### `src/agents/core/subagent_prompts.py`

- `SUBAGENT_PROMPT`：子 agent 系统提示。
- `MAIN_AGENT_PROMPT_SECTIONS`：主 agent 提示段。
- `build_role_subagent_section`、`get_memory_guide` 等。
- 子 agent 通过 `deepagents` 的 `SubAgent`/`CompiledSubAgent` 配置（见各节点 `custom_subagents`）。

#### `src/agents/team_agent/`

- `TeamAgentContext`（`src/agents/team_agent/context.py`）：扩展 SearchAgentContext，加 team 解析。
- `team_router_node`（`src/agents/team_agent/nodes.py:122-619`）：团队路由节点，按角色构建子 agent（`custom_subagents`，`nodes.py:386-466`）。
- 子 agent 机制：每个角色一个 `SubAgent`，有独立 `system_prompt`/`middleware`/`description`，主 agent 通过 `dispatch` 调用。

**关键**：LambChat 已有成熟的子 agent 调用模式（deepagents SubAgent），但子 agent 是"完整 chat 模型接管子任务"，不是"辅助模型做单步识别"。辅助 vision 模型更适合用工具模式或预处理模式，而非子 agent。

#### Agent 注册与工厂

- `BaseGraphAgent`（`src/agents/core/base.py:106-583`）：graph agent 基类，`stream` 方法是主入口。
- `AgentFactory.get(agent_id)`（`base.py:688-710`）：单例工厂。
- `_AGENT_REGISTRY`（`base.py:31`）：`register_agent` 装饰器注册。
- configurable 构造：`base.py:375-383`，`configurable` 含 `thread_id`/`presenter`/`**kwargs`（kwargs 含 `attachments`/`agent_options`/`persona_system_prompt` 等）。

---

### 6. 数据流总览（当前状态）

```
Web 端：
  upload.py:upload_file → S3 → attachment{key,url,name,type,mime_type,size}
  chat.py:validate_agent_model_access → _attach_resolved_model_options(agent_options, model)
    → agent_options[_resolved_supports_vision] = model.profile.supports_vision
  agent.stream(message, attachments=..., agent_options=...)
    → BaseGraphAgent._stream → graph.astream_events
      → {search,fast,team}_node
        → resolve_model_supports_vision（若未预解析）
        → if supports_vision: inline_image_attachments_as_data_urls
        → build_human_message(text, attachments, supports_vision=...)
          → supports_vision=True: image_url block（模型看到图）
          → supports_vision=False: _format_attachment_summary（只有 URL 文本）← BUG

WeCom 端：
  handler.py:_build_wecom_attachments → 下载媒体 → S3 → attachment
  handler.py:task_manager.submit(attachments=...)
    → execute_wecom_agent → agent.stream(...)
      → （同上节点链路）
      → wecom_agent_options 不经 validate_agent_model_access
        → _resolved_supports_vision 可能未设，节点内 fallback 查库
```

**bug 链路**：`deepseek-v4-flash` model card 的 `profile.supports_vision=false` → `_resolved_supports_vision=False` → `build_human_message` 把 image 附件降级为文本摘要（只有 `{APP_BASE_URL}/api/upload/file/{key}` URL）→ 主模型看不到图，尝试用 `reveal_file_tool` 等工具读 URL → 这些工具不是为读图片 URL 设计的 → 全部失败。

### Related Specs

- `.trellis/tasks/archive/2026-06/06-24-unify-llm-model-management-card-based-resolution-for-all-consumers/prd.md`：卡片化模型管理（引入 `kind` 字段、`AUDIO_TRANSCRIPTION_MODEL_ID` 卡片化、`fallback_model` 改为传 model_id）。
- `.trellis/tasks/archive/2026-06/06-24-unify-llm-model-management-card-based-resolution-for-all-consumers/research/model-card-dropdown-feasibility.md`：model card 字段清单。
- `.trellis/tasks/archive/2026-06/06-24-unify-llm-model-management-card-based-resolution-for-all-consumers/research/model-consumers-survey.md`：所有模型消费者清单（含 audio_transcribe_tool）。
- `.trellis/tasks/07-07-wecom-inbound-attachments/prd.md`：本次任务 PRD（WeCom 入站附件下载 + 透传，R1-R7）。

## Caveats / Not Found

- 未确认 WeCom 端 `wecom_agent_options` 是否在某处补 `_resolved_supports_vision`（grep 仅命中 chat.py 和三个节点）。若 WeCom 端不预解析，节点内 `resolve_model_supports_vision` 会查库——功能正确但多一次 DB 查询。
- 未深入读 `deepagents` 库的 SubAgent 实现细节（第三方库）。
- `ModelProfile.model_config = ConfigDict(extra="ignore")`（`model.py:19`）意味着未声明的 profile 字段会被忽略——若在 profile 加新字段需同步更新 schema。
