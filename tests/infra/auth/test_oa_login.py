"""Tests for OA workcode login provisioning."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.auth.oa_login import OaLoginNotProvisionedError, login_or_provision_from_workcode


@pytest.mark.asyncio
async def test_login_existing_user_by_workcode():
    mock_user = MagicMock()
    mock_user.id = "u1"
    mock_user.username = "10001"
    mock_user.email = "10001@ksrcb.com"
    mock_user.roles = ["user"]
    mock_user.is_active = True

    with patch("src.infra.auth.oa_login.UserStorage") as Storage:
        storage = Storage.return_value
        storage.get_by_username = AsyncMock(return_value=mock_user)
        storage.touch_updated_at = AsyncMock()

        with patch("src.infra.auth.oa_login.create_access_token", return_value="a"):
            with patch("src.infra.auth.oa_login.create_refresh_token", return_value="r"):
                token = await login_or_provision_from_workcode("10001")

    assert token.access_token == "a"
    storage.touch_updated_at.assert_awaited_once_with("u1")


@pytest.mark.asyncio
async def test_provision_when_missing_and_auto_provision_enabled():
    created = MagicMock()
    created.id = "u2"
    created.username = "20002"
    created.email = "20002@ksrcb.com"
    created.roles = ["user"]
    created.is_active = True

    with patch("src.infra.auth.oa_login.settings") as mock_settings:
        mock_settings.OA_SSO_AUTO_PROVISION = True
        mock_settings.OA_SSO_EMAIL_DOMAIN = "ksrcb.com"
        mock_settings.DEFAULT_USER_ROLE = "user"
        mock_settings.ACCESS_TOKEN_EXPIRE_HOURS = 24

        with patch("src.infra.auth.oa_login.UserStorage") as Storage:
            storage = Storage.return_value
            storage.get_by_username = AsyncMock(side_effect=[None, created])
            storage.list_users = AsyncMock(return_value=[MagicMock()])
            storage.create = AsyncMock(return_value=created)
            storage.touch_updated_at = AsyncMock()

            with patch("src.infra.auth.oa_login.create_access_token", return_value="a"):
                with patch("src.infra.auth.oa_login.create_refresh_token", return_value="r"):
                    token = await login_or_provision_from_workcode("20002")

    assert token.refresh_token == "r"
    storage.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_when_no_user_and_auto_provision_disabled():
    with patch("src.infra.auth.oa_login.settings") as mock_settings:
        mock_settings.OA_SSO_AUTO_PROVISION = False

        with patch("src.infra.auth.oa_login.UserStorage") as Storage:
            storage = Storage.return_value
            storage.get_by_username = AsyncMock(return_value=None)

            with pytest.raises(OaLoginNotProvisionedError):
                await login_or_provision_from_workcode("30003")