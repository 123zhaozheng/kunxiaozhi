from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from src.infra.agent.wecom import bot as bot_module
from src.infra.agent.wecom.bot import WeComBot
from src.infra.agent.wecom.manager import WeComBotManager
from src.infra.agent.wecom.state import ConnectionState


class _FakeWSClient:
    instances: list["_FakeWSClient"] = []
    connect_error: Exception | None = None
    initially_connected = False

    def __init__(self, **_: Any) -> None:
        self.listeners: dict[str, Callable[..., Any]] = {}
        self.is_connected = self.initially_connected
        self.disconnect_awaited = False
        self.instances.append(self)

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.listeners[event] = handler

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        self.disconnect_awaited = True
        self.is_connected = False


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeWSClient.instances.clear()
    _FakeWSClient.connect_error = None
    _FakeWSClient.initially_connected = False
    monkeypatch.setattr(bot_module, "WECOM_AVAILABLE", True)
    monkeypatch.setattr("wecom_aibot_sdk.WSClient", _FakeWSClient)


@pytest.mark.asyncio
async def test_initial_handshake_failure_does_not_report_connected() -> None:
    bot = WeComBot("bot-1", "secret")

    started = await bot.start()

    assert started is True
    assert bot.is_running is True
    assert bot._connection_state == ConnectionState.RECONNECTING
    assert bot._ws_client is _FakeWSClient.instances[0]

    await bot._on_authenticated()
    assert bot._connection_state == ConnectionState.CONNECTED

    await bot.stop()


@pytest.mark.asyncio
async def test_stop_awaits_sdk_disconnect_and_drains_status_tasks() -> None:
    published: list[ConnectionState] = []

    async def _publish_status(
        _aibotid: str,
        *,
        state: ConnectionState,
        **_: Any,
    ) -> None:
        published.append(state)

    bot = WeComBot("bot-1", "secret", status_callback=_publish_status)
    assert await bot.start() is True
    client = _FakeWSClient.instances[0]

    await bot.stop()

    assert client.disconnect_awaited is True
    assert bot._ws_client is None
    assert bot.is_running is False
    assert bot._connection_state == ConnectionState.DISCONNECTED
    assert ConnectionState.DISCONNECTED in published
    assert not bot._status_publish_tasks


@pytest.mark.asyncio
async def test_start_exception_disconnects_partial_client() -> None:
    _FakeWSClient.connect_error = RuntimeError("connect failed before SDK supervision")
    bot = WeComBot("bot-1", "secret")

    started = await bot.start()

    client = _FakeWSClient.instances[0]
    assert started is False
    assert client.disconnect_awaited is True
    assert bot._ws_client is None
    assert bot.is_running is False
    assert bot._connection_state == ConnectionState.FAILED


@pytest.mark.asyncio
async def test_manager_reconcile_preserves_existing_bot_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = WeComBotManager()
    bot = WeComBot("bot-1", "secret")
    bot._running = True
    bot._connection_state = ConnectionState.RECONNECTING
    manager._bots["bot-1"] = bot
    published: list[ConnectionState] = []

    async def _publish(
        _aibotid: str,
        *,
        state: ConnectionState,
        **_: Any,
    ) -> None:
        published.append(state)

    monkeypatch.setattr(manager, "_publish_bot_status", _publish)
    monkeypatch.setattr(manager, "_ensure_lease_refresh_task", lambda _aibotid: None)

    started = await manager._start_bot("bot-1", "secret", replace_existing=False)

    assert started is True
    assert published == [ConnectionState.RECONNECTING]
