"""辅助 vision 模型预处理：主模型不支持 vision 时，用辅助 vision 模型描述图片。

主模型 ``supports_vision=False`` 且启用 vision assist 时，在 ``build_human_message``
之前对 image attachment 调辅助 vision 模型生成描述文本，塞回
``attachment["vision_description"]``，由 ``_format_attachment_summary`` 渲染。

始终走 base64 data_url（storage 下载 → base64 → 辅助模型），不依赖 URL fetch
——内网/k8s 部署环境对外部 vision 模型不可达的 URL 不生效。

直通条件（不处理）：``supports_vision=True`` / 未启用 / 无 model_id / 非图片附件。
失败（下载失败/超限/模型错误/空描述）返回 None，调用方降级为现状纯 URL 文本摘要。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from src.agents.core.node_utils import (
    _download_image_as_data_url,
    _is_image_attachment,
)
from src.infra.agent.attachments import attachment_is_unavailable, attachment_reupload_context
from src.infra.logging import get_logger

logger = get_logger(__name__)

VISION_ASSIST_PROMPT = (
    "Describe the image in detail. If it contains text, transcribe it "
    "verbatim. Respond in the user's language."
)


def _vision_assist_enabled() -> bool:
    """Whether vision assist is configured and enabled."""
    from src.kernel.config import settings

    return bool(
        getattr(settings, "ENABLE_VISION_ASSIST", False)
        and getattr(settings, "VISION_ASSIST_MODEL_ID", "")
    )


def _vision_assist_max_bytes() -> int:
    from src.kernel.config import settings

    return int(getattr(settings, "VISION_ASSIST_MAX_BYTES", 10 * 1024 * 1024))


async def describe_image(attachment: dict, *, max_bytes: int) -> str | None:
    """下载图片(base64) → 调辅助 vision 模型 → 返回描述文本。

    始终走 base64 data_url，不依赖 URL fetch（内网可用）。
    失败/超限返回 None（调用方降级）。
    """
    key = attachment.get("key")
    if attachment_is_unavailable(attachment):
        return None
    if not key:
        return None

    # 预检 size，避免下载大图后才拒绝
    size = attachment.get("size")
    if isinstance(size, int) and size > max_bytes:
        logger.info(
            "[vision_assist] Skipping image size=%s > max=%s key=%s",
            size,
            max_bytes,
            key,
        )
        return None

    mime_type = (
        attachment.get("mime_type") or attachment.get("mimeType") or "image/jpeg"
    )

    try:
        from src.infra.storage.s3.service import get_or_init_storage

        storage = await get_or_init_storage()
        data_url = await _download_image_as_data_url(
            storage,
            key,
            mime_type,
            max_bytes=max_bytes,
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
        from src.kernel.config import settings

        llm = await LLMClient.get_model(model_id=settings.VISION_ASSIST_MODEL_ID)
        msg = HumanMessage(
            content=[
                {"type": "text", "text": VISION_ASSIST_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        )
        resp = await llm.ainvoke([msg])
        content: Any = getattr(resp, "content", None)
        if isinstance(content, list):
            # 某些 provider 返回 content list，取文本部分拼接
            content = "".join(
                block.get("text", "")
                for block in content
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

    直通条件（不处理）：``supports_vision=True`` / 未启用 / 无 model_id / 无附件。
    返回的 list 顺序与输入一致；非 image 附件原样保留。
    """
    if not attachments:
        return attachments or []
    if supports_vision:
        return attachments
    if not _vision_assist_enabled():
        return attachments

    max_bytes = _vision_assist_max_bytes()

    async def _enrich(att: dict) -> dict:
        if not _is_image_attachment(att):
            return att
        if attachment_is_unavailable(att):
            return {
                **att,
                "reupload_context": attachment_reupload_context(att),
            }
        desc = await describe_image(att, max_bytes=max_bytes)
        if desc:
            return {**att, "vision_description": desc}
        return att

    return list(await asyncio.gather(*[_enrich(a) for a in attachments]))
