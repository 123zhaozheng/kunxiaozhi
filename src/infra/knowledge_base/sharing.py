"""Sharing, public publishing/review, subscriptions and the public plaza."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from src.infra.knowledge_base import notifications as notif
from src.infra.knowledge_base import users
from src.infra.knowledge_base.access import (
    is_publicly_readable,
    require_kb_access,
    resolve_kb_access,
)
from src.infra.knowledge_base.errors import (
    KBConflictError,
    KBForbiddenError,
    KBNotFoundError,
    KBValidationError,
)
from src.infra.knowledge_base.service import build_kb_responses, ensure_enabled
from src.infra.knowledge_base.storage import get_kb_storage
from src.infra.utils.datetime import utc_now
from src.kernel.config import settings
from src.kernel.schemas.knowledge_base import (
    KB_ROLE_OWNER,
    KB_ROLE_SUBSCRIBER,
    KB_ROLE_VISITOR,
    KB_SHARE_ROLES,
    PUBLIC_APPROVED,
    PUBLIC_DELISTED,
    PUBLIC_NONE,
    PUBLIC_PENDING,
    PUBLIC_REJECTED,
    PUBLIC_STATUSES,
    KBAdminListResponse,
    KBMember,
    KBMemberAdd,
    KBMemberAddError,
    KBMemberAddResponse,
    KBMemberListResponse,
    KBPlazaResponse,
    KBUserResolve,
    KBUserResolveResponse,
    KnowledgeBase,
)
from src.kernel.schemas.user import TokenPayload

REASON_MAX = 500
ADMIN_VISIBLE_STATUSES = [PUBLIC_PENDING, PUBLIC_APPROVED, PUBLIC_REJECTED, PUBLIC_DELISTED]


def _check_share_role(role: str) -> str:
    if role not in KB_SHARE_ROLES:
        raise KBValidationError("角色只能是 editor 或 viewer")
    return role


def _member(row: dict[str, Any]) -> KBMember:
    return KBMember.model_validate(row)


# ── user resolution ──────────────────────────────────────────


async def resolve_users(usernames: list[str]) -> KBUserResolveResponse:
    """Expose only whether each username exists and is active (exact match)."""
    ensure_enabled()
    items: list[KBUserResolve] = []
    seen: set[str] = set()
    for raw in usernames:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        brief = await users.get_user_by_username(name)
        items.append(
            KBUserResolve(
                username=name, exists=brief is not None, active=bool(brief and brief.active)
            )
        )
    return KBUserResolveResponse(items=items)


# ── members ──────────────────────────────────────────────────


async def list_members(user: TokenPayload, kb_id: str) -> KBMemberListResponse:
    ensure_enabled()
    await require_kb_access(user.sub, kb_id, manage=True)
    rows = await get_kb_storage().list_members(kb_id, [KB_ROLE_OWNER, *KB_SHARE_ROLES])
    return KBMemberListResponse(items=[_member(r) for r in rows])


async def _add_one(
    user: TokenPayload, access: Any, kb_id: str, name: str, role: str
) -> tuple[str, KBMember | str]:
    """Return ("added"|"updated"|"unchanged", member) or ("error", reason)."""
    target = await users.get_user_by_username(name)
    if target is None:
        return "error", "not_found"
    if not target.active:
        return "error", "inactive"
    if target.user_id == str(access.kb["owner_id"]):
        return "error", "self"

    storage = get_kb_storage()
    existing = await storage.get_member(kb_id, target.user_id)
    if existing is not None and existing.get("role") in (*KB_SHARE_ROLES, KB_ROLE_SUBSCRIBER):
        if existing["role"] == role:
            return "unchanged", _member(existing)
        updated = await storage.set_member_role(
            kb_id, target.user_id, role, [str(existing["role"])]
        )
        if updated is None:
            raise KBConflictError("成员状态已变化，请刷新后重试")
        await notif.notify(
            [target.user_id],
            type_=notif.NOTIF_ROLE_CHANGED,
            kb=access.kb,
            message=f"你在知识库「{access.kb['name']}」中的角色已变更为 {role}",
            actor_id=user.sub,
            actor_username=user.username,
        )
        return "updated", _member(updated)

    row = {
        "_id": uuid4().hex,
        "kb_id": kb_id,
        "user_id": target.user_id,
        "username": target.username,
        "role": role,
        "chat_enabled": True,
        "granted_by": user.sub,
        "created_at": utc_now(),
    }
    try:
        await storage.insert_member(row)
    except DuplicateKeyError as exc:
        raise KBConflictError("该用户已是成员") from exc
    await notif.notify(
        [target.user_id],
        type_=notif.NOTIF_SHARED,
        kb=access.kb,
        message=f"{user.username} 与你分享了知识库「{access.kb['name']}」（{role}）",
        actor_id=user.sub,
        actor_username=user.username,
    )
    return "added", _member(row)


async def add_members(user: TokenPayload, kb_id: str, body: KBMemberAdd) -> KBMemberAddResponse:
    ensure_enabled()
    access = await require_kb_access(user.sub, kb_id, manage=True)
    role = _check_share_role(body.role)
    result = KBMemberAddResponse()
    seen: set[str] = set()
    for raw in body.usernames:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        kind, value = await _add_one(user, access, kb_id, name, role)
        if kind == "error":
            result.errors.append(KBMemberAddError(username=name, reason=str(value)))
        elif kind == "added" and isinstance(value, KBMember):
            result.added.append(value)
        elif kind == "updated" and isinstance(value, KBMember):
            result.updated.append(value)
    return result


async def update_member(user: TokenPayload, kb_id: str, target_id: str, role: str) -> KBMember:
    ensure_enabled()
    access = await require_kb_access(user.sub, kb_id, manage=True)
    role = _check_share_role(role)
    updated = await get_kb_storage().set_member_role(kb_id, target_id, role, list(KB_SHARE_ROLES))
    if updated is None:
        raise KBNotFoundError("成员不存在")
    await notif.notify(
        [target_id],
        type_=notif.NOTIF_ROLE_CHANGED,
        kb=access.kb,
        message=f"你在知识库「{access.kb['name']}」中的角色已变更为 {role}",
        actor_id=user.sub,
        actor_username=user.username,
    )
    return _member(updated)


async def remove_member(user: TokenPayload, kb_id: str, target_id: str) -> None:
    """Owner removes an editor/viewer, or an editor/viewer leaves (``target_id`` is themselves)."""
    ensure_enabled()
    access = await require_kb_access(user.sub, kb_id)
    leaving = target_id == user.sub
    if not leaving and not access.can_manage:
        raise KBForbiddenError()
    if leaving and access.role == KB_ROLE_OWNER:
        raise KBValidationError("所有者不能退出自己的知识库")
    if not await get_kb_storage().delete_member(kb_id, target_id, list(KB_SHARE_ROLES)):
        raise KBNotFoundError("成员不存在")
    if leaving:
        await notif.notify(
            [str(access.kb["owner_id"])],
            type_=notif.NOTIF_LEFT,
            kb=access.kb,
            message=f"{user.username} 退出了知识库「{access.kb['name']}」",
            actor_id=user.sub,
            actor_username=user.username,
        )
    else:
        await notif.notify(
            [target_id],
            type_=notif.NOTIF_REMOVED,
            kb=access.kb,
            message=f"你已被移出知识库「{access.kb['name']}」",
            actor_id=user.sub,
            actor_username=user.username,
        )


# ── publishing ───────────────────────────────────────────────


async def _respond(kb: dict[str, Any], role: str) -> KnowledgeBase:
    return (await build_kb_responses([(kb, role)]))[0]


async def publish(user: TokenPayload, kb_id: str) -> KnowledgeBase:
    ensure_enabled()
    access = await require_kb_access(user.sub, kb_id, manage=True)
    if not getattr(settings, "KB_ALLOW_PUBLIC", True):
        raise KBForbiddenError("管理员已关闭知识库公开功能")
    status = access.kb.get("public_status") or PUBLIC_NONE
    if status == PUBLIC_DELISTED:
        raise KBForbiddenError("该知识库已被管理员下架，无法重新公开")
    if status in (PUBLIC_PENDING, PUBLIC_APPROVED):
        return await _respond(access.kb, access.role)

    approved = not getattr(settings, "KB_PUBLIC_REQUIRES_REVIEW", False)
    kb = await get_kb_storage().transition_public_status(
        kb_id,
        [PUBLIC_NONE, PUBLIC_REJECTED],
        {
            "visibility": "public",
            "public_status": PUBLIC_APPROVED if approved else PUBLIC_PENDING,
            "published_at": utc_now(),
            "review": None,
        },
    )
    if kb is None:
        raise KBConflictError("公开状态已变化，请刷新后重试")
    return await _respond(kb, access.role)


async def unpublish(user: TokenPayload, kb_id: str) -> KnowledgeBase:
    ensure_enabled()
    access = await require_kb_access(user.sub, kb_id, manage=True)
    storage = get_kb_storage()
    subscribers = [
        str(m["user_id"]) for m in await storage.list_members(kb_id, [KB_ROLE_SUBSCRIBER])
    ]
    kb = await storage.transition_public_status(
        kb_id,
        [PUBLIC_PENDING, PUBLIC_APPROVED, PUBLIC_REJECTED],
        {"visibility": "private", "public_status": PUBLIC_NONE, "published_at": None},
    )
    if kb is None:
        return await _respond(access.kb, access.role)  # not public: nothing to undo
    await storage.delete_members_by_role(kb_id, KB_ROLE_SUBSCRIBER)
    await notif.notify(
        subscribers,
        type_=notif.NOTIF_UNPUBLISHED,
        kb=kb,
        message=f"知识库「{kb['name']}」已取消公开，订阅已失效",
        actor_id=user.sub,
        actor_username=user.username,
    )
    return await _respond(kb, access.role)


# ── subscriptions ────────────────────────────────────────────


async def subscribe(user: TokenPayload, kb_id: str) -> KnowledgeBase:
    ensure_enabled()
    access = await resolve_kb_access(user.sub, kb_id)
    if access is None:
        raise KBNotFoundError()
    if access.role != KB_ROLE_VISITOR:
        return await _respond(access.kb, access.role)  # owner/member/already subscribed
    try:
        await get_kb_storage().insert_member(
            {
                "_id": uuid4().hex,
                "kb_id": kb_id,
                "user_id": user.sub,
                "username": user.username,
                "role": KB_ROLE_SUBSCRIBER,
                "chat_enabled": True,
                "granted_by": user.sub,
                "created_at": utc_now(),
            }
        )
    except DuplicateKeyError:
        pass
    await notif.notify(
        [str(access.kb["owner_id"])],
        type_=notif.NOTIF_SUBSCRIBED,
        kb=access.kb,
        message=f"{user.username} 订阅了你的知识库「{access.kb['name']}」",
        actor_id=user.sub,
        actor_username=user.username,
    )
    return await _respond(access.kb, KB_ROLE_SUBSCRIBER)


async def unsubscribe(user: TokenPayload, kb_id: str) -> None:
    ensure_enabled()
    if not await get_kb_storage().delete_member(kb_id, user.sub, [KB_ROLE_SUBSCRIBER]):
        raise KBNotFoundError("未订阅该知识库")


# ── plaza ────────────────────────────────────────────────────


async def list_plaza(
    user: TokenPayload, *, q: str | None = None, page: int = 1, page_size: int = 20
) -> KBPlazaResponse:
    ensure_enabled()
    page = max(int(page), 1)
    page_size = min(max(int(page_size), 1), 100)
    if not getattr(settings, "KB_ALLOW_PUBLIC", True):
        return KBPlazaResponse(items=[], total=0, page=page, page_size=page_size)
    storage = get_kb_storage()
    bases, total = await storage.list_public_bases(query=q, page=page, page_size=page_size)
    bases = [kb for kb in bases if is_publicly_readable(kb)]
    roles = {
        str(m["kb_id"]): str(m["role"])
        for m in await storage.list_memberships(
            user.sub, [KB_ROLE_OWNER, KB_ROLE_SUBSCRIBER, *KB_SHARE_ROLES]
        )
    }
    items = await build_kb_responses(
        [(kb, roles.get(str(kb["_id"]), KB_ROLE_VISITOR)) for kb in bases]
    )
    return KBPlazaResponse(items=items, total=total, page=page, page_size=page_size)


# ── admin review ─────────────────────────────────────────────


async def admin_list_public(
    *, status: str | None = None, q: str | None = None, page: int = 1, page_size: int = 20
) -> KBAdminListResponse:
    ensure_enabled()
    wanted = [s.strip() for s in (status or "").split(",") if s.strip()]
    if any(s not in PUBLIC_STATUSES or s == PUBLIC_NONE for s in wanted):
        raise KBValidationError("不支持的公开状态")
    page = max(int(page), 1)
    page_size = min(max(int(page_size), 1), 100)
    bases, total = await get_kb_storage().list_public_bases(
        statuses=wanted or ADMIN_VISIBLE_STATUSES, query=q, page=page, page_size=page_size
    )
    items = await build_kb_responses([(kb, KB_ROLE_VISITOR) for kb in bases])
    return KBAdminListResponse(items=items, total=total, page=page, page_size=page_size)


def _review(action: str, admin: TokenPayload, reason: str) -> dict[str, Any]:
    return {
        "action": action,
        "reviewer_id": admin.sub,
        "reviewer_username": admin.username,
        "reason": (reason or "").strip()[:REASON_MAX],
        "at": utc_now(),
    }


async def _admin_transition(
    admin: TokenPayload,
    kb_id: str,
    from_statuses: list[str],
    updates: dict[str, Any],
    action: str,
    reason: str,
    notify_type: str,
    message: str,
    *,
    notify_subscribers: bool = False,
) -> KnowledgeBase:
    storage = get_kb_storage()
    kb = await storage.get_base(kb_id)
    if kb is None or kb.get("public_status", PUBLIC_NONE) == PUBLIC_NONE:
        raise KBNotFoundError()
    if kb.get("status") != "active":
        raise KBNotFoundError()
    updated = await storage.transition_public_status(
        kb_id, from_statuses, {**updates, "review": _review(action, admin, reason)}
    )
    if updated is None:
        raise KBConflictError("当前状态不允许此操作")
    recipients = [str(updated["owner_id"])]
    if notify_subscribers:
        recipients += [
            str(m["user_id"]) for m in await storage.list_members(kb_id, [KB_ROLE_SUBSCRIBER])
        ]
    suffix = f"：{reason.strip()[:REASON_MAX]}" if reason and reason.strip() else ""
    await notif.notify(
        recipients,
        type_=notify_type,
        kb=updated,
        message=message.format(name=updated["name"]) + suffix,
        actor_id=admin.sub,
        actor_username=admin.username,
    )
    return (await build_kb_responses([(updated, KB_ROLE_VISITOR)]))[0]


async def admin_approve(admin: TokenPayload, kb_id: str) -> KnowledgeBase:
    ensure_enabled()
    return await _admin_transition(
        admin,
        kb_id,
        [PUBLIC_PENDING, PUBLIC_REJECTED, PUBLIC_DELISTED],
        {"visibility": "public", "public_status": PUBLIC_APPROVED, "published_at": utc_now()},
        "approved",
        "",
        notif.NOTIF_APPROVED,
        "你的知识库「{name}」已通过审核并公开",
    )


async def admin_reject(admin: TokenPayload, kb_id: str, reason: str = "") -> KnowledgeBase:
    ensure_enabled()
    return await _admin_transition(
        admin,
        kb_id,
        [PUBLIC_PENDING],
        {"visibility": "private", "public_status": PUBLIC_REJECTED},
        "rejected",
        reason,
        notif.NOTIF_REJECTED,
        "你的知识库「{name}」未通过公开审核",
    )


async def admin_delist(admin: TokenPayload, kb_id: str, reason: str = "") -> KnowledgeBase:
    ensure_enabled()
    return await _admin_transition(
        admin,
        kb_id,
        [PUBLIC_PENDING, PUBLIC_APPROVED],
        {"visibility": "private", "public_status": PUBLIC_DELISTED},
        "delisted",
        reason,
        notif.NOTIF_DELISTED,
        "知识库「{name}」已被管理员下架",
        notify_subscribers=True,
    )
