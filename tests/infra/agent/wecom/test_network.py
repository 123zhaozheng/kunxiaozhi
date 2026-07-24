from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from src.infra.agent.wecom.network import (
    WeComMediaDownloadError,
    WeComNetworkTransport,
)
from src.kernel.schemas.wecom_network import WeComNetworkConfig, WeComNetworkMode


def test_reverse_gateway_routes_media_url_as_encoded_target() -> None:
    transport = WeComNetworkTransport(
        WeComNetworkConfig(
            mode=WeComNetworkMode.REVERSE_GATEWAY,
            websocket_url="wss://dmz.example/wecom/ws",
            media_gateway_url="https://dmz.example/wecom/media?source=wecom",
        )
    )
    callback_url = "https://media.work.weixin.qq.com/file?id=abc&sig=secret"

    routed = transport.media_request_url(callback_url)
    query = parse_qs(urlsplit(routed).query)

    assert query["source"] == ["wecom"]
    assert query["target"] == [callback_url]


def test_media_callback_must_be_https() -> None:
    transport = WeComNetworkTransport(WeComNetworkConfig())

    with pytest.raises(WeComMediaDownloadError):
        transport.media_request_url("http://media.example/file")


def test_forward_proxy_is_scoped_into_ws_options() -> None:
    transport = WeComNetworkTransport(
        WeComNetworkConfig(
            mode=WeComNetworkMode.FORWARD_PROXY,
            forward_proxy_url="http://proxy.example:8080",
            forward_proxy_username="svc user",
            forward_proxy_password="s:ecret",
        )
    )

    options = transport.websocket_options()

    assert options["proxy"] == "http://svc%20user:s%3Aecret@proxy.example:8080"
    assert options["open_timeout"] == 10.0


def test_direct_mode_disables_environment_proxy_for_ws() -> None:
    transport = WeComNetworkTransport(WeComNetworkConfig())

    assert transport.websocket_options()["proxy"] is None
