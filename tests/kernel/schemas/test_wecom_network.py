from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.kernel.schemas.wecom_network import (
    WECOM_OFFICIAL_WEBSOCKET_URL,
    WeComNetworkConfig,
    WeComNetworkConfigUpdate,
    WeComNetworkMode,
)


def test_direct_mode_preserves_official_endpoint() -> None:
    config = WeComNetworkConfig(
        mode=WeComNetworkMode.DIRECT,
        websocket_url="wss://ignored.example/ws",
    )

    assert config.websocket_url == WECOM_OFFICIAL_WEBSOCKET_URL


def test_reverse_gateway_requires_wss_and_https_urls() -> None:
    config = WeComNetworkConfig(
        mode=WeComNetworkMode.REVERSE_GATEWAY,
        websocket_url="wss://dmz.example/wecom/ws",
        media_gateway_url="https://dmz.example/wecom/media",
    )

    assert config.websocket_url.endswith("/wecom/ws")
    assert config.media_gateway_url.endswith("/wecom/media")

    with pytest.raises(ValidationError):
        WeComNetworkConfig(
            mode=WeComNetworkMode.REVERSE_GATEWAY,
            websocket_url="ws://dmz.example/wecom/ws",
            media_gateway_url="https://dmz.example/wecom/media",
        )


def test_forward_proxy_rejects_embedded_credentials() -> None:
    with pytest.raises(ValidationError):
        WeComNetworkConfig(
            mode=WeComNetworkMode.FORWARD_PROXY,
            forward_proxy_url="http://user:secret@proxy.example:8080",
        )


def test_update_empty_password_preserves_current_secret() -> None:
    update = WeComNetworkConfigUpdate(
        mode=WeComNetworkMode.FORWARD_PROXY,
        forward_proxy_url="http://proxy.example:8080",
        forward_proxy_username="svc",
        forward_proxy_password="",
    )

    merged = update.merge_secret("existing-secret")

    assert merged.forward_proxy_password == "existing-secret"


def test_update_can_explicitly_clear_proxy_password() -> None:
    update = WeComNetworkConfigUpdate(
        mode=WeComNetworkMode.FORWARD_PROXY,
        forward_proxy_url="http://proxy.example:8080",
        clear_forward_proxy_password=True,
    )

    assert update.merge_secret("existing-secret").forward_proxy_password == ""
