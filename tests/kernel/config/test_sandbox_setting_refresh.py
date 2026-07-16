"""Tests for sandbox-config hot-reload (soft reset of SessionSandboxManager singleton).

Mirrors tests/kernel/config/test_checkpoint_setting_refresh.py: refresh_settings on a
sandbox-affected key must rebuild the SessionSandboxManager singleton, without touching
persisted bindings or running sandboxes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.kernel.config import service as config_service


class _FakeSettingsStorage:
    def __init__(self, value: object) -> None:
        self._value = value

    async def get_raw(self, key: str):
        return SimpleNamespace(key=key, value=self._value)


class _FakeSettingsService:
    def __init__(self, value: object) -> None:
        self._storage = _FakeSettingsStorage(value)


@pytest.mark.asyncio
async def test_refresh_sandbox_setting_rebuilds_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refreshing an OPENSANDBOX_* key must call reset_session_sandbox_manager."""
    reset_calls: list[str] = []

    def _fake_reset() -> None:
        reset_calls.append("reset")

    monkeypatch.setattr(config_service, "_settings_service", _FakeSettingsService("http://new:8090"))
    monkeypatch.setattr(config_service.settings, "OPENSANDBOX_DOMAIN", "http://old:8090")
    monkeypatch.setattr(
        "src.infra.sandbox.session_manager.reset_session_sandbox_manager",
        _fake_reset,
    )

    await config_service.refresh_settings("OPENSANDBOX_DOMAIN")

    assert config_service.settings.OPENSANDBOX_DOMAIN == "http://new:8090"
    assert reset_calls == ["reset"]


@pytest.mark.asyncio
async def test_refresh_non_sandbox_setting_does_not_rebuild_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-sandbox key must NOT rebuild the sandbox manager."""
    reset_calls: list[str] = []

    def _fake_reset() -> None:
        reset_calls.append("reset")

    monkeypatch.setattr(config_service, "_settings_service", _FakeSettingsService("DEBUG"))
    monkeypatch.setattr(config_service.settings, "LOG_LEVEL", "INFO")
    monkeypatch.setattr(
        "src.infra.sandbox.session_manager.reset_session_sandbox_manager",
        _fake_reset,
    )

    await config_service.refresh_settings("LOG_LEVEL")

    assert reset_calls == []


def test_sandbox_affected_settings_covers_all_platforms() -> None:
    """Sanity: the affected set includes keys for all three platforms + the switches."""
    required = {
        "ENABLE_SANDBOX",
        "SANDBOX_PLATFORM",
        "DAYTONA_API_KEY",
        "E2B_API_KEY",
        "OPENSANDBOX_DOMAIN",
        "OPENSANDBOX_USE_SERVER_PROXY",
    }
    assert required <= config_service._SANDBOX_AFFECTED_SETTINGS


def test_sandbox_settings_do_not_require_restart() -> None:
    """Sandbox settings are hot-reloaded now, so they must NOT be flagged restart-required
    (otherwise the frontend would mislead the user)."""
    from src.infra.settings.service import SettingsService

    for key in config_service._SANDBOX_AFFECTED_SETTINGS:
        assert not SettingsService.requires_restart(key), f"{key} should be hot-reloadable"


def test_reset_session_sandbox_manager_clears_singleton() -> None:
    """reset_session_sandbox_manager() must drop the cached singleton so the next get
    rebuilds it (and thus picks up current settings)."""
    import src.infra.sandbox.session_manager as sm

    # Force a singleton instance
    original = sm._session_sandbox_manager
    sm._session_sandbox_manager = object.__new__(sm.SessionSandboxManager)
    try:
        sm.reset_session_sandbox_manager()
        assert sm._session_sandbox_manager is None
    finally:
        sm._session_sandbox_manager = original
