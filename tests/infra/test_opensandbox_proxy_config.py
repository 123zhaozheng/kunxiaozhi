"""Tests for OpenSandboxSandboxAdapter proxy passthrough.

Verifies that OPENSANDBOX_USE_SERVER_PROXY flows into ConnectionConfigSync.use_server_proxy
in both the adapter (session_manager path) and the factory (base.py create_opensandbox path).
"""

from __future__ import annotations

import pytest

from src.infra.sandbox import session_manager as sandbox_module


def _make_adapter(**kwargs) -> sandbox_module.OpenSandboxSandboxAdapter:
    defaults = {
        "domain": "http://localhost:8090",
        "api_key": "key",
        "image": "ubuntu",
        "timeout": 3600,
        "work_dir": "/root",
        "use_server_proxy": True,
    }
    defaults.update(kwargs)
    return sandbox_module.OpenSandboxSandboxAdapter(**defaults)


def test_adapter_connection_config_passes_use_server_proxy_true() -> None:
    adapter = _make_adapter(use_server_proxy=True)
    cfg = adapter._get_connection_config()
    assert cfg.use_server_proxy is True


def test_adapter_connection_config_passes_use_server_proxy_false() -> None:
    adapter = _make_adapter(use_server_proxy=False)
    cfg = adapter._get_connection_config()
    assert cfg.use_server_proxy is False


def test_adapter_sync_from_settings_reads_proxy_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _make_adapter(use_server_proxy=False)
    # settings says True now
    monkeypatch.setattr(sandbox_module.settings, "OPENSANDBOX_USE_SERVER_PROXY", True)
    monkeypatch.setattr(sandbox_module.settings, "OPENSANDBOX_DOMAIN", "http://new:8090")
    adapter._sync_from_settings()
    assert adapter._use_server_proxy is True
    cfg = adapter._get_connection_config()
    assert cfg.use_server_proxy is True
    assert cfg.domain == "http://new:8090"


def test_factory_create_opensandbox_passes_use_server_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory path (SandboxFactory.create_opensandbox) must also honor the setting."""
    captured: dict = {}

    class _FakeConnectionConfig:
        def __init__(self, *, domain=None, api_key=None, use_server_proxy=False):
            captured["domain"] = domain
            captured["use_server_proxy"] = use_server_proxy

    class _FakeSandbox:
        id = "sb-fake"

        def kill(self) -> None:
            pass

    class _FakeSandboxSync:
        @staticmethod
        def create(image, *, timeout, env, connection_config):
            captured["image"] = image
            return _FakeSandbox()

    import sys
    import types

    fake_mod = types.ModuleType("opensandbox.config")
    fake_mod.ConnectionConfigSync = _FakeConnectionConfig
    fake_sync_mod = types.ModuleType("opensandbox.sync.sandbox")
    fake_sync_mod.SandboxSync = _FakeSandboxSync
    monkeypatch.setitem(sys.modules, "opensandbox.config", fake_mod)
    monkeypatch.setitem(sys.modules, "opensandbox.sync.sandbox", fake_sync_mod)

    from src.infra.sandbox.base import SandboxFactory

    monkeypatch.setattr(sandbox_module.settings, "OPENSANDBOX_USE_SERVER_PROXY", True)

    SandboxFactory._sandbox_registry.clear()
    SandboxFactory.create_opensandbox(
        domain="http://localhost:8090",
        api_key="key",
        image="ubuntu",
        timeout=60,
        work_dir="/root",
        env={},
    )

    assert captured["use_server_proxy"] is True

    # And when disabled
    monkeypatch.setattr(sandbox_module.settings, "OPENSANDBOX_USE_SERVER_PROXY", False)
    captured.clear()
    SandboxFactory.create_opensandbox(
        domain="http://localhost:8090",
        api_key="key",
        image="ubuntu",
        timeout=60,
        work_dir="/root",
        env={},
    )
    assert captured["use_server_proxy"] is False
