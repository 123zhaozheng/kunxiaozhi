"""Tests for WeCom Redis status helpers."""

from __future__ import annotations

import json

import pytest

from src.infra.agent.wecom.state import ConnectionState
from src.infra.agent.wecom.status import (
    WeComStatusReasonCode,
    map_error_to_reason_code,
    read_wecom_status,
    resolve_wecom_status,
    wecom_status_redis_key,
    write_wecom_status,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _ReconnectExhausted(Exception):
    pass


class _AuthFailure(Exception):
    pass


def test_map_error_to_reason_code_by_type_name() -> None:
    assert (
        map_error_to_reason_code(_ReconnectExhausted("max"))
        == WeComStatusReasonCode.DISCONNECTED
    )
    err = type("WSReconnectExhaustedError", (Exception,), {})()
    assert map_error_to_reason_code(err) == WeComStatusReasonCode.RECONNECT_EXHAUSTED
    auth_err = type("WSAuthFailureError", (Exception,), {})()
    assert map_error_to_reason_code(auth_err) == WeComStatusReasonCode.AUTH_FAILED


def test_map_error_auth_message() -> None:
    assert (
        map_error_to_reason_code(Exception("Authentication failed: bad secret"))
        == WeComStatusReasonCode.AUTH_FAILED
    )


@pytest.mark.asyncio
async def test_write_and_read_wecom_status(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(
        "src.infra.agent.wecom.status.get_redis_client",
        lambda: fake,
    )

    await write_wecom_status(
        "preset-1",
        state=ConnectionState.CONNECTED,
        reason_code=None,
        node_id="node-a",
        aibotid="bot-1",
    )

    key = wecom_status_redis_key("preset-1")
    assert key in fake._data
    stored = json.loads(fake._data[key])
    assert stored["state"] == "connected"
    assert stored["preset_id"] == "preset-1"
    assert stored["node_id"] == "node-a"

    read_back = await read_wecom_status("preset-1")
    assert read_back is not None
    assert read_back["state"] == "connected"


@pytest.mark.asyncio
async def test_resolve_wecom_status_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(
        "src.infra.agent.wecom.status.get_redis_client",
        lambda: fake,
    )

    no_config = await resolve_wecom_status("p1", has_wecom=False)
    assert no_config["reason_detail"] == "wecom_not_configured"

    configured = await resolve_wecom_status("p2", has_wecom=True)
    assert configured["state"] == "disconnected"
    assert configured["reason_code"] is None