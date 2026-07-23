from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infra.auth.oauth import OAuthService, OAuthUserInfo
from src.infra.auth.rbac import RBACManager
from src.kernel.schemas.permission import get_permissions_response
from src.kernel.schemas.user import OAuthProvider
from src.kernel.types import Permission


def test_username_update_permission_exists_and_is_grouped() -> None:
    assert Permission.USERNAME_UPDATE.value == "username:update"

    response = get_permissions_response()
    grouped_values = {
        permission.value
        for group in response.groups
        if group.name == "个人资料"
        for permission in group.permissions
    }

    assert grouped_values == {"username:update"}
    metadata = next(
        p for p in response.all_permissions if p.value == "username:update"
    )
    assert metadata.label == "修改用户名"


def test_default_roles_include_username_update_for_user_not_guest() -> None:
    roles = {role["name"]: role for role in RBACManager().get_default_roles()}

    assert Permission.USERNAME_UPDATE.value in roles["user"]["permissions"]
    assert Permission.USERNAME_UPDATE.value not in roles["guest"]["permissions"]
    assert Permission.USERNAME_UPDATE.value in roles["admin"]["permissions"]


@pytest.mark.asyncio
async def test_oauth_blocks_new_user_when_registration_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = OAuthService()
    storage = SimpleNamespace(
        get_by_oauth=AsyncMock(return_value=None),
        get_by_email=AsyncMock(return_value=None),
        create=AsyncMock(),
    )
    service.storage = storage  # type: ignore[assignment]

    import src.infra.auth.oauth as oauth_module

    monkeypatch.setattr(oauth_module.settings, "ENABLE_REGISTRATION", False)

    user_info = OAuthUserInfo(
        provider=OAuthProvider.GITHUB,
        oauth_id="gh-1",
        email="new@example.com",
        username="newuser",
        avatar_url=None,
    )

    result = await service._find_or_create_user(user_info)

    assert result is None
    storage.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_allows_existing_user_when_registration_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(
        id="u-1",
        model_dump=lambda: {
            "id": "u-1",
            "username": "existing",
            "email": "existing@example.com",
            "roles": ["user"],
            "is_active": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        },
    )
    service = OAuthService()
    storage = SimpleNamespace(
        get_by_oauth=AsyncMock(return_value=existing),
        get_by_email=AsyncMock(),
        create=AsyncMock(),
    )
    service.storage = storage  # type: ignore[assignment]

    import src.infra.auth.oauth as oauth_module

    monkeypatch.setattr(oauth_module.settings, "ENABLE_REGISTRATION", False)

    user_info = OAuthUserInfo(
        provider=OAuthProvider.GITHUB,
        oauth_id="gh-1",
        email="existing@example.com",
        username="existing",
        avatar_url=None,
    )

    result = await service._find_or_create_user(user_info)

    assert result is not None
    assert result.username == "existing"
    storage.create.assert_not_awaited()
