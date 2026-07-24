"""Scoped WSS and HTTPS transport configuration for WeCom."""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

import httpx

from src.infra.logging import get_logger
from src.kernel.schemas.wecom_network import (
    WECOM_OFFICIAL_WEBSOCKET_URL,
    WeComNetworkConfig,
    WeComNetworkMode,
)

logger = get_logger(__name__)


class WeComNetworkError(RuntimeError):
    reason_code = "network_failed"


class WeComTLSVerificationError(WeComNetworkError):
    reason_code = "tls_verify_failed"


class WeComMediaDownloadError(WeComNetworkError):
    reason_code = "media_download_failed"


class WeComMediaTooLargeError(WeComMediaDownloadError):
    reason_code = "media_too_large"


@dataclass(frozen=True)
class WeComDownloadedMedia:
    content: bytes
    filename: str | None


def _build_ssl_context(config: WeComNetworkConfig) -> ssl.SSLContext:
    if not config.ca_bundle_path:
        return ssl.create_default_context()
    ca_path = Path(config.ca_bundle_path)
    if not ca_path.is_file():
        raise WeComTLSVerificationError("configured CA bundle is not a readable file")
    try:
        return ssl.create_default_context(cafile=str(ca_path))
    except (OSError, ssl.SSLError) as exc:
        raise WeComTLSVerificationError("failed to load configured CA bundle") from exc


def _proxy_url(config: WeComNetworkConfig) -> str | None:
    if config.mode != WeComNetworkMode.FORWARD_PROXY:
        return None
    parsed = urlsplit(config.forward_proxy_url)
    if not config.forward_proxy_username and not config.forward_proxy_password:
        return config.forward_proxy_url
    username = quote(config.forward_proxy_username, safe="")
    password = quote(config.forward_proxy_password, safe="")
    credentials = username
    if config.forward_proxy_password:
        credentials = f"{credentials}:{password}"
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (
            parsed.scheme,
            f"{credentials}@{host}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _content_disposition_filename(header: str) -> str | None:
    if not header:
        return None
    utf8_match = re.search(
        r"filename\*=UTF-8''([^;\s]+)",
        header,
        flags=re.IGNORECASE,
    )
    if utf8_match:
        return unquote(utf8_match.group(1))
    filename_match = re.search(
        r'filename="?([^";]+)"?',
        header,
        flags=re.IGNORECASE,
    )
    if filename_match:
        return filename_match.group(1).strip()
    return None


class WeComNetworkTransport:
    """Build clients scoped to WeCom so other application traffic is unaffected."""

    def __init__(self, config: WeComNetworkConfig) -> None:
        self.config = config
        self._ssl_context = _build_ssl_context(config)

    @property
    def websocket_url(self) -> str:
        if self.config.mode == WeComNetworkMode.REVERSE_GATEWAY:
            return self.config.websocket_url
        return WECOM_OFFICIAL_WEBSOCKET_URL

    def websocket_options(self) -> dict[str, Any]:
        return {
            "proxy": _proxy_url(self.config),
            "ssl": self._ssl_context,
            "open_timeout": float(self.config.connect_timeout_seconds),
        }

    def media_request_url(self, callback_url: str) -> str:
        parsed = urlsplit(callback_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise WeComMediaDownloadError("WeCom media callback URL must be HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise WeComMediaDownloadError("WeCom media callback URL contains credentials")
        if self.config.mode != WeComNetworkMode.REVERSE_GATEWAY:
            return callback_url
        separator = "&" if urlsplit(self.config.media_gateway_url).query else "?"
        return f"{self.config.media_gateway_url}{separator}{urlencode({'target': callback_url})}"

    async def download_media(
        self,
        callback_url: str,
        aes_key: str = "",
    ) -> WeComDownloadedMedia:
        request_url = self.media_request_url(callback_url)
        timeout = httpx.Timeout(float(self.config.media_download_timeout_seconds))
        try:
            async with httpx.AsyncClient(
                proxy=_proxy_url(self.config),
                verify=self._ssl_context,
                timeout=timeout,
                trust_env=False,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", request_url) as response:
                    response.raise_for_status()
                    length_header = response.headers.get("content-length")
                    if length_header:
                        try:
                            if int(length_header) > self.config.media_max_bytes:
                                raise WeComMediaTooLargeError(
                                    "WeCom media exceeds configured size limit"
                                )
                        except ValueError:
                            pass
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self.config.media_max_bytes:
                            raise WeComMediaTooLargeError(
                                "WeCom media exceeds configured size limit"
                            )
                    filename = _content_disposition_filename(
                        response.headers.get("content-disposition", "")
                    )
        except WeComNetworkError:
            raise
        except httpx.TimeoutException as exc:
            raise WeComMediaDownloadError("WeCom media download timed out") from exc
        except httpx.HTTPError as exc:
            raise WeComMediaDownloadError(
                f"WeCom media download failed ({type(exc).__name__})"
            ) from exc

        raw = bytes(content)
        if aes_key:
            try:
                from wecom_aibot_sdk.crypto import decrypt_file

                raw = decrypt_file(raw, aes_key)
            except Exception as exc:
                raise WeComMediaDownloadError("WeCom media decryption failed") from exc
        return WeComDownloadedMedia(content=raw, filename=filename)

    async def probe_websocket(self) -> None:
        """Open and close the configured WSS endpoint without sending credentials."""
        try:
            from websockets.asyncio.client import connect

            async with connect(
                self.websocket_url,
                ping_interval=None,
                close_timeout=5,
                **self.websocket_options(),
            ):
                return
        except ssl.SSLError as exc:
            raise WeComTLSVerificationError("WeCom WSS TLS verification failed") from exc
        except Exception as exc:
            raise WeComNetworkError(f"WeCom WSS probe failed ({type(exc).__name__})") from exc
