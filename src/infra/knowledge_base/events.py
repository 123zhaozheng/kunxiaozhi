"""Best-effort WebSocket pushes for knowledge base state changes."""

from __future__ import annotations

from typing import Any

from src.infra.knowledge_base.access import is_publicly_readable
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.logging import get_logger
from src.infra.websocket import get_connection_manager
from src.kernel.schemas.knowledge_base import KB_ROLE_EDITOR, KB_ROLE_OWNER, KB_ROLE_VIEWER

logger = get_logger(__name__)

_NON_SUBSCRIBER_ROLES = [KB_ROLE_OWNER, KB_ROLE_EDITOR, KB_ROLE_VIEWER]


async def push_event(kb_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Fan an event out to every member of the knowledge base; never raises."""
    try:
        storage = get_kb_storage()
        kb = await storage.get_base(kb_id)
        # Subscriber rows survive delisting; only push to them while the KB is readable.
        roles = None if kb is not None and is_publicly_readable(kb) else _NON_SUBSCRIBER_ROLES
        user_ids = await storage.list_member_user_ids(kb_id, roles)
        if not user_ids:
            return
        manager = get_connection_manager()
        message = {"type": event_type, "data": {"kb_id": kb_id, **data}}
        for user_id in user_ids:
            await manager.send_to_user_with_broadcast(user_id, message)
    except Exception as exc:  # noqa: BLE001 - pushes are an optimisation, polling is the fallback
        logger.warning("[KB] push %s for %s failed: %s", event_type, kb_id, exc)


async def push_document_updated(kb_id: str, doc_id: str, status: str) -> None:
    await push_event(kb_id, "kb:document_updated", {"doc_id": doc_id, "status": status})


async def push_task_updated(task: dict[str, Any]) -> None:
    await push_event(
        str(task.get("kb_id")),
        "kb:task_updated",
        {
            "task_id": task.get("_id"),
            "type": task.get("type"),
            "status": task.get("status"),
            "progress": task.get("progress") or {},
        },
    )


async def push_notification(user_id: str, notification: dict[str, Any]) -> None:
    """Best-effort ``kb:notification`` push to one user; never raises."""
    try:
        await get_connection_manager().send_to_user_with_broadcast(
            user_id,
            {
                "type": "kb:notification",
                "data": {
                    "id": notification.get("_id"),
                    "type": notification.get("type"),
                    "kb_id": notification.get("kb_id"),
                    "kb_name": notification.get("kb_name"),
                    "actor_username": notification.get("actor_username"),
                    "message": notification.get("message"),
                },
            },
        )
    except Exception as exc:  # noqa: BLE001 - the stored row is the source of truth
        logger.warning("[KB] push notification to %s failed: %s", user_id, exc)
