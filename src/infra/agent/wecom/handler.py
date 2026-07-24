"""
WeCom (企业微信) 消息处理器模块

处理 WeCom 消息的 Agent 执行和响应。
按 aibotid 路由到 persona preset，session 归属 sender_id。
支持流式回复（原生 WebSocket 流式）、5 秒思考占位、6 分钟超时回退。
"""

import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Optional
from urllib.parse import quote

from src.infra.agent.wecom.collector import WeComResponseCollector
from src.infra.agent.wecom.manager import WeComBotManager
from src.infra.logging import get_logger
from src.infra.storage.s3.service import get_or_init_storage
from src.infra.upload.file_record import FileRecordStorage
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.wecom import WECOM_DEFAULT_SEGMENT_TARGET_CHARS

logger = get_logger(__name__)

# 单例 file_record 存储（与 Web 端 upload.py 共享同一 collection）
_file_record_storage = FileRecordStorage()

# ── 常量 ──────────────────────────────────────────────────────────────

# Redis key prefix for WeCom chat→session mapping
WECOM_SESSION_KEY_PREFIX = "wecom:session:"
# Redis key prefix for WeCom run_id→session_id mapping (for feedback lookup)
WECOM_RUN_SESSION_KEY_PREFIX = "wecom:run_session:"
_WECOM_RUN_SESSION_TTL_SECONDS = 25 * 3600  # ~25h, longer than session TTL

# 事件类型（与 agent stream 事件一致）
EVENT_MESSAGE_CHUNK = "message:chunk"
EVENT_THINKING = "thinking"
EVENT_TOOL_START = "tool:start"
EVENT_TOOL_RESULT = "tool:result"
EVENT_DONE = "done"


# ── Session 辅助函数 ──────────────────────────────────────────────────


def _wecom_session_key(aibotid: str, chat_type: str, chat_id: str) -> str:
    """Return a bot-scoped session key so persona conversations cannot collide."""
    return f"{WECOM_SESSION_KEY_PREFIX}v2:{aibotid}:{chat_type}:{chat_id}"


async def _get_wecom_session_id(
    chat_id: str,
    ttl_hours: int | None = 24,
    *,
    aibotid: str = "legacy",
    chat_type: str = "single",
) -> str:
    """获取 WeCom 聊天对应的当前 session ID，如果不存在则创建默认的"""
    from src.infra.storage.redis import RedisStorage

    storage = RedisStorage()
    key = _wecom_session_key(aibotid, chat_type, chat_id)
    session_id = await storage.get(key)

    if session_id is None:
        session_id = f"wecom_{aibotid}_{chat_type}_{chat_id}"
        ttl_seconds = (ttl_hours or 0) * 3600 + 3600  # +1h buffer
        if ttl_hours:  # 0 means no expiry
            await storage.set(key, session_id, ttl=ttl_seconds)
        else:
            await storage.set(key, session_id)

    return session_id


async def _create_new_wecom_session(
    chat_id: str,
    ttl_hours: int | None = 24,
    *,
    aibotid: str = "legacy",
    chat_type: str = "single",
) -> str:
    """为 WeCom 聊天创建新的 session ID"""
    from src.infra.storage.redis import RedisStorage

    storage = RedisStorage()
    key = _wecom_session_key(aibotid, chat_type, chat_id)

    # 使用时间戳生成唯一的 session ID
    timestamp = int(time.time())
    session_id = f"wecom_{aibotid}_{chat_type}_{chat_id}_{timestamp}"

    ttl_seconds = (ttl_hours or 0) * 3600 + 3600  # +1h buffer
    if ttl_hours:  # 0 means no expiry
        await storage.set(key, session_id, ttl=ttl_seconds)
    else:
        await storage.set(key, session_id)

    logger.info("[WeCom] Created new session for chat %s: %s", chat_id, session_id)
    return session_id


async def _store_run_session_mapping(run_id: str, session_id: str) -> None:
    """Store run_id → session_id mapping in Redis for later feedback lookup."""
    from src.infra.storage.redis import RedisStorage

    storage = RedisStorage()
    key = f"{WECOM_RUN_SESSION_KEY_PREFIX}{run_id}"
    await storage.set(key, session_id, ttl=_WECOM_RUN_SESSION_TTL_SECONDS)


async def _lookup_session_by_run_id(run_id: str) -> str | None:
    """Look up session_id from Redis by run_id (stored at reply time)."""
    from src.infra.storage.redis import RedisStorage

    storage = RedisStorage()
    key = f"{WECOM_RUN_SESSION_KEY_PREFIX}{run_id}"
    value = await storage.get(key)
    return str(value) if value is not None else None


async def _reconcile_wecom_session_owner(
    session_id: str,
    wecom_userid: str,
    mapped_user_id: str,
) -> None:
    """将历史 WeCom 会话 owner 从企微 userid 迁移为昆小智 user_id。

    旧版本曾用 sender_id（工号）作为 session.user_id；映射上线后 submit 使用
    mapped_user_id，ensure_session 会拒绝 owner 不一致。
    """
    if not session_id or wecom_userid == mapped_user_id:
        return
    try:
        from src.infra.session.storage import SessionStorage

        storage = SessionStorage()
        existing = await storage.get_by_session_id(session_id)
        if not existing or not existing.user_id:
            return
        if existing.user_id == mapped_user_id:
            return
        if existing.user_id != wecom_userid:
            return
        migrated = await storage.set_user_id_if_matches(session_id, wecom_userid, mapped_user_id)
        if migrated:
            logger.info(
                "[WeCom] Migrated session %s owner %s → %s",
                session_id,
                wecom_userid,
                mapped_user_id,
            )
    except Exception as e:
        logger.warning(
            "[WeCom] Failed to reconcile session owner for %s: %s",
            session_id,
            e,
        )


async def _reconcile_wecom_channel_project(
    wecom_userid: str,
    mapped_user_id: str,
    preset_name: str,
) -> str | None:
    """确保 persona 同名 channel 项目挂在昆小智 user_id 下，返回 project_id。"""
    if wecom_userid == mapped_user_id:
        try:
            from src.infra.folder.storage import get_project_storage

            project = await get_project_storage().get_or_create_by_name(
                mapped_user_id, preset_name, project_type="channel", icon="💬"
            )
            return project.id
        except Exception as e:
            logger.warning("[WeCom] Failed to resolve channel project: %s", e)
            return None
    try:
        from src.infra.folder.storage import get_project_storage

        storage = get_project_storage()
        migrated = await storage.migrate_owner_by_name_and_type(
            wecom_userid, mapped_user_id, preset_name, project_type="channel"
        )
        if migrated:
            logger.info(
                "[WeCom] Using channel project %s for preset %s (user %s)",
                migrated.id,
                preset_name,
                mapped_user_id,
            )
            return migrated.id
        project = await storage.get_or_create_by_name(
            mapped_user_id, preset_name, project_type="channel", icon="💬"
        )
        return project.id
    except Exception as e:
        logger.warning("[WeCom] Failed to reconcile channel project: %s", e)
        return None


async def _bind_wecom_session_to_project(
    session_id: str,
    mapped_user_id: str,
    project_id: str | None,
) -> None:
    """已存在的 WeCom 会话不会走 ensure_session 创建，需显式写入 project_id。"""
    if not project_id:
        return
    try:
        from src.infra.session.storage import SessionStorage

        storage = SessionStorage()
        existing = await storage.get_by_session_id(session_id)
        if not existing or existing.user_id != mapped_user_id:
            return
        current_pid = (existing.metadata or {}).get("project_id")
        if current_pid == project_id:
            return
        await storage.move_to_project(session_id, mapped_user_id, project_id)
        logger.info(
            "[WeCom] Bound session %s to project %s",
            session_id,
            project_id,
        )
    except Exception as e:
        logger.warning(
            "[WeCom] Failed to bind session %s to project: %s",
            session_id,
            e,
        )


async def _persist_wecom_session_config(
    *,
    session_id: str,
    run_id: str,
    agent_id: str,
    agent_request: Any,
    agent_options: dict[str, Any],
    project_id: str | None,
) -> bool:
    """Persist the same Persona restore metadata used by Web chat."""
    try:
        from src.api.routes.chat import build_conversation_config
        from src.infra.session.manager import SessionManager

        agent_request.agent_options = agent_options or None
        agent_request.project_id = project_id
        conversation_config = build_conversation_config(
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            request=agent_request,
            language="zh-CN",
        )
        saved = await SessionManager().update_session_metadata(session_id, conversation_config)
        if not saved:
            logger.warning("[WeCom] Failed to persist Persona config for session %s", session_id)
        return saved
    except Exception:
        logger.exception(
            "[WeCom] Failed to build or persist Persona config for session %s",
            session_id,
        )
        return False


# ── Agent 执行 ────────────────────────────────────────────────────────


async def execute_wecom_agent(
    session_id: str,
    agent_id: str,
    message: str,
    user_id: str,
    presenter: Optional[Any] = None,
    disabled_tools: list[str] | None = None,
    agent_options: dict | None = None,
    attachments: list[dict] | None = None,
    disabled_skills: list[str] | None = None,
    enabled_skills: list[str] | None = None,
    persona_system_prompt: str | None = None,
    disabled_mcp_tools: list[str] | None = None,
    team_id: str | None = None,
    active_goal: dict | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """执行 Agent 并生成事件流"""
    from src.agents.core.base import AgentFactory
    from src.infra.task.exceptions import TaskInterruptedError

    agent = await AgentFactory.get(agent_id)
    run_id = presenter.run_id if presenter else None

    started_at: str | None = None
    if active_goal is not None:
        started_at = datetime.now(timezone.utc).isoformat()
        yield {"event": "goal:start", "data": {"goal": active_goal, "started_at": started_at}}

    try:
        async for event in agent.stream(
            message,
            session_id,
            user_id=user_id,
            presenter=presenter,
            disabled_tools=disabled_tools,
            agent_options=agent_options,
            attachments=attachments,
            disabled_skills=disabled_skills,
            enabled_skills=enabled_skills,
            persona_system_prompt=persona_system_prompt,
            disabled_mcp_tools=disabled_mcp_tools,
            team_id=team_id,
            active_goal=active_goal,
            goal_started_at=started_at,
        ):
            yield event
    except (asyncio.CancelledError, TaskInterruptedError):
        if run_id:
            await agent.close(run_id)
        if active_goal is not None:
            ended_at = datetime.now(timezone.utc).isoformat()
            yield {
                "event": "goal:end",
                "data": {"goal": active_goal, "started_at": started_at, "ended_at": ended_at},
            }
        raise


# ── 入站附件处理（下载 → S3 → attachment）────────────────────────────


# 扩展名 → MIME 类型映射（用于 WeCom 入站附件构造；缺失时回退到默认）。
_EXTENSION_MIME_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "xml": "application/xml",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
    "amr": "audio/amr",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
}


def _mime_type_for(filename: str, *, default: str) -> str:
    """根据文件名扩展名推断 MIME 类型，缺失时返回 default。"""
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext in _EXTENSION_MIME_TYPES:
            return _EXTENSION_MIME_TYPES[ext]
    return default


def _app_base_url_configured() -> bool:
    """Whether settings.APP_BASE_URL is a real, usable http(s) URL.

    Guards against the default placeholder value (e.g. an example comment)
    being used to build attachment URLs — that would produce broken URLs the
    vision model cannot fetch.
    """
    base_url = (getattr(settings, "APP_BASE_URL", "") or "").strip()
    return base_url.startswith("http://") or base_url.startswith("https://")


def _attachment_url_from_key(key: str) -> str:
    """构造附件访问 URL：{APP_BASE_URL}/api/upload/file/{key}。

    与 Web 端 `_build_upload_response` 一致，优先使用 settings.APP_BASE_URL。
    """
    base_url = (getattr(settings, "APP_BASE_URL", "") or "").rstrip("/")
    quoted_key = quote(key.lstrip("/"), safe="/")
    return f"{base_url}/api/upload/file/{quoted_key}"


# attachment_type → S3 folder 与 MIME 默认值（扩展名未命中时回退）
_ATTACHMENT_DEFAULTS: dict[str, tuple[str, str]] = {
    "image": ("image", "image/jpeg"),
    "document": ("document", "application/octet-stream"),
    "audio": ("audio", "audio/amr"),
}


async def _build_single_attachment(
    bot: Any,
    *,
    url: str,
    aes_key: str,
    file_name: str,
    attachment_type: str,
    owner_id: str,
) -> dict | None:
    """下载单个 WeCom 媒体 → 上传 S3 → 写 file_record → 构造 attachment dict。

    与 Web 端 AttachmentSchema 对齐。attachment_type ∈ "image"|"document"|"audio"。
    下载/S3 上传/file_record 任一失败降级返回 None（记日志，不阻断消息处理）。
    """
    folder, default_mime = _ATTACHMENT_DEFAULTS.get(
        attachment_type, ("document", "application/octet-stream")
    )
    mime_type = _mime_type_for(file_name, default=default_mime)

    if not url:
        logger.warning("[WeCom] Empty media URL, skipping attachment for owner=%s", owner_id)
        return None

    try:
        file_bytes, _ = await bot.download_media_file(url, aes_key or "")
    except Exception as e:
        logger.error("[WeCom] Failed to download inbound media: %s", e, exc_info=True)
        return None
    if not file_bytes:
        logger.warning("[WeCom] Empty inbound media bytes, skipping attachment")
        return None

    try:
        storage = await get_or_init_storage()
        upload_result = await storage.upload_bytes(
            file_bytes,
            folder=folder,
            filename=file_name or f"{uuid.uuid4().hex}",
            content_type=mime_type,
            metadata={"uploaded_by": owner_id, "source": "wecom_inbound"},
            skip_size_limit=True,
        )
    except Exception as e:
        logger.error("[WeCom] Failed to upload media to S3: %s", e, exc_info=True)
        return None

    storage_key = upload_result.key
    size = upload_result.size
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    logger.info(
        "[WeCom] Media uploaded to S3: key=%s size=%d mime=%s owner=%s",
        storage_key,
        size,
        mime_type,
        owner_id,
    )

    # file_record 写失败不阻断附件传递（去重失效但 agent 仍可见附件）
    try:
        await _file_record_storage.create(
            file_hash=file_hash,
            key=storage_key,
            name=file_name or "unknown",
            mime_type=mime_type,
            size=size,
            category=attachment_type,
            uploaded_by=owner_id,
        )
    except Exception as e:
        logger.warning("[WeCom] Failed to write file_record for key=%s: %s", storage_key, e)

    return {
        "id": uuid.uuid4().hex,
        "key": storage_key,
        "name": file_name or "unknown",
        "type": attachment_type,
        "mime_type": mime_type,
        "size": size,
        # APP_BASE_URL 未配置时留空：agent 链会按 live request base_url 重建，
        # 或回退到内联 data_url。填占位 URL 会让 vision 模型 fetch 失败。
        "url": _attachment_url_from_key(storage_key) if _app_base_url_configured() else "",
    }


async def _build_wecom_attachments(
    manager: WeComBotManager,
    aibotid: str,
    metadata: dict,
    owner_id: str,
) -> list[dict] | None:
    """根据 metadata 构造 WeCom 入站附件列表。失败降级返回 None。

    返回 None 表示不走附件链路（保持占位符行为）；返回空列表表示无需附件
    （如 voice 已有转写）；返回非空列表表示有附件需透传。
    """
    msg_type = metadata.get("msg_type", "")
    bot = manager.find_bot(aibotid)
    if bot is None:
        logger.warning("[WeCom] No bot for aibotid=%s, cannot build attachments", aibotid)
        return None

    attachments: list[dict] = []

    try:
        if msg_type == "image":
            att = await _build_single_attachment(
                bot,
                url=metadata.get("pic_url", ""),
                aes_key=metadata.get("aes_key", ""),
                file_name="image.jpg",  # WeCom 不返回图片文件名
                attachment_type="image",
                owner_id=owner_id,
            )
            if att:
                attachments.append(att)

        elif msg_type == "file":
            file_name = metadata.get("file_name", "") or "file"
            att = await _build_single_attachment(
                bot,
                url=metadata.get("file_url", ""),
                aes_key=metadata.get("aes_key", ""),
                file_name=file_name,
                attachment_type="document",
                owner_id=owner_id,
            )
            if att:
                attachments.append(att)

        elif msg_type == "voice":
            # 有转写 → 用转写文本做 content，不下载语音
            if metadata.get("voice_transcribed"):
                return None
            att = await _build_single_attachment(
                bot,
                url=metadata.get("voice_url", ""),
                aes_key=metadata.get("aes_key", ""),
                file_name="voice.amr",
                attachment_type="audio",
                owner_id=owner_id,
            )
            if att:
                attachments.append(att)

        elif msg_type == "mixed":
            mixed_items = metadata.get("mixed_media_items") or []
            for item in mixed_items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")
                url = item.get("url", "")
                if not url:
                    continue
                if item_type == "image":
                    att_type, file_name = "image", "image.jpg"
                elif item_type == "file":
                    att_type = "document"
                    file_name = item.get("file_name", "") or "file"
                else:
                    continue
                att = await _build_single_attachment(
                    bot,
                    url=url,
                    aes_key=item.get("aes_key", ""),
                    file_name=file_name,
                    attachment_type=att_type,
                    owner_id=owner_id,
                )
                if att:
                    attachments.append(att)

        # video 和未知类型：不构造附件，保持占位符行为
    except Exception as e:
        logger.error(
            "[WeCom] Failed to build attachments for msg_type=%s: %s",
            msg_type,
            e,
            exc_info=True,
        )
        return None

    return attachments or None


# ── 消息处理器工厂 ────────────────────────────────────────────────────


def create_wecom_message_handler(
    manager: WeComBotManager,
) -> Callable:
    """
    创建 WeCom 消息处理器

    Args:
        manager: WeCom Bot 管理器
    """
    from src.infra.task.manager import get_task_manager

    async def wecom_message_handler(
        sender_id: str,
        chat_id: str,
        content: str,
        metadata: dict,
    ) -> None:
        """处理 WeCom 消息 — 按 aibotid 路由到 persona preset"""
        original_message_id = metadata.get("message_id")
        aibotid = metadata.get("aibotid", "")
        delivery_chat_id = chat_id

        try:
            logger.info(
                "[WeCom] Processing message from %s, aibotid=%s: %s...",
                sender_id,
                aibotid,
                content[:50],
            )

            sender_id_from_msg = metadata.get("sender_id")
            chat_type_from_msg = metadata.get("chat_type")
            reply_to_msgid = original_message_id

            # ── 路由：aibotid → preset_id ──────────────────────────
            preset_id = manager.get_preset_id_for_aibotid(aibotid)
            if not preset_id:
                logger.warning(
                    "[WeCom] No preset mapping for aibotid=%s, skipping message",
                    aibotid,
                )
                return

            wecom_config = manager.get_config_for_aibotid(aibotid)
            if not wecom_config:
                logger.warning(
                    "[WeCom] No config for aibotid=%s, skipping message",
                    aibotid,
                )
                return

            # ── WeCom userid → 昆小智 user_id mapping ───────────────
            # WeCom userid (e.g. employee ID "10325") is used as the
            # 昆小智 username during registration. Look up the real
            # 昆小智 user_id (MongoDB ObjectId) via username.
            session_owner_id: str | None = None
            try:
                from src.infra.user.storage import UserStorage

                user_storage = UserStorage()
                wecom_user_obj = await user_storage.get_by_username(sender_id)
                if wecom_user_obj:
                    session_owner_id = wecom_user_obj.id
                    logger.info(
                        "[WeCom] Mapped sender %s → user_id %s",
                        sender_id,
                        session_owner_id,
                    )
                else:
                    logger.warning(
                        "[WeCom] No user found for WeCom username=%s",
                        sender_id,
                    )
                    await manager.send_message(
                        aibotid,
                        delivery_chat_id,
                        "当前企业微信账号尚未绑定系统用户，请先在网页端完成账号注册或联系管理员。",
                    )
                    return
            except Exception as e:
                logger.warning(
                    "[WeCom] Failed to lookup user for sender %s: %s",
                    sender_id,
                    e,
                )
                await manager.send_message(
                    aibotid,
                    delivery_chat_id,
                    "暂时无法确认企业微信账号身份，请稍后重试。",
                )
                return

            # ── Persona resolve ─────────────────────────────────────
            from src.api.routes.chat import resolve_persona_request
            from src.kernel.schemas.agent import AgentRequest
            from src.kernel.schemas.user import TokenPayload

            agent_request = AgentRequest(
                persona_preset_id=preset_id,
                message=content,
            )
            # Build a virtual TokenPayload with the mapped user_id
            wecom_user = TokenPayload(
                sub=session_owner_id,
                username=sender_id,
                roles=[],
                permissions=[],
            )
            await resolve_persona_request(agent_request, wecom_user)

            from src.infra.persona_preset.dify_kb_agent_options import (
                apply_dify_kb_dataset_ids_to_agent_options,
            )

            wecom_agent_options = apply_dify_kb_dataset_ids_to_agent_options(
                {},
                persona_snapshot=agent_request.persona_snapshot,
            )

            # The persona snapshot and system prompt are now filled
            persona_system_prompt = agent_request.persona_system_prompt
            enabled_skills = agent_request.enabled_skills

            # Resolve agent from persona preferred_agent_id (same authority as Web).
            # Missing/invalid preferred falls back to fast.
            from src.agents.core.persona import resolve_persona_agent_id

            preferred_agent_id = (
                agent_request.persona_snapshot.preferred_agent_id
                if agent_request.persona_snapshot is not None
                else None
            )
            agent_to_use = resolve_persona_agent_id(None, preferred_agent_id)

            # ── WeCom 行为配置 ─────────────────────────────────────
            stream_reply = wecom_config.get("stream_reply", True)
            send_thinking_message = wecom_config.get("send_thinking_message", True)
            segmented_reply = wecom_config.get("segmented_reply", True)
            segment_target_chars = wecom_config.get(
                "segment_target_chars", WECOM_DEFAULT_SEGMENT_TARGET_CHARS
            )
            session_ttl_hours = wecom_config.get("session_ttl_hours", 24)

            # ── Project 自动创建 / 迁移（Web 侧按 user_id + metadata.project_id 展示）──
            preset_name = "WeCom"
            try:
                from src.infra.persona_preset.manager import PersonaPresetManager

                preset_manager = PersonaPresetManager()
                preset = await preset_manager.get_preset(
                    preset_id,
                    user_id="",
                    is_admin=True,
                )
                if preset:
                    preset_name = preset.name
            except Exception as e:
                logger.warning("[WeCom] Failed to load preset name for %s: %s", preset_id, e)

            project_id = await _reconcile_wecom_channel_project(
                sender_id, session_owner_id, preset_name
            )

            # ── 处理 /new 命令 - 严格匹配 ─────────────────────────
            if content.strip() == "/new":
                new_session_id = await _create_new_wecom_session(
                    chat_id,
                    ttl_hours=session_ttl_hours,
                    aibotid=aibotid,
                    chat_type=chat_type_from_msg or "single",
                )
                await manager.send_message(
                    aibotid,
                    delivery_chat_id,
                    "✅ 已创建新对话，请发送消息开始",
                )
                logger.info("[WeCom] New session created for chat %s: %s", chat_id, new_session_id)
                return

            # 获取当前 session ID
            session_id = await _get_wecom_session_id(
                chat_id,
                ttl_hours=session_ttl_hours,
                aibotid=aibotid,
                chat_type=chat_type_from_msg or "single",
            )
            await _reconcile_wecom_session_owner(session_id, sender_id, session_owner_id)
            await _bind_wecom_session_to_project(session_id, session_owner_id, project_id)

            # ── 构造入站附件（下载媒体 → S3 → attachment）──────────
            # 失败降级返回 None，保持占位符行为，不阻断消息处理。
            attachments = await _build_wecom_attachments(
                manager, aibotid, metadata, owner_id=session_owner_id
            )

            task_manager = get_task_manager()

            # Cancel any previous running task for this session.
            # The same session is kept — context is preserved, the new message
            # is submitted as a fresh run on the existing session so the agent
            # sees the full conversation history plus the new user input.
            try:
                cancel_result = await task_manager.cancel(session_id, user_id=session_owner_id)
                if cancel_result.get("success") or cancel_result.get("cancelled_locally"):
                    logger.info(
                        "[WeCom] Cancelled previous run for session %s: %s",
                        session_id,
                        cancel_result.get("message", ""),
                    )
                    # Poll until old run reaches CANCELLED status (max 3s)
                    old_run_id = cancel_result.get("run_id")
                    if old_run_id:
                        for _ in range(30):
                            try:
                                run_status = await task_manager.get_run_status(
                                    session_id, old_run_id
                                )
                                if run_status and str(run_status) in (
                                    "CANCELLED",
                                    "cancelled",
                                    "TaskStatus.CANCELLED",
                                ):
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.1)
            except Exception as e:
                logger.debug("[WeCom] No previous run to cancel for session %s: %s", session_id, e)

            collector = WeComResponseCollector(
                manager=manager,
                aibotid=aibotid,
                chat_id=delivery_chat_id,
                reply_to_msgid=reply_to_msgid,
                sender_id=sender_id_from_msg,
                chat_type=chat_type_from_msg,
                stream_reply=stream_reply,
                send_thinking_message=send_thinking_message,
                segmented_reply=segmented_reply,
                segment_target_chars=segment_target_chars,
            )

            # 立即发送思考占位消息（满足 5 秒回调截止要求）
            if stream_reply and send_thinking_message:
                await collector.send_thinking_placeholder()

            async def executor(
                session_id: str,
                agent_id: str,
                message: str,
                user_id: str,
                presenter=None,
                disabled_tools=None,
                agent_options=None,
                attachments=None,
                disabled_skills=None,
                enabled_skills=None,
                persona_system_prompt=None,
                disabled_mcp_tools=None,
                team_id=None,
                active_goal=None,
            ):
                async for event in execute_wecom_agent(
                    session_id=session_id,
                    agent_id=agent_id,
                    message=message,
                    user_id=user_id,
                    presenter=presenter,
                    disabled_tools=disabled_tools,
                    agent_options=agent_options,
                    attachments=attachments,
                    disabled_skills=disabled_skills,
                    enabled_skills=enabled_skills,
                    persona_system_prompt=persona_system_prompt,
                    disabled_mcp_tools=disabled_mcp_tools,
                    team_id=team_id,
                    active_goal=active_goal,
                ):
                    yield event

            # Use time-based session title for WeCom
            session_title = utc_now().strftime("%Y-%m-%d %H:%M")

            run_id, trace_id = await task_manager.submit(
                session_id=session_id,
                agent_id=agent_to_use,
                message=content,
                user_id=session_owner_id,
                executor=executor,
                project_id=project_id,
                agent_options=wecom_agent_options or None,
                session_name=session_title,
                display_message=content,
                write_user_message_immediately=True,
                enabled_skills=enabled_skills,
                persona_system_prompt=persona_system_prompt,
                persona_preset_id=preset_id,
                attachments=attachments or None,
            )

            logger.info(
                "[WeCom] Task submitted: session=%s, run_id=%s, user=%s",
                session_id,
                run_id,
                session_owner_id,
            )
            await _persist_wecom_session_config(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_to_use,
                agent_request=agent_request,
                agent_options=wecom_agent_options,
                project_id=project_id,
            )

            # Set run_id on collector so the first content stream frame includes feedback
            collector.set_run_id(run_id)
            collector.set_reveal_scope(
                user_id=session_owner_id,
                session_id=session_id,
                trace_id=trace_id,
            )

            # Store run_id→session_id mapping for later feedback lookup
            await _store_run_session_mapping(run_id, session_id)

            await _process_events(
                collector=collector,
                session_id=session_id,
                run_id=run_id,
            )

            # Check if this run is still the active run for the session.
            # If a newer message arrived and cancelled this run, skip sending
            # the reply to avoid duplicate/conflicting messages.
            current_session = await task_manager.storage.get_by_session_id(session_id)
            current_run_id = (
                current_session.metadata.get("current_run_id")
                if current_session and current_session.metadata
                else None
            )
            if current_run_id and current_run_id != run_id:
                logger.info(
                    "[WeCom] Run %s superseded by %s, skipping reply for chat %s",
                    run_id,
                    current_run_id,
                    chat_id,
                )
                return

            streamed = await collector.finalize_stream_message()
            if not streamed:
                await collector.send_message()

            await collector.upload_and_send_files()

            logger.info("[WeCom] Message processing completed for %s", chat_id)

        except Exception as e:
            logger.error("[WeCom] Error handling message: %s", e, exc_info=True)
            try:
                if aibotid:
                    await manager.send_message(
                        aibotid,
                        delivery_chat_id,
                        f"❌ 处理消息时发生错误: {str(e)[:200]}",
                    )
            except Exception:
                pass

    return wecom_message_handler


# ── 事件处理 ──────────────────────────────────────────────────────────


async def _process_events(
    collector: WeComResponseCollector,
    session_id: str,
    run_id: str,
) -> None:
    """处理事件流并收集响应"""
    from src.infra.agent.wecom.helpers import _extract_tool_media_files
    from src.infra.session.dual_writer import get_dual_writer

    dual_writer = get_dual_writer()

    try:
        async for event in dual_writer.read_from_redis(session_id, run_id):
            event_type = event.get("event_type", "")
            data = event.get("data", {})

            if event_type == EVENT_MESSAGE_CHUNK:
                chunk = data.get("content", "")
                depth = data.get("depth", 0)
                if chunk and depth == 0:
                    await collector.append_stream_chunk(chunk)

            elif event_type == EVENT_TOOL_START:
                tool_name = data.get("tool", "")
                depth = data.get("depth", 0)
                agent_id = data.get("agent_id", "")
                if tool_name:
                    if depth > 0 or (agent_id and not agent_id.startswith("tool_")):
                        collector.add_subagent(tool_name)
                    else:
                        collector.add_tool(tool_name)

            elif event_type == EVENT_TOOL_RESULT:
                tool_name = data.get("tool", "")
                result = data.get("result", {})
                if tool_name == "reveal_file" and isinstance(result, dict):
                    file_infos = _extract_tool_media_files(result)

                    # Also handle reveal_file direct result format:
                    # {"key": "...", "url": "...", "name": "...", "type": "image", ...}
                    if not file_infos and "key" in result and "url" in result:
                        file_type = str(result.get("type") or "").lower()
                        mime_type = str(result.get("mime_type") or "").lower()
                        if (
                            file_type in {"image", "file", "audio", "video", "document"}
                            or mime_type
                        ):
                            media_type = file_type
                            if file_type == "document":
                                media_type = "file"
                            elif mime_type.startswith("image/"):
                                media_type = "image"
                            elif mime_type.startswith("audio/"):
                                media_type = "audio"
                            elif mime_type.startswith("video/"):
                                media_type = "video"
                            file_infos.append(
                                {
                                    "key": result["key"],
                                    "name": result.get("name", "unknown"),
                                    "type": media_type,
                                    "mime_type": mime_type or "application/octet-stream",
                                    "url": result.get("url", ""),
                                }
                            )

                    for fi in file_infos:
                        collector.add_file_to_reveal(fi)
                        logger.info(
                            "[WeCom] Added tool media file to reveal: %s",
                            fi.get("name"),
                        )

            elif event_type in ("done", "complete", "error"):
                break

        logger.info("[WeCom] Event processing completed for session=%s", session_id)

    except Exception as e:
        logger.error("[WeCom] Event processing error: %s", e, exc_info=True)


# ── WeCom 反馈处理 ──────────────────────────────────────────────────

_INACCURATE_REASON_MAP: dict[int, str] = {
    1: "与问题无关",
    2: "内容不完整",
    3: "内容错误",
    4: "数据分析错误",
}

_WECOM_REASON_TO_ENUM: dict[int, str] = {
    1: "irrelevant",
    2: "incomplete",
    3: "incorrect",
    4: "data_error",
}


async def _handle_wecom_feedback(
    feedback_id: str,
    feedback_type: int,
    content: str,
    inaccurate_reasons: list[int],
    sender_id: str,
    chat_id: str,
    chat_type: str,
    aibotid: str,
) -> None:
    """Process WeCom feedback event and sync to 昆小智 feedback system.

    WeCom feedback_type: 1=like, 2=dislike, 3=cancel.
    昆小智 rating: "up" or "down".
    """
    from src.infra.feedback.manager import FeedbackManager
    from src.infra.feedback.storage import FeedbackStorage
    from src.infra.user.storage import UserStorage
    from src.kernel.schemas.feedback import FeedbackCreate

    # Validate feedback_type (only 1=like, 2=dislike, 3=cancel are valid)
    if feedback_type not in (1, 2, 3):
        logger.warning(
            "[WeCom Feedback] Unknown feedback_type=%s for id=%s, skipping",
            feedback_type,
            feedback_id,
        )
        return

    # Map sender_id → user_id (same pattern as message handler)
    user_id = sender_id  # fallback
    username = sender_id
    try:
        user_storage = UserStorage()
        wecom_user_obj = await user_storage.get_by_username(sender_id)
        if wecom_user_obj:
            user_id = wecom_user_obj.id
            username = sender_id
            logger.info(
                "[WeCom Feedback] Mapped sender %s → user_id %s",
                sender_id,
                user_id,
            )
        else:
            logger.warning(
                "[WeCom Feedback] No 昆小智 user for username=%s, using sender_id as fallback",
                sender_id,
            )
    except Exception as e:
        logger.warning(
            "[WeCom Feedback] Failed to lookup user for sender %s: %s",
            sender_id,
            e,
        )

    # Look up session_id from run_id→session_id mapping stored at reply time.
    # Fallback to "wecom_{chat_id}" if mapping not found (e.g. Redis expired).
    session_id = await _lookup_session_by_run_id(feedback_id) or f"wecom_{chat_id}"

    # feedback.id was set to run_id when replying
    run_id = feedback_id

    feedback_manager = FeedbackManager()
    feedback_storage = FeedbackStorage()

    if feedback_type == 3:
        # Cancel: delete existing feedback record (idempotent)
        existing = await feedback_storage.get_user_feedback_for_run(user_id, session_id, run_id)
        if existing:
            await feedback_storage.delete(existing.id)
            logger.info(
                "[WeCom Feedback] Deleted feedback for run %s by user %s",
                run_id,
                sender_id,
            )
        return

    # type=1 → "up", type=2 → "down"
    rating = "up" if feedback_type == 1 else "down"

    # 点踩原因（仅 down 有意义）：取首个原因码映射到枚举
    reason: str | None = None
    if feedback_type == 2 and inaccurate_reasons:
        reason = _WECOM_REASON_TO_ENUM.get(inaccurate_reasons[0])

    # Build comment for type=2 (dislike)
    comment: str | None = None
    if feedback_type == 2:
        comment_parts: list[str] = []
        if content:
            comment_parts.append(f"用户反馈: {content}")
        if inaccurate_reasons:
            reason_texts = [
                _INACCURATE_REASON_MAP.get(r, f"未知原因({r})") for r in inaccurate_reasons
            ]
            comment_parts.append(f"原因: {', '.join(reason_texts)}")
        if comment_parts:
            comment = " | ".join(comment_parts)

    # Handle duplicate: check existing feedback
    existing = await feedback_storage.get_user_feedback_for_run(user_id, session_id, run_id)
    if existing:
        if existing.rating == rating:
            # Same rating, skip (idempotent)
            logger.info(
                "[WeCom Feedback] Duplicate feedback (same rating) for run %s by user %s, skipping",
                run_id,
                sender_id,
            )
            return
        # Different rating: delete old, then create new
        await feedback_storage.delete(existing.id)
        logger.info(
            "[WeCom Feedback] Replaced feedback for run %s by user %s (%s → %s)",
            run_id,
            sender_id,
            existing.rating,
            rating,
        )

    # Create feedback
    try:
        data = FeedbackCreate(
            session_id=session_id,
            run_id=run_id,
            rating=rating,
            comment=comment,
            reason=reason,
        )
        await feedback_manager.submit_feedback(user_id, username, data)
        logger.info(
            "[WeCom Feedback] Created %s feedback for run %s by user %s",
            rating,
            run_id,
            sender_id,
        )
    except ValueError as e:
        # Duplicate from concurrent request
        logger.warning(
            "[WeCom Feedback] Duplicate feedback for run %s by user %s: %s",
            run_id,
            sender_id,
            e,
        )
    except Exception as e:
        logger.error(
            "[WeCom Feedback] Failed to create feedback for run %s by user %s: %s",
            run_id,
            sender_id,
            e,
            exc_info=True,
        )


# ── Handler 设置入口 ──────────────────────────────────────────────────


async def setup_wecom_handler() -> None:
    """
    设置 WeCom 消息处理器并启动 Bot 管理器。

    从 persona_wecom_config 加载配置，按 aibotid 建 WS 连接。
    """
    from src.infra.agent.wecom.manager import get_wecom_bot_manager, start_wecom_bots

    manager = get_wecom_bot_manager()
    handler = create_wecom_message_handler(manager=manager)

    await start_wecom_bots(handler, feedback_handler=_handle_wecom_feedback)
    logger.info("WeCom bots started (preset-based routing)")
