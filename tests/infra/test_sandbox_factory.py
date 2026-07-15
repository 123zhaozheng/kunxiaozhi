from __future__ import annotations

import pytest

from src.infra.sandbox.base import OpenSandboxConfig, SandboxFactory


@pytest.fixture(autouse=True)
def _clear_sandbox_factory_registry() -> None:
    SandboxFactory._sandbox_registry.clear()
    SandboxFactory._run_id_to_sandbox.clear()


@pytest.mark.asyncio
async def test_close_sandbox_offloads_provider_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inside_blocking_io = False

    class _DaytonaProvider:
        __module__ = "daytona.fake"

        def __init__(self) -> None:
            self.deleted = False

        def delete(self) -> None:
            assert inside_blocking_io, "provider delete must be offloaded"
            self.deleted = True

    provider = _DaytonaProvider()
    SandboxFactory._sandbox_registry["sandbox-1"] = (object(), provider)

    async def _fake_run_blocking_io(func, /, *args, **kwargs):
        nonlocal inside_blocking_io
        assert inside_blocking_io is False
        inside_blocking_io = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_blocking_io = False

    monkeypatch.setattr(
        "src.infra.sandbox.base.run_blocking_io",
        _fake_run_blocking_io,
        raising=False,
    )

    closed = await SandboxFactory.close_sandbox("sandbox-1")

    assert closed is True
    assert provider.deleted is True
    assert "sandbox-1" not in SandboxFactory._sandbox_registry


@pytest.mark.asyncio
async def test_close_sandbox_calls_kill_for_opensandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenSandbox provider should be killed via kill() on close."""

    class _OpenSandboxProvider:
        __module__ = "opensandbox.sync.sandbox"

        def __init__(self) -> None:
            self.killed = False

        def kill(self) -> None:
            self.killed = True

    provider = _OpenSandboxProvider()
    SandboxFactory._sandbox_registry["osb-1"] = (object(), provider)

    async def _fake_run_blocking_io(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "src.infra.sandbox.base.run_blocking_io",
        _fake_run_blocking_io,
        raising=False,
    )

    closed = await SandboxFactory.close_sandbox("osb-1")

    assert closed is True
    assert provider.killed is True
    assert "osb-1" not in SandboxFactory._sandbox_registry


def test_create_opensandbox_registers_and_returns_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_opensandbox should register the sandbox in the registry."""

    class _FakeSandboxSync:
        __module__ = "opensandbox.sync.sandbox"

        def __init__(self) -> None:
            self.id = "osb-fake-1"
            self.killed = False

        def kill(self) -> None:
            self.killed = True

    fake_sandbox = _FakeSandboxSync()

    def _fake_create(image, *, timeout=None, env=None, connection_config=None, **kwargs):
        assert image == "ubuntu"
        return fake_sandbox

    monkeypatch.setattr(
        "opensandbox.sync.sandbox.SandboxSync.create",
        classmethod(lambda cls, *args, **kwargs: _fake_create(*args, **kwargs)),
    )
    monkeypatch.setattr(
        "opensandbox.config.ConnectionConfigSync",
        lambda *args, **kwargs: object(),
    )

    backend = SandboxFactory.create_opensandbox(
        domain="internal.example.com",
        api_key="key123",
        image="ubuntu",
        timeout=1800,
    )

    assert backend.id == "osb-fake-1"
    assert "osb-fake-1" in SandboxFactory._sandbox_registry
    registered_backend, registered_provider = SandboxFactory._sandbox_registry["osb-fake-1"]
    assert registered_backend is backend
    assert registered_provider is fake_sandbox


def test_get_sandbox_config_from_settings_opensandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infra.sandbox.base import get_sandbox_config_from_settings
    from src.kernel.config import settings

    monkeypatch.setattr(settings, "SANDBOX_PLATFORM", "opensandbox")
    monkeypatch.setattr(settings, "OPENSANDBOX_DOMAIN", "sb.internal")
    monkeypatch.setattr(settings, "OPENSANDBOX_API_KEY", "secret")
    monkeypatch.setattr(settings, "OPENSANDBOX_IMAGE", "python:3.11")
    monkeypatch.setattr(settings, "OPENSANDBOX_TIMEOUT", 7200)
    monkeypatch.setattr(settings, "OPENSANDBOX_WORK_DIR", "/workspace")

    config = get_sandbox_config_from_settings()

    assert isinstance(config, OpenSandboxConfig)
    assert config.domain == "sb.internal"
    assert config.api_key == "secret"
    assert config.image == "python:3.11"
    assert config.timeout == 7200
    assert config.work_dir == "/workspace"
