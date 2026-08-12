"""Business logic: OA workcode -> LambChat JWT (find or provision user)."""

from __future__ import annotations

import secrets

from src.infra.auth.jwt import create_token_pair
from src.infra.logging import get_logger
from src.infra.user.storage import UserStorage
from src.kernel.config import settings
from src.kernel.exceptions import AccountNotActiveError, ValidationError
from src.kernel.schemas.user import Token, UserCreate

logger = get_logger(__name__)


class OaLoginNotProvisionedError(Exception):
    """Workcode is valid but auto-provision is disabled and user does not exist."""


async def login_or_provision_from_workcode(workcode: str) -> Token:
    """
    Resolve workcode to a user and issue tokens.

    Username must equal workcode. Creates user when missing if auto-provision enabled.
    """
    workcode = workcode.strip()
    if not workcode:
        raise ValidationError("工号无效")

    storage = UserStorage()
    user = await storage.get_by_username(workcode)

    if not user:
        if not settings.OA_SSO_AUTO_PROVISION:
            raise OaLoginNotProvisionedError("工号尚未开通系统账号，请联系管理员")

        email = f"{workcode}@{settings.OA_SSO_EMAIL_DOMAIN}"
        existing_users = await storage.list_users(limit=1)
        if not existing_users:
            roles = ["admin"]
            skip_verification = True
        else:
            roles = [settings.DEFAULT_USER_ROLE or "user"]
            skip_verification = True

        user_data = UserCreate(
            username=workcode,
            email=email,
            password=secrets.token_urlsafe(32),
            roles=roles,
            skip_verification=skip_verification,
        )
        try:
            user = await storage.create(user_data, generated_password=True)
            logger.info("[OA SSO] Provisioned user username=%s", workcode)
        except ValidationError as exc:
            if "已存在" in str(exc):
                user = await storage.get_by_username(workcode)
                if not user:
                    raise
            else:
                raise

    if user.is_active is False:
        raise AccountNotActiveError("账户未激活，请验证邮箱后登录", user.email)

    await storage.touch_updated_at(user.id)

    access_token, refresh_token = await create_token_pair(
        user.id, user.username, user.credential_version
    )

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_HOURS * 3600,
    )
