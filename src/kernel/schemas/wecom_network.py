"""Deployment-level network settings for WeCom AI Bot transports."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

WECOM_OFFICIAL_WEBSOCKET_URL = "wss://openws.work.weixin.qq.com"
WECOM_NETWORK_CONNECT_TIMEOUT_DEFAULT = 10
WECOM_MEDIA_DOWNLOAD_TIMEOUT_DEFAULT = 30
WECOM_MEDIA_MAX_BYTES_DEFAULT = 20 * 1024 * 1024


class WeComNetworkMode(str, Enum):
    DIRECT = "direct"
    REVERSE_GATEWAY = "reverse_gateway"
    FORWARD_PROXY = "forward_proxy"


def _validate_url(
    value: str,
    *,
    field_name: str,
    schemes: frozenset[str],
    allow_empty: bool = False,
) -> str:
    normalized = value.strip()
    if not normalized and allow_empty:
        return ""
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in schemes or not parsed.hostname:
        choices = ", ".join(sorted(schemes))
        raise ValueError(f"{field_name} must use one of [{choices}] and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not contain credentials")
    return normalized


class WeComNetworkConfig(BaseModel):
    """Internal persisted network configuration, including proxy credentials."""

    model_config = ConfigDict(extra="forbid")

    mode: WeComNetworkMode = WeComNetworkMode.DIRECT
    websocket_url: str = WECOM_OFFICIAL_WEBSOCKET_URL
    media_gateway_url: str = ""
    forward_proxy_url: str = ""
    forward_proxy_username: str = ""
    forward_proxy_password: str = Field(default="", repr=False)
    ca_bundle_path: str = ""
    connect_timeout_seconds: int = Field(
        WECOM_NETWORK_CONNECT_TIMEOUT_DEFAULT,
        ge=1,
        le=120,
    )
    media_download_timeout_seconds: int = Field(
        WECOM_MEDIA_DOWNLOAD_TIMEOUT_DEFAULT,
        ge=1,
        le=300,
    )
    media_max_bytes: int = Field(
        WECOM_MEDIA_MAX_BYTES_DEFAULT,
        ge=1024,
        le=50 * 1024 * 1024,
    )

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "WeComNetworkConfig":
        self.ca_bundle_path = self.ca_bundle_path.strip()
        self.forward_proxy_username = self.forward_proxy_username.strip()

        if self.mode == WeComNetworkMode.DIRECT:
            self.websocket_url = WECOM_OFFICIAL_WEBSOCKET_URL
            return self

        if self.mode == WeComNetworkMode.REVERSE_GATEWAY:
            self.websocket_url = _validate_url(
                self.websocket_url,
                field_name="websocket_url",
                schemes=frozenset({"wss"}),
            )
            self.media_gateway_url = _validate_url(
                self.media_gateway_url,
                field_name="media_gateway_url",
                schemes=frozenset({"https"}),
            )
            return self

        self.websocket_url = WECOM_OFFICIAL_WEBSOCKET_URL
        self.forward_proxy_url = _validate_url(
            self.forward_proxy_url,
            field_name="forward_proxy_url",
            schemes=frozenset({"http", "https"}),
        )
        return self


class WeComNetworkConfigUpdate(BaseModel):
    """Admin update payload. Empty password preserves the persisted secret."""

    model_config = ConfigDict(extra="forbid")

    mode: WeComNetworkMode = WeComNetworkMode.DIRECT
    websocket_url: str = WECOM_OFFICIAL_WEBSOCKET_URL
    media_gateway_url: str = ""
    forward_proxy_url: str = ""
    forward_proxy_username: str = ""
    forward_proxy_password: str | None = Field(default=None, repr=False)
    clear_forward_proxy_password: bool = False
    expected_revision: str | None = None
    ca_bundle_path: str = ""
    connect_timeout_seconds: int = Field(
        WECOM_NETWORK_CONNECT_TIMEOUT_DEFAULT,
        ge=1,
        le=120,
    )
    media_download_timeout_seconds: int = Field(
        WECOM_MEDIA_DOWNLOAD_TIMEOUT_DEFAULT,
        ge=1,
        le=300,
    )
    media_max_bytes: int = Field(
        WECOM_MEDIA_MAX_BYTES_DEFAULT,
        ge=1024,
        le=50 * 1024 * 1024,
    )

    def merge_secret(self, current_password: str) -> WeComNetworkConfig:
        password = current_password
        if self.clear_forward_proxy_password:
            password = ""
        elif self.forward_proxy_password:
            password = self.forward_proxy_password
        return WeComNetworkConfig(
            **self.model_dump(
                exclude={
                    "forward_proxy_password",
                    "clear_forward_proxy_password",
                    "expected_revision",
                }
            ),
            forward_proxy_password=password,
        )


class WeComNetworkConfigResponse(BaseModel):
    mode: WeComNetworkMode
    websocket_url: str
    media_gateway_url: str
    forward_proxy_url: str
    forward_proxy_username: str
    has_forward_proxy_password: bool
    ca_bundle_path: str
    connect_timeout_seconds: int
    media_download_timeout_seconds: int
    media_max_bytes: int
    revision: str
    updated_at: datetime | None = None
    updated_by: str | None = None


class WeComNetworkBotResult(BaseModel):
    preset_id: str
    aibotid: str
    state: str
    reason_code: str | None = None
    reason_detail: str | None = None
    node_id: str | None = None


class WeComNetworkOperationStatus(str, Enum):
    CONNECTED = "connected"
    PARTIAL_FAILURE = "partial_failure"
    ROLLED_BACK = "rolled_back"
    SAVED_UNVERIFIED = "saved_unverified"
    TEST_OK = "test_ok"
    TEST_FAILED = "test_failed"


class WeComNetworkOperationResponse(BaseModel):
    operation_id: str
    status: WeComNetworkOperationStatus
    revision: str
    config: WeComNetworkConfigResponse
    results: list[WeComNetworkBotResult] = Field(default_factory=list)
    detail: str | None = None
