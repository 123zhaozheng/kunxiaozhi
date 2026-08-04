"""Persona 点赞/点踩通知分发服务（Web 实时 + 企业微信主动推送）

在 persona 收到企业微信点赞/点踩（_handle_wecom_feedback 写入成功后）触发，
读取 persona_wecom_config.feedback_notify_targets：
- Web 渠道：目标 username → 昆小智 user_id → WebSocket 实时推送。
- WeCom 渠道：仅向已绑定（wecom_notify_bindings）的用户主动推送。

任何渠道/目标失败均记录日志并隔离，不向上抛出（调用方为反馈主流程，
绝不能因通知失败影响反馈写入）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.infra.agent.config_storage import AgentConfigStorage
from src.infra.agent.wecom.binding import WeComNotifyBindingStorage
from src.infra.agent.wecom.manager import get_wecom_bot_manager
from src.infra.logging import get_logger
from src.infra.user.storage import UserStorage
from src.infra.websocket import get_connection_manager

logger = get_logger(__name__)


def _build_wecom_text(
    preset_name: str,
    rating: str,
    operator: str,
    comment: str | None,
    user_question: str | None,
    model_output: str | None,
) -> str:
    """构建企业微信主动推送文案。"""
    action = "点赞" if rating == "up" else "点踩"
    lines = [f"📢 【{preset_name}】收到新的{action}", f"操作人：{operator}"]
    if rating == "down" and comment:
        lines.append(f"反馈：{comment}")
    if user_question:
        lines.append(f"用户问题：{user_question}")
    if model_output:
        lines.append(f"模型回复：{model_output}")
    return "\n".join(lines)


def _truncate(text: str, limit: int = 200) -> str:
    """截断长文本并追加省略号。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


async def _load_run_context(
    session_id: str, run_id: str
) -> tuple[str | None, str | None]:
    """从 trace 中提取该 run 的用户问题与模型最终输出（用于通知内容）。

    失败时返回 (None, None)，不影响通知主流程。
    """
    try:
        from src.infra.session.trace_storage import get_trace_storage

        events = await get_trace_storage().get_session_events(
            session_id,
            event_types=["user:message", "message:chunk"],
            run_id=run_id,
        )
        user_question: str | None = None
        chunks: list[tuple[int, str]] = []
        for event in events:
            event_type = event.get("event_type")
            data = event.get("data") or {}
            content = data.get("content")
            if not content:
                continue
            if event_type == "user:message" and user_question is None:
                user_question = _truncate(str(content))
            elif event_type == "message:chunk":
                chunks.append((event.get("seq") or 0, str(content)))
        chunks.sort(key=lambda item: item[0])
        model_output = _truncate("".join(c for _, c in chunks)) if chunks else None
        return user_question, model_output
    except Exception as e:
        logger.warning(
            "[FeedbackNotify] Failed to load run context for run=%s: %s",
            run_id,
            e,
        )
        return None, None


async def notify_persona_feedback(
    *,
    preset_id: str,
    preset_name: str,
    rating: str,  # "up" | "down"
    operator: str,  # 操作者 userid/username
    comment: str | None,
    aibotid: str,
    session_id: str,
    run_id: str,
) -> None:
    """向 persona 配置的通知对象分发点赞/点踩通知（Web + WeCom 双渠道）。

    无通知目标 / 未配置时不产生任何通知；单渠道单目标失败不阻塞其余通知。
    """
    try:
        config = await AgentConfigStorage().get_persona_wecom_config(preset_id)
        targets = config.feedback_notify_targets if config else []
        if not targets:
            return

        user_question, model_output = await _load_run_context(session_id, run_id)

        message: dict[str, Any] = {
            "type": "notification:feedback",
            "data": {
                "preset_id": preset_id,
                "preset_name": preset_name,
                "rating": rating,
                "operator": operator,
                "comment": comment,
                "user_question": user_question,
                "model_output": model_output,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        }

        # ── Web 渠道：username → user_id → WebSocket 定向推送 ─────
        for username in targets:
            try:
                user = await UserStorage().get_by_username(username)
                if not user:
                    logger.warning(
                        "[FeedbackNotify] No user for target username=%s, skipping web push",
                        username,
                    )
                    continue
                await get_connection_manager().send_to_user_with_broadcast(
                    user.id, message
                )
            except Exception as e:
                logger.warning(
                    "[FeedbackNotify] Web push failed for username=%s: %s",
                    username,
                    e,
                )

        # ── WeCom 渠道：仅向已绑定该机器人的目标主动推送 ──────────
        bound = await WeComNotifyBindingStorage().list_bound(aibotid, list(targets))
        if bound:
            wecom_text = _build_wecom_text(
                preset_name, rating, operator, comment, user_question, model_output
            )
            for username in bound:
                try:
                    ok = await get_wecom_bot_manager().send_message(
                        aibotid, username, wecom_text
                    )
                    if not ok:
                        logger.warning(
                            "[FeedbackNotify] WeCom send returned False for aibotid=%s username=%s",
                            aibotid,
                            username,
                        )
                except Exception as e:
                    logger.warning(
                        "[FeedbackNotify] WeCom push failed for aibotid=%s username=%s: %s",
                        aibotid,
                        username,
                        e,
                    )
    except Exception as e:
        logger.warning(
            "[FeedbackNotify] notify_persona_feedback failed for preset=%s: %s",
            preset_id,
            e,
        )
