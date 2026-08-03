"""OA SSO login route."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.infra.auth.oa_login import OaLoginNotProvisionedError, login_or_provision_from_workcode
from src.infra.auth.oa_sso import OASsoError, OASsoService
from src.infra.auth.session import SessionStoreError
from src.infra.logging import get_logger
from src.kernel.config import settings
from src.kernel.exceptions import AccountNotActiveError
from src.kernel.schemas.user import Token

from .rate_limiter import get_rate_limiter
from .utils import _get_client_ip

router = APIRouter()
logger = get_logger(__name__)


class OaSsoLoginRequest(BaseModel):
    token: str = Field(..., min_length=1, description="OA portal SSO token")


@router.post("/login/oa-sso", response_model=Token)
async def oa_sso_login(request: Request, body: OaSsoLoginRequest) -> Token:
    if not settings.OA_SSO_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OA SSO 未启用")

    client_ip = _get_client_ip(request)
    limiter = get_rate_limiter()
    ip_key = limiter.build_key("ratelimit:oa-sso:ip", client_ip)
    allowed, _ = await limiter.check_rate_limit(ip_key, max_requests=10, window_seconds=60)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后重试",
        )

    sso_service = OASsoService()
    try:
        workcode = await sso_service.get_workcode(body.token)
    except OASsoError as exc:
        logger.warning("OA SSO failed from %s: %s", client_ip, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    finally:
        await sso_service.close()

    try:
        return await login_or_provision_from_workcode(workcode)
    except OaLoginNotProvisionedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AccountNotActiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except SessionStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
