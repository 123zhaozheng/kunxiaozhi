from __future__ import annotations

from typing import Optional

import pytest

from src.infra.user.manager import UserManager
from src.kernel.config import settings
from src.kernel.exceptions import AccountNotActiveError, EmailNotVerifiedError
from src.kernel.schemas.user import UserInDB

_USERNAME = "alice"
_EMAIL = "alice@example.com"
_PASSWORD_HASH = "hash"
_USER_ID = "65f1a2b3c4d5e6f7a8b9c0d1"


def _make_user(
    *,
    email_verified: Optional[bool] = True,
    is_active: bool = True,
) -> UserInDB:
    return UserInDB(
        id=_USER_ID,
        username=_USERNAME,
        email=_EMAIL,
        password_hash=_PASSWORD_HASH,
        roles=[],
        is_active=is_active,
        email_verified=email_verified,
    )


class _Storage:
    """Records touch_updated_at calls and controls authenticate() output."""

    def __init__(self, user: Optional[UserInDB]) -> None:
        self._user = user
        self.touched: list[str] = []

    async def authenticate(self, username_or_email: str, password: str):
        return self._user

    async def touch_updated_at(self, user_id: str) -> bool:
        self.touched.append(user_id)
        return True


class _RoleStorage:
    async def get_by_name(self, name: str):
        return None


def _manager_with(storage: _Storage) -> UserManager:
    manager = UserManager()
    manager.storage = storage
    manager.role_storage = _RoleStorage()
    return manager


@pytest.mark.asyncio
async def test_login_refreshes_updated_at_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False, raising=False)

    storage = _Storage(user=_make_user(email_verified=True, is_active=True))
    manager = _manager_with(storage)

    token = await manager.login(_USERNAME, "password")

    assert token is not None
    assert storage.touched == [_USER_ID]


@pytest.mark.asyncio
async def test_login_does_not_refresh_updated_at_on_bad_credentials() -> None:
    storage = _Storage(user=None)
    manager = _manager_with(storage)

    token = await manager.login(_USERNAME, "wrong-password")

    assert token is None
    assert storage.touched == []


@pytest.mark.asyncio
async def test_login_does_not_refresh_updated_at_when_email_not_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", True, raising=False)

    storage = _Storage(user=_make_user(email_verified=False, is_active=True))
    manager = _manager_with(storage)

    with pytest.raises(EmailNotVerifiedError):
        await manager.login(_USERNAME, "password")

    assert storage.touched == []


@pytest.mark.asyncio
async def test_login_does_not_refresh_updated_at_when_account_inactive() -> None:
    storage = _Storage(user=_make_user(email_verified=True, is_active=False))
    manager = _manager_with(storage)

    with pytest.raises(AccountNotActiveError):
        await manager.login(_USERNAME, "password")

    assert storage.touched == []
