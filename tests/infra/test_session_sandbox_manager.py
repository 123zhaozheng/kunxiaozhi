from __future__ import annotations

from collections import OrderedDict

import pytest

from src.infra.sandbox import session_manager as sandbox_module


class _FakeE2BAdapter:
    def __init__(self) -> None:
        self.method_calls: list[str] = []

    def sandbox_is_running(self, _provider_obj) -> bool:
        self.method_calls.append("sandbox_is_running")
        return True

    def extend_timeout(self, _provider_obj, _timeout: int) -> None:
        self.method_calls.append("extend_timeout")

    def get_work_dir(self, _provider_obj) -> str:
        self.method_calls.append("get_work_dir")
        return "/home/user"


@pytest.mark.asyncio
async def test_e2b_cache_hit_runs_sync_sdk_calls_in_blocking_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeE2BAdapter()
    manager = sandbox_module.SessionSandboxManager()
    manager._e2b_adapter = adapter
    manager._cache = OrderedDict({"user-1": ("sandbox-1", object(), object())})

    blocking_calls: list[str] = []

    async def fake_run_blocking_io(func, *args, **kwargs):
        del kwargs
        blocking_calls.append(func.__name__)
        return func(*args)

    async def fake_save_binding(*_args, **_kwargs) -> None:
        return None

    async def fake_ensure_sandbox_mcp(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(sandbox_module, "run_blocking_io", fake_run_blocking_io)
    monkeypatch.setattr(manager, "_save_binding", fake_save_binding)
    monkeypatch.setattr(sandbox_module, "ensure_sandbox_mcp", fake_ensure_sandbox_mcp)
    monkeypatch.setattr(sandbox_module.settings, "E2B_TIMEOUT", 123)

    _backend, work_dir = await manager._get_or_create_e2b("session-1", "user-1")

    assert work_dir == "/home/user"
    assert blocking_calls == ["sandbox_is_running", "extend_timeout", "get_work_dir"]
    assert adapter.method_calls == ["sandbox_is_running", "extend_timeout", "get_work_dir"]


class _FakeOpenSandboxAdapter:
    """Fake OpenSandbox adapter for testing manager dispatch."""

    def __init__(self, work_dir: str = "/root") -> None:
        self.method_calls: list[str] = []
        self._work_dir = work_dir
        self.create_called = False
        self.get_sandbox_called = False

    def sandbox_is_running(self, _provider_obj) -> bool:
        self.method_calls.append("sandbox_is_running")
        return True

    def extend_timeout(self, _provider_obj, _timeout: int) -> None:
        self.method_calls.append("extend_timeout")

    def get_work_dir(self, _provider_obj) -> str:
        self.method_calls.append("get_work_dir")
        return self._work_dir

    def get_sandbox_info(self, _provider_obj) -> dict:
        self.method_calls.append("get_sandbox_info")
        return {"sandbox_id": "osb-1", "state": "running"}

    def create_sandbox(self, user_id=None, envs=None) -> tuple[object, str]:
        self.create_called = True
        return object(), self._work_dir

    def get_sandbox(self, sandbox_id: str) -> object | None:
        self.get_sandbox_called = True
        return object()

    def get_sandbox_id(self, sandbox) -> str:
        return "osb-1"

    def stop_sandbox(self, sandbox) -> None:
        self.method_calls.append("stop_sandbox")


@pytest.mark.asyncio
async def test_opensandbox_cache_hit_runs_sync_sdk_calls_in_blocking_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeOpenSandboxAdapter()
    manager = sandbox_module.SessionSandboxManager()
    manager._opensandbox_adapter = adapter
    manager._cache = OrderedDict({"user-1": ("osb-1", object(), object())})

    blocking_calls: list[str] = []

    async def fake_run_blocking_io(func, *args, **kwargs):
        del kwargs
        blocking_calls.append(func.__name__)
        return func(*args)

    async def fake_save_binding(*_args, **_kwargs) -> None:
        return None

    async def fake_ensure_sandbox_mcp(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(sandbox_module, "run_blocking_io", fake_run_blocking_io)
    monkeypatch.setattr(manager, "_save_binding", fake_save_binding)
    monkeypatch.setattr(sandbox_module, "ensure_sandbox_mcp", fake_ensure_sandbox_mcp)
    monkeypatch.setattr(sandbox_module.settings, "OPENSANDBOX_TIMEOUT", 456)

    _backend, work_dir = await manager._get_or_create_opensandbox("session-1", "user-1")

    assert work_dir == "/root"
    assert blocking_calls == ["sandbox_is_running", "extend_timeout", "get_work_dir"]
    assert adapter.method_calls == ["sandbox_is_running", "extend_timeout", "get_work_dir"]


@pytest.mark.asyncio
async def test_opensandbox_binding_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When cache is empty but MongoDB binding has an id, reconnect via get_sandbox."""
    adapter = _FakeOpenSandboxAdapter()
    manager = sandbox_module.SessionSandboxManager()
    manager._opensandbox_adapter = adapter
    manager._cache = OrderedDict()

    async def fake_run_blocking_io(func, *args, **kwargs):
        del kwargs
        return func(*args)

    async def fake_get_binding(user_id: str):
        return {"sandbox_id": "osb-existing-1", "sandbox_state": "running"}

    async def fake_save_binding(*_args, **_kwargs) -> None:
        return None

    async def fake_ensure_sandbox_mcp(*_args, **_kwargs) -> None:
        return None

    def fake_build_composite(provider_obj, user_id):
        return object()

    monkeypatch.setattr(sandbox_module, "run_blocking_io", fake_run_blocking_io)
    monkeypatch.setattr(manager, "_get_binding", fake_get_binding)
    monkeypatch.setattr(manager, "_save_binding", fake_save_binding)
    monkeypatch.setattr(sandbox_module, "ensure_sandbox_mcp", fake_ensure_sandbox_mcp)
    monkeypatch.setattr(manager, "_build_composite_backend_opensandbox", fake_build_composite)
    monkeypatch.setattr(sandbox_module.settings, "OPENSANDBOX_TIMEOUT", 3600)

    _backend, work_dir = await manager._get_or_create_opensandbox("session-1", "user-1")

    assert adapter.get_sandbox_called is True
    assert adapter.create_called is False
    assert work_dir == "/root"


@pytest.mark.asyncio
async def test_opensandbox_create_when_no_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no cache and no binding, create a new sandbox."""
    adapter = _FakeOpenSandboxAdapter()
    manager = sandbox_module.SessionSandboxManager()
    manager._opensandbox_adapter = adapter
    manager._cache = OrderedDict()

    async def fake_run_blocking_io(func, *args, **kwargs):
        del kwargs
        return func(*args)

    async def fake_get_binding(user_id: str):
        return None

    async def fake_save_binding(*_args, **_kwargs) -> None:
        return None

    async def fake_ensure_sandbox_mcp(*_args, **_kwargs) -> None:
        return None

    async def fake_get_user_env_vars(user_id: str) -> dict:
        return {}

    monkeypatch.setattr(sandbox_module, "run_blocking_io", fake_run_blocking_io)
    monkeypatch.setattr(manager, "_get_binding", fake_get_binding)
    monkeypatch.setattr(manager, "_save_binding", fake_save_binding)
    monkeypatch.setattr(sandbox_module, "ensure_sandbox_mcp", fake_ensure_sandbox_mcp)
    monkeypatch.setattr(manager, "_get_user_env_vars", fake_get_user_env_vars)

    # Mock the OpenSandboxBackend and CompositeBackend to avoid real SDK
    import deepagents.backends as deepagents_backends

    class _FakeComposite:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(deepagents_backends, "CompositeBackend", _FakeComposite)

    _backend, work_dir = await manager._get_or_create_opensandbox("session-1", "user-1")

    assert adapter.create_called is True
    assert adapter.get_sandbox_called is False
    assert work_dir == "/root"


@pytest.mark.asyncio
async def test_opensandbox_stop_pauses_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() should call adapter.stop_sandbox and save binding as paused."""
    adapter = _FakeOpenSandboxAdapter()
    manager = sandbox_module.SessionSandboxManager()
    manager._opensandbox_adapter = adapter
    manager._cache = OrderedDict({"user-1": ("osb-1", object(), object())})

    async def fake_run_blocking_io(func, *args, **kwargs):
        del kwargs
        return func(*args)

    saved_bindings: list[tuple] = []

    async def fake_save_binding(user_id, sandbox_id, state, **kwargs):
        saved_bindings.append((user_id, sandbox_id, state))

    monkeypatch.setattr(sandbox_module, "run_blocking_io", fake_run_blocking_io)
    monkeypatch.setattr(manager, "_save_binding", fake_save_binding)

    result = await manager._stop_opensandbox("user-1")

    assert result is True
    assert "stop_sandbox" in adapter.method_calls
    assert saved_bindings == [("user-1", "osb-1", "paused")]
    assert "user-1" not in manager._cache


@pytest.mark.asyncio
async def test_opensandbox_stop_returns_false_when_no_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeOpenSandboxAdapter()
    manager = sandbox_module.SessionSandboxManager()
    manager._opensandbox_adapter = adapter
    manager._cache = OrderedDict()

    async def fake_run_blocking_io(func, *args, **kwargs):
        del kwargs
        return func(*args)

    monkeypatch.setattr(sandbox_module, "run_blocking_io", fake_run_blocking_io)

    result = await manager._stop_opensandbox("user-1")

    assert result is False
