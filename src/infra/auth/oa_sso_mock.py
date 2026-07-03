"""Dev-only OA SSO mock: resolve portal token to workcode without calling bank API."""

from __future__ import annotations

import re

from src.infra.auth.oa_sso import OASsoError
from src.kernel.config import settings

_MOCK_TOKEN_PREFIX = "mock:"


def is_oa_sso_mock_active() -> bool:
    return bool(settings.OA_SSO_ENABLED and settings.OA_SSO_MOCK_ENABLED)


def resolve_mock_workcode(token: str) -> str:
    """
    Map token to workcode when mock is enabled.

    Accepted forms:
    - mock:<workcode>
    - mock-<workcode>
    - plain workcode (alphanumeric, 3–32 chars) when mock is on
    """
    raw = token.strip()
    if not raw:
        raise OASsoError("token 不能为空")

    if raw.lower().startswith(_MOCK_TOKEN_PREFIX):
        workcode = raw[len(_MOCK_TOKEN_PREFIX) :].strip()
    elif raw.lower().startswith("mock-"):
        workcode = raw[5:].strip()
    else:
        workcode = raw

    if not workcode or not re.fullmatch(r"[A-Za-z0-9_\-]{3,32}", workcode):
        raise OASsoError(
            "Mock 模式下请使用 mock:工号 或 3–32 位字母数字工号作为 token"
        )
    return workcode