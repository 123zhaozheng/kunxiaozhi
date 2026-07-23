from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.infra.agent.wecom import control
from src.infra.agent.wecom import runtime as wecom_runtime
from src.infra.agent.wecom.control import WeComControlListener, request_wecom_reload
from src.infra.agent.wecom.mode import get_wecom_runtime_mode


def test_wecom_runtime_mode_normalizes_supported_values() -> None:
    assert get_wecom_runtime_mode(" EMBEDDED ") == "embedded"
    assert get_wecom_runtime_mode("external") == "external"
    assert get_wecom_runtime_mode("disabled") == "disabled"


def test_invalid_wecom_runtime_mode_falls_back_to_embedded() -> None:
    assert get_wecom_runtime_mode("unknown") == "embedded"


class _FakeRedis:
    def __init__(self, *, subscriber_count: int = 1, auto_ack: bool = False) -> None:
        self.subscriber_count = subscriber_count
        self.auto_ack = auto_ack
        self.data: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        if self.auto_ack:
            command_id = json.loads(payload)["command_id"]
            self.data[control.wecom_control_result_key(command_id)] = json.dumps(
                {"command_id": command_id, "status": "ok"}
            )
        return self.subscriber_count

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.mark.asyncio
async def test_external_reload_waits_for_owner_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis(auto_ack=True)
    monkeypatch.setattr(control, "get_wecom_runtime_mode", lambda: "external")
    monkeypatch.setattr(control, "get_redis_client", lambda: redis)

    reloaded = await request_wecom_reload("preset-1", requested_by="admin")

    assert reloaded is True
    assert redis.published[0][0] == control.WECOM_CONTROL_CHANNEL
    payload = json.loads(redis.published[0][1])
    assert payload["preset_id"] == "preset-1"
    assert payload["requested_by"] == "admin"


@pytest.mark.asyncio
async def test_external_reload_fails_fast_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis(subscriber_count=0)
    monkeypatch.setattr(control, "get_wecom_runtime_mode", lambda: "external")
    monkeypatch.setattr(control, "get_redis_client", lambda: redis)

    assert await request_wecom_reload("preset-1") is False


@pytest.mark.asyncio
async def test_external_reload_contains_redis_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingRedis:
        async def publish(self, _channel: str, _payload: str) -> int:
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(control, "get_wecom_runtime_mode", lambda: "external")
    monkeypatch.setattr(control, "get_redis_client", _FailingRedis)

    assert await request_wecom_reload("preset-1") is False


@pytest.mark.asyncio
async def test_control_listener_only_owner_writes_ack() -> None:
    redis = _FakeRedis()

    class _Manager:
        _node_id = "node-1"

        def __init__(self, result: bool) -> None:
            self.result = result
            self.reloaded: list[str] = []

        async def reload_preset(self, preset_id: str) -> bool:
            self.reloaded.append(preset_id)
            return self.result

    payload: dict[str, Any] = {
        "command_id": "command-1",
        "action": "reload_preset",
        "preset_id": "preset-1",
    }
    owner = _Manager(True)
    listener = WeComControlListener(owner, redis=redis)

    await listener._handle_message({"data": json.dumps(payload)})

    result = json.loads(redis.data[control.wecom_control_result_key("command-1")])
    assert owner.reloaded == ["preset-1"]
    assert result["status"] == "ok"
    assert result["node_id"] == "node-1"

    redis.data.clear()
    non_owner = _Manager(False)
    listener = WeComControlListener(non_owner, redis=redis)
    await listener._handle_message({"data": json.dumps(payload)})
    assert redis.data == {}


@pytest.mark.asyncio
async def test_external_runtime_starts_and_stops_owned_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _record_async(name: str) -> None:
        calls.append(name)

    class _ControlListener:
        def __init__(self, _manager: Any) -> None:
            calls.append("control:init")

        async def start(self) -> None:
            calls.append("control:start")

        async def stop(self) -> None:
            calls.append("control:stop")

    monkeypatch.setattr(wecom_runtime, "setup_logging", lambda: calls.append("logging"))
    monkeypatch.setattr(
        wecom_runtime,
        "initialize_settings",
        lambda: _record_async("settings:init"),
    )
    monkeypatch.setattr(wecom_runtime, "get_wecom_runtime_mode", lambda: "external")
    monkeypatch.setattr(
        wecom_runtime,
        "ensure_local_filesystem_dirs",
        lambda _settings: calls.append("filesystem"),
    )
    monkeypatch.setattr(
        "src.infra.tracing.init_tracing",
        lambda _settings: calls.append("tracing"),
    )
    monkeypatch.setattr(
        wecom_runtime,
        "_start_support_services",
        lambda: _record_async("support:start"),
    )
    monkeypatch.setattr(
        wecom_runtime,
        "setup_wecom_handler",
        lambda: _record_async("wecom:start"),
    )
    class _Manager:
        async def start(self) -> None:
            calls.append("wecom:reconcile")

    monkeypatch.setattr(wecom_runtime, "get_wecom_bot_manager", _Manager)
    monkeypatch.setattr(wecom_runtime, "WeComControlListener", _ControlListener)
    monkeypatch.setattr(
        wecom_runtime,
        "stop_wecom_bots",
        lambda: _record_async("wecom:stop"),
    )
    monkeypatch.setattr(
        wecom_runtime,
        "_stop_support_services",
        lambda: _record_async("support:stop"),
    )
    monkeypatch.setattr(
        wecom_runtime,
        "close_mongo_client",
        lambda: _record_async("mongo:close"),
    )
    monkeypatch.setattr(
        wecom_runtime,
        "close_redis_client",
        lambda: _record_async("redis:close"),
    )
    monkeypatch.setattr(
        wecom_runtime,
        "shutdown_blocking_io_executor",
        lambda: calls.append("blocking-io:stop"),
    )
    monkeypatch.setattr(wecom_runtime, "_install_signal_handlers", lambda _event: None)
    stop_event = asyncio.Event()
    stop_event.set()

    await wecom_runtime.run_external_wecom_runtime(stop_event=stop_event)

    assert calls == [
        "logging",
        "settings:init",
        "filesystem",
        "tracing",
        "support:start",
        "wecom:start",
        "control:init",
        "control:start",
        "wecom:reconcile",
        "control:stop",
        "wecom:stop",
        "support:stop",
        "mongo:close",
        "redis:close",
        "blocking-io:stop",
    ]


@pytest.mark.asyncio
async def test_external_runtime_refuses_embedded_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _initialize() -> None:
        return None

    monkeypatch.setattr(wecom_runtime, "setup_logging", lambda: None)
    monkeypatch.setattr(wecom_runtime, "initialize_settings", _initialize)
    monkeypatch.setattr(wecom_runtime, "get_wecom_runtime_mode", lambda: "embedded")

    with pytest.raises(RuntimeError, match="WECOM_RUNTIME_MODE=external"):
        await wecom_runtime.run_external_wecom_runtime(stop_event=asyncio.Event())
