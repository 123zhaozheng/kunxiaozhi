"""KB notifications: persisted in ``kb_notifications`` and pushed over WebSocket best effort."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.infra.knowledge_base import events
from src.infra.knowledge_base.errors import KBNotFoundError
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.logging import get_logger
from src.infra.utils.datetime import utc_now
from src.kernel.schemas.knowledge_base import (
    KBNotification,
    KBNotificationListResponse,
    KBUnreadCount,
)
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

NOTIF_SHARED = "kb_shared"
NOTIF_ROLE_CHANGED = "kb_role_changed"
NOTIF_REMOVED = "kb_removed"
NOTIF_LEFT = "kb_member_left"
NOTIF_SUBSCRIBED = "kb_subscribed"
NOTIF_PUBLISH_PENDING = "kb_publish_pending"
NOTIF_APPROVED = "kb_approved"
NOTIF_REJECTED = "kb_rejected"
NOTIF_DELISTED = "kb_delisted"
NOTIF_UNPUBLISHED = "kb_unpublished"


async def notify(
    user_ids: list[str],
    *,
    type_: str,
    kb: dict[str, Any],
    message: str,
    actor_id: str = "",
    actor_username: str = "",
) -> None:
    """Persist one notification per recipient (never the actor); failures are logged only."""
    for user_id in dict.fromkeys(user_ids):
        if not user_id or user_id == actor_id:
            continue
        doc = {
            "_id": uuid4().hex,
            "user_id": user_id,
            "type": type_,
            "kb_id": str(kb.get("_id")),
            "kb_name": kb.get("name", ""),
            "actor_id": actor_id,
            "actor_username": actor_username,
            "message": message,
            "read": False,
            "created_at": utc_now(),
        }
        try:
            await get_kb_storage().insert_notification(doc)
        except Exception:  # noqa: BLE001 - a lost notification must not fail the action
            logger.exception("[KB] store notification for %s failed", user_id)
            continue
        await events.push_notification(user_id, doc)


async def list_notifications(
    user: TokenPayload, *, unread_only: bool = False, page: int = 1, page_size: int = 20
) -> KBNotificationListResponse:
    storage = get_kb_storage()
    items, total = await storage.list_notifications(
        user.sub, unread_only=unread_only, page=page, page_size=page_size
    )
    return KBNotificationListResponse(
        items=[KBNotification.model_validate(i) for i in items],
        total=total,
        unread=await storage.count_unread_notifications(user.sub),
        page=max(int(page), 1),
        page_size=min(max(int(page_size), 1), 100),
    )


async def unread_count(user: TokenPayload) -> KBUnreadCount:
    return KBUnreadCount(count=await get_kb_storage().count_unread_notifications(user.sub))


async def mark_read(user: TokenPayload, notification_id: str) -> None:
    if not await get_kb_storage().mark_notification_read(user.sub, notification_id):
        raise KBNotFoundError("通知不存在")


async def mark_all_read(user: TokenPayload) -> int:
    return await get_kb_storage().mark_all_notifications_read(user.sub)
