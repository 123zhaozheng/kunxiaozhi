from __future__ import annotations

from unittest.mock import patch

from src.infra.auth.oa_sso import OASsoService
from src.kernel.config import settings


def test_oa_sso_client_uses_configured_timeout() -> None:
    with patch("src.infra.auth.oa_sso.httpx.AsyncClient") as client_cls:
        OASsoService(
            base_url="https://oa.example.test",
            public_key="test-public-key",
            channel_id="test-channel",
        )

    client_cls.assert_called_once_with(timeout=settings.OA_SSO_TIMEOUT_SECONDS)
