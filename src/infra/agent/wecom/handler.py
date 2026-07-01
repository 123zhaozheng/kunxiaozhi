"""
WeCom (企业微信) 消息处理器模块

处理 WeCom 消息的 Agent 执行和响应。
按 aibotid 路由到 persona preset，session 归属 sender_id。
支持流式回复（原生 WebSocket 流式）、5 秒思考占位、6 分钟超时回退。
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Optional

from src.infra.agent.wecom.collector import WeComResponseCollector
from src.infra.agent.wecom.manager import WeComBotManager
from src.infra.logging import get_logger
from src.infra.utils.datetime import utc_now

logger = get_logger(__name__)

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


async def _get_wecom_session_id(chat_id: str, ttl_hours: int | None = 24) -> str:
    """获取 WeCom 聊天对应的当前 session ID，如果不存在则创建默认的"""
    from src.infra.storage.redis import RedisStorage

    storage = RedisStorage()
    key = f"{WECOM_SESSION_KEY_PREFIX}{chat_id}"
    session_id = await storage.get(key)

    if session_id is None:
        # 默认使用 chat_id 作为 session ID（兼容旧数据）
        session_id = f"wecom_{chat_id}"
        ttl_seconds = (ttl_hours or 0) * 3600 + 3600  # +1h buffer
        if ttl_hours:  # 0 means no expiry
            await storage.set(key, session_id, ttl=ttl_seconds)
        else:
            await storage.set(key, session_id)

    return session_id


async def _create_new_wecom_session(chat_id: str, ttl_hours: int | None = 24) -> str:
    """为 WeCom 聊天创建新的 session ID"""
    from src.infra.storage.redis import RedisStorage

    storage = RedisStorage()
    key = f"{WECOM_SESSION_KEY_PREFIX}{chat_id}"

    # 使用时间戳生成唯一的 session ID
    timestamp = int(time.time())
    session_id = f"wecom_{chat_id}_{timestamp}"

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
            session_owner_id = sender_id  # fallback
            try:
                from src.infra.user.storage import UserStorage
                user_storage = UserStorage()
                wecom_user_obj = await user_storage.get_by_username(sender_id)
                if wecom_user_obj:
                    session_owner_id = wecom_user_obj.id
                    logger.info(
                        "[WeCom] Mapped sender %s → user_id %s",
                        sender_id, session_owner_id,
                    )
                else:
                    logger.warning(
                        "[WeCom] No 昆小智 user found for username=%s, "
                        "using sender_id as fallback",
                        sender_id,
                    )
            except Exception as e:
                logger.warning(
                    "[WeCom] Failed to lookup user for sender %s: %s",
                    sender_id, e,
                )

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

            # Use "search" agent (same as Web chat default).
            # PersonaPreset provides the system_prompt and skills via snapshot.
            agent_to_use = "search"

            # ── WeCom 行为配置 ─────────────────────────────────────
            stream_reply = wecom_config.get("stream_reply", True)
            send_thinking_message = wecom_config.get("send_thinking_message", True)
            segmented_reply = wecom_config.get("segmented_reply", True)
            session_ttl_hours = wecom_config.get("session_ttl_hours", 24)

            # ── Project 自动创建 ───────────────────────────────────
            # Use the preset name as the project name for organizing sessions
            project_id: str | None = None
            try:
                from src.infra.folder.storage import get_project_storage
                from src.infra.persona_preset.manager import PersonaPresetManager

                preset_manager = PersonaPresetManager()
                preset = await preset_manager.get_preset(
                    preset_id,
                    user_id="",
                    is_admin=True,
                )
                preset_name = preset.name if preset else "WeCom"

                proj_storage = get_project_storage()
                project = await proj_storage.get_or_create_by_name(
                    session_owner_id, preset_name, project_type="channel", icon="💬"
                )
                project_id = project.id
            except Exception as e:
                logger.warning("[WeCom] Failed to auto-create project for preset %s: %s", preset_id, e)

            # ── 处理 /new 命令 - 严格匹配 ─────────────────────────
            if content.strip() == "/new":
                new_session_id = await _create_new_wecom_session(chat_id, ttl_hours=session_ttl_hours)
                await manager.send_message(
                    aibotid,
                    delivery_chat_id,
                    "✅ 已创建新对话，请发送消息开始",
                )
                logger.info("[WeCom] New session created for chat %s: %s", chat_id, new_session_id)
                return

            # 获取当前 session ID
            session_id = await _get_wecom_session_id(chat_id, ttl_hours=session_ttl_hours)
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
                                run_status = await task_manager.get_run_status(session_id, old_run_id)
                                if run_status and str(run_status) in ("CANCELLED", "cancelled", "TaskStatus.CANCELLED"):
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

            run_id, _ = await task_manager.submit(
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
            )

            logger.info("[WeCom] Task submitted: session=%s, run_id=%s, user=%s", session_id, run_id, session_owner_id)

            # Set run_id on collector so the first content stream frame includes feedback
            collector.set_run_id(run_id)

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
                    run_id, current_run_id, chat_id,
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
                if isinstance(result, dict):
                    file_infos = _extract_tool_media_files(result)

                    # Also handle reveal_file direct result format:
                    # {"key": "...", "url": "...", "name": "...", "type": "image", ...}
                    if not file_infos and "key" in result and "url" in result:
                        file_type = str(result.get("type") or "").lower()
                        mime_type = str(result.get("mime_type") or "").lower()
                        if file_type in {"image", "file", "audio", "video", "document"} or mime_type:
                            media_type = file_type
                            if file_type == "document":
                                media_type = "file"
                            elif mime_type.startswith("image/"):
                                media_type = "image"
                            elif mime_type.startswith("audio/"):
                                media_type = "audio"
                            elif mime_type.startswith("video/"):
                                media_type = "video"
                            file_infos.append({
                                "key": result["key"],
                                "name": result.get("name", "unknown"),
                                "type": media_type,
                                "mime_type": mime_type or "application/octet-stream",
                                "url": result.get("url", ""),
                            })

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
        existing = await feedback_storage.get_user_feedback_for_run(
            user_id, session_id, run_id
        )
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
                _INACCURATE_REASON_MAP.get(r, f"未知原因({r})")
                for r in inaccurate_reasons
            ]
            comment_parts.append(f"原因: {', '.join(reason_texts)}")
        if comment_parts:
            comment = " | ".join(comment_parts)

    # Handle duplicate: check existing feedback
    existing = await feedback_storage.get_user_feedback_for_run(
        user_id, session_id, run_id
    )
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
