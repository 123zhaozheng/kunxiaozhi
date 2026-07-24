"""OA SSO client: exchange portal token for workcode via bank SSO API."""

from __future__ import annotations

import httpx

from src.infra.crypto.sm2_utils import SM2Utils
from src.infra.logging import get_logger
from src.kernel.config import settings

logger = get_logger(__name__)


class OASsoError(Exception):
    """OA SSO verification failed."""


class OASsoService:
    def __init__(
        self,
        base_url: str | None = None,
        public_key: str | None = None,
        channel_id: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.OA_SSO_BASE_URL).rstrip("/")
        self.public_key = public_key or settings.OA_SSO_PUBLIC_KEY
        self.channel_id = channel_id or settings.OA_SSO_CHANNEL_ID
        self._client = httpx.AsyncClient(timeout=settings.OA_SSO_TIMEOUT_SECONDS)

        if not self.public_key:
            logger.warning("OA_SSO_PUBLIC_KEY is not set; OA SSO will not work")

    async def close(self) -> None:
        await self._client.aclose()

    async def get_workcode(self, token: str) -> str:
        if not token:
            raise OASsoError("token 不能为空")
        if not self.public_key:
            raise OASsoError("OA SSO 未配置公钥")

        current_token = token
        for attempt in range(2):
            plaintext = f"{self.channel_id}-{current_token}"
            try:
                encrypted_hex = SM2Utils.encrypt(self.public_key, plaintext)
            except (ValueError, RuntimeError) as exc:
                raise OASsoError("加密 OA 凭证失败") from exc

            url = (
                f"{self.base_url}/api/nsh/ssotoken/getssotoken"
                f"?channelid={self.channel_id}&Encrypted={encrypted_hex}"
            )
            try:
                response = await self._client.post(url)
                response.raise_for_status()
            except httpx.RequestError as exc:
                logger.error("OA SSO request error (attempt %s): %s", attempt + 1, exc)
                raise OASsoError("请求 OA SSO 接口失败") from exc
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "OA SSO HTTP error (attempt %s): %s",
                    attempt + 1,
                    exc.response.status_code,
                )
                raise OASsoError("OA SSO 接口返回错误") from exc

            data = response.json()
            status = data.get("status")

            if status == "0":
                workcode = data.get("workcode")
                if not workcode:
                    raise OASsoError("OA SSO 返回数据缺少工号")
                return str(workcode).strip()

            if status == "1" and attempt == 0:
                new_token = data.get("token")
                if not new_token:
                    raise OASsoError("OA SSO 凭证已过期")
                current_token = new_token
                logger.info("OA SSO token refreshed, retrying")
                continue

            msg = data.get("message", "OA SSO 认证失败")
            raise OASsoError(str(msg))

        raise OASsoError("OA SSO 认证在两次尝试后仍然失败")
