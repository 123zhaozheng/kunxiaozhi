"""User directory lookups used by sharing, access checks and quota resolution."""

from __future__ import annotations

from dataclasses import dataclass

from src.infra.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class UserBrief:
    user_id: str
    username: str
    active: bool
    roles: tuple[str, ...] = ()


def _brief(user: object) -> UserBrief:
    return UserBrief(
        user_id=str(getattr(user, "id", "")),
        username=str(getattr(user, "username", "")),
        active=bool(getattr(user, "is_active", True)),
        roles=tuple(getattr(user, "roles", None) or ()),
    )


async def get_user_by_id(user_id: str) -> UserBrief | None:
    from src.infra.user.storage import UserStorage

    user = await UserStorage().get_by_id(user_id)
    return _brief(user) if user is not None else None


async def get_user_by_username(username: str) -> UserBrief | None:
    from src.infra.user.storage import UserStorage

    user = await UserStorage().get_by_username(username)
    return _brief(user) if user is not None else None


async def is_user_active(user_id: str) -> bool:
    """Unknown users count as inactive."""
    user = await get_user_by_id(user_id)
    return user is not None and user.active
