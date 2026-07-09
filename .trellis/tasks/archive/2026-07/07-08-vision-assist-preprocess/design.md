# Design: 辅助 vision 模型预处理注入

## 架构概览

```
用户消息 + image attachments
        │
        ▼
┌─────────────────────────────────────────────┐
│ agent 节点（search/fast/team）              │
│                                             │
│  supports_vision?                           │
│  ├─ True  → inline_image_attachments_as_    │
│  │         data_urls (URL/data_url, 现状)   │
│  │         → build_human_message            │
│  │           (image_url block, 直通)        │
│  └─ False → describe_image_attachments      │
│             (新增, 始终 base64)             │
│             → build_human_message           │
│               (vision_description 渲染)     │
└─────────────────────────────────────────────┘
        │
        ▼
    主模型推理
```

**核心边界**：vision assist 只在 `supports_vision=False` 分支生效。直通路径（`True`）零改动。

## 模块设计

### 新增 `src/agents/core/vision_assist.py`

核心预处理模块，职责单一：对 image attachment 生成 vision 描述，塞回 attachment dict。

```python
"""辅助 vision 模型预处理：主模型不支持 vision 时，用辅助 vision 模型描述图片。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from src.agents.core.node_utils import (
    _download_image_as_data_url,
    _is_image_attachment,
)
from src.infra.logging import get_logger
from src.infra.storage.s3.service import get_or_init_storage
from src.kernel.config import settings

logger = get_logger(__name__)

VISION_ASSIST_PROMPT = (
    "Describe the image in detail. If it contains text, transcribe it "
    "verbatim. Respond in the user's language."
)


async def describe_image(attachment: dict, *, max_bytes: int) -> str | None:
    """下载图片(base64) → 调辅助 vision 模型 → 返回描述文本。

    始终走 base64 data_url，不依赖 URL fetch（内网可用）。
    失败/超限返回 None（调用方降级）。
    """
    if not settings.ENABLE_VISION_ASSIST or not settings.VISION_ASSIST_MODEL_ID:
        return None

    key = attachment.get("key")
    if not key:
        return None

    # 预检 size，避免下载大图后才拒绝
    size = attachment.get("size")
    if isinstance(size, int) and size > max_bytes:
        logger.info(
            "[vision_assist] Skipping image size=%s > max=%s key=%s",
            size, max_bytes, key,
        )
        return None

    mime_type = attachment.get("mime_type") or attachment.get("mimeType") or "image/jpeg"

    try:
        storage = await get_or_init_storage()
        data_url = await _download_image_as_data_url(
            storage, key, mime_type, max_bytes=max_bytes,
        )
    except Exception as e:
        logger.warning("[vision_assist] Download failed key=%s: %s", key, e)
        return None

    if data_url is None:
        # 下载后超限或空
        logger.info("[vision_assist] data_url is None (oversized/empty) key=%s", key)
        return None

    try:
        from src.infra.llm.client import LLMClient

        llm = await LLMClient.get_model(model_id=settings.VISION_ASSIST_MODEL_ID)
        msg = HumanMessage(content=[
            {"type": "text", "text": VISION_ASSIST_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ])
        resp = await llm.ainvoke([msg])
        content = getattr(resp, "content", None)
        if isinstance(content, list):
            # 某些模型返回 content list，取文本部分
            content = "".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if not content or not str(content).strip():
            logger.warning("[vision_assist] Empty description key=%s", key)
            return None
        return str(content)
    except Exception as e:
        logger.warning("[vision_assist] Vision model call failed key=%s: %s", key, e)
        return None


async def describe_image_attachments(
    attachments: list[dict] | None,
    *,
    supports_vision: bool,
) -> list[dict]:
    """对 image attachment 并发生成 vision 描述，塞回 attachment dict。

    直通条件（不处理）：supports_vision=True / 未启用 / 无 model_id / 无附件。
    返回的 list 顺序与输入一致；非 image 附件原样保留。
    """
    if not attachments:
        return attachments or []
    if supports_vision:
        return attachments
    if not settings.ENABLE_VISION_ASSIST or not settings.VISION_ASSIST_MODEL_ID:
        return attachments

    max_bytes = int(settings.VISION_ASSIST_MAX_BYTES)

    async def _enrich(att: dict) -> dict:
        if not _is_image_attachment(att):
            return att
        desc = await describe_image(att, max_bytes=max_bytes)
        if desc:
            return {**att, "vision_description": desc}
        return att

    return await asyncio.gather(*[_enrich(a) for a in attachments])
```

**设计要点**：
- `describe_image` 单图描述，失败返回 None（降级信号）。
- `describe_image_attachments` 并发多图，顺序保持。
- 不复用 `inline_image_attachments_as_data_urls`（它优先 URL fetch，内网死）；直接调 `_download_image_as_data_url` 走 storage 下载 + base64。
- 辅助模型 `content` 可能是 str 或 list（某些 provider），兼容处理。
- `settings` 延迟到函数内读取（测试时 monkeypatch 生效）。

### 改 `src/agents/core/node_utils.py`

#### `_format_attachment_summary`（行 223-257）

image attachment 有 `vision_description` 时，渲染带描述的文本块：

```python
def _format_attachment_summary(text: str, attachments: list[dict]) -> str:
    enhanced_text = text
    if not attachments:
        return enhanced_text

    enhanced_text += "\n\n---\n**User Uploaded Attachments:**"

    for attachment in attachments:
        url = attachment.get("url", "")
        name = attachment.get("name", "未知文件")
        file_type = attachment.get("type", "document")
        mime_type = attachment.get("mime_type") or attachment.get("mimeType") or ""
        size = attachment.get("size", 0)
        vision_description = attachment.get("vision_description", "")

        # 有 vision 描述的图片：渲染描述块（URL 可为空，内网 base64 已消费）
        if vision_description:
            enhanced_text += f"\n\n**[{name}]**"
            if mime_type:
                enhanced_text += f"\n- 类型: {file_type} ({mime_type})"
            else:
                enhanced_text += f"\n- 类型: {file_type}"
            if size:
                size_str = _format_size(size)
                if size_str:
                    enhanced_text += f"\n- 大小: {size_str}"
            enhanced_text += f"\n- 视觉描述:\n{vision_description}"
            if url:
                enhanced_text += f"\n- 链接: {url}"
            continue

        # 无描述（文档/降级图片）：维持现状
        if not url:
            continue
        # ... 现有 size_str + 渲染逻辑
```

**提取 `_format_size` 辅助函数**（复用现有 size 格式化逻辑，避免重复）。

#### `build_human_message`（行 260-310）

`supports_vision=False` 分支：image attachment 有 `vision_description` 时仍进 `text_summary_attachments`（走带描述的摘要）。**无需改分支逻辑**——`_is_image_attachment` 判定 + `_format_attachment_summary` 渲染即可。`supports_vision=True` 分支完全不动。

### 改三个 agent 节点

统一模式：`if supports_vision:` 之后加 `else` 分支。

#### `src/agents/search_agent/nodes.py:311-316`

```python
if supports_vision:
    attachments = await inline_image_attachments_as_data_urls(
        attachments,
        base_url=configurable.get("base_url", ""),
    )
else:
    attachments = await describe_image_attachments(
        attachments,
        supports_vision=supports_vision,
    )
new_message = build_human_message(user_input, attachments, supports_vision=supports_vision)
```

#### `src/agents/fast_agent/nodes.py:278-283` / `team_agent/nodes.py:546-551`

同上模式（team 节点在 subagent 路径也要覆盖）。

**import**：`from src.agents.core.vision_assist import describe_image_attachments`。

### 配置层

#### `src/kernel/schemas/setting.py`（行 55 附近）

```python
class SettingCategory(str, Enum):
    # ... 现有
    VISION_ASSIST = "vision_assist"
```

#### `src/kernel/config/base.py`（行 325 附近，AUDIO_TRANSCRIPTION 之后）

```python
ENABLE_VISION_ASSIST: bool = False
VISION_ASSIST_MODEL_ID: str = ""
VISION_ASSIST_MAX_BYTES: int = 10 * 1024 * 1024
```

#### `src/kernel/config/definitions.py`（AUDIO_TRANSCRIPTION 段之后）

三个 setting 定义：`ENABLE_VISION_ASSIST`（toggle）、`VISION_ASSIST_MODEL_ID`（model_id 选择，`depends_on=ENABLE_VISION_ASSIST`，`frontend_visible=True`）、`VISION_ASSIST_MAX_BYTES`（number，`depends_on=ENABLE_VISION_ASSIST`）。category=`SettingCategory.VISION_ASSIST`。

### 前端

#### `frontend/src/components/panels/SettingsPanel.constants.ts`（行 47, 58）

`MODEL_ID_SETTINGS` 加 `VISION_ASSIST_MODEL_ID`，kind 映射 `"chat"`。**需确认**：前端能否过滤 `supports_vision=true` 的 chat 卡片（当前 audio_transcribe 映射 `transcribe` 无过滤；vision 需只显示 vision 卡片）。若前端无过滤能力，下拉显示所有 chat 卡片，管理员自行选 vision 卡片——可接受 MVP。

#### `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`

加 `ENABLE_VISION_ASSIST` / `VISION_ASSIST_MODEL_ID` / `VISION_ASSIST_MAX_BYTES` 描述 + `SettingCategory.VISION_ASSIST` 分类标题。

## 数据流

```
attachment = {id, key, name, type:image, mime_type, size, url}
                                                          │
                          describe_image_attachments     │
                          ├─ _is_image_attachment? yes   │
                          ├─ describe_image:            │
                          │   ├─ pre-check size          │
                          │   ├─ storage.download → b64  │
                          │   └─ llm.ainvoke → desc      │
                          └─ attachment["vision_description"] = desc
                                                          │
                          build_human_message             │
                          └─ _format_attachment_summary   │
                              └─ 渲染带描述文本块         ▼
```

## 兼容性与降级

| 场景 | 行为 |
|---|---|
| `supports_vision=True` | 直通 `image_url` block，零改动 |
| `ENABLE_VISION_ASSIST=false` | `describe_image_attachments` 直通返回，现状文本摘要 |
| `VISION_ASSIST_MODEL_ID=""` | 同上直通 |
| 辅助模型调用失败 | `describe_image` 返回 None，attachment 无 `vision_description`，走现状纯 URL 摘要 |
| 图片超限 | 预检 size 跳过，同上降级 |
| 下载失败 | 同上降级 |
| 非图片附件 | `_is_image_attachment=False`，原样保留 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 辅助模型延迟（每图 1-3s） | `asyncio.gather` 并发；WeCom 5s 回调由 `send_thinking_placeholder` 兜底 |
| base64 payload 大（10MB→13MB） | 主流 vision API 可接受；超限降级 |
| 辅助模型 `content` 格式不一 | 兼容 str / list |
| WeCom `_resolved_supports_vision` 未预解析 | 节点内 fallback 查库（多一次 DB，可接受） |
| 历史图片重复描述（成本） | MVP 接受；二期加缓存 |

## 测试策略

`tests/agents/core/test_vision_assist.py`：
- `test_describe_image_attachments_passthrough_when_supports_vision`：直通
- `test_describe_image_attachments_passthrough_when_disabled`：未启用直通
- `test_describe_image_success`：mock storage + LLMClient，验证 `vision_description` 注入
- `test_describe_image_llm_failure_degrades`：LLM 抛异常 → None → 无描述
- `test_describe_image_oversize_degrades`：size > max_bytes → None
- `test_describe_image_download_failure_degrades`：storage 下载失败 → None
- `test_describe_image_non_image_passthrough`：文档附件原样保留
- `test_describe_image_attachments_concurrent`：多图并发，顺序保持

`tests/agents/core/test_node_utils.py`（若存在，扩展）：
- `test_format_attachment_summary_with_vision_description`：渲染描述块
- `test_format_attachment_summary_without_vision_description`：现状不变（回归）

## 不改动清单

- `inline_image_attachments_as_data_urls`（直通路径用，不改）
- `resolve_model_supports_vision`（复用）
- `build_human_message` 的 `supports_vision=True` 分支
- `audio_transcribe_tool`（音频已覆盖）
- model card schema（不新增字段）
- WeCom handler 附件构造逻辑（描述在节点层做）
