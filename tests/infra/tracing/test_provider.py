"""Unit tests for tracing provider resolve / env apply / Phoenix init gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infra.tracing import phoenix as phoenix_module
from src.infra.tracing.provider import (
    apply_tracing_env,
    init_tracing,
    resolve_tracing_provider,
    shutdown_tracing,
)
from src.kernel.config.definitions import SETTING_DEFINITIONS


def _settings(**kwargs):
    defaults = {
        "TRACING_PROVIDER": "none",
        "LANGSMITH_API_KEY": None,
        "LANGSMITH_PROJECT": "lamb-agent",
        "LANGSMITH_API_URL": "https://api.smith.langchain.com",
        "LANGSMITH_SAMPLE_RATE": 1.0,
        "PHOENIX_COLLECTOR_ENDPOINT": "http://localhost:6006/v1/traces",
        "PHOENIX_PROJECT_NAME": "lamb-agent",
        "PHOENIX_API_KEY": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestResolveTracingProvider:
    def test_provider_is_the_only_tracing_enable_setting(self):
        assert "TRACING_PROVIDER" in SETTING_DEFINITIONS
        assert "LANGSMITH_TRACING" not in SETTING_DEFINITIONS

    def test_defaults_to_none(self):
        assert resolve_tracing_provider(_settings()) == "none"

    def test_explicit_none(self):
        assert resolve_tracing_provider(_settings(TRACING_PROVIDER="none")) == "none"

    def test_explicit_phoenix(self):
        assert (
            resolve_tracing_provider(_settings(TRACING_PROVIDER="phoenix"))
            == "phoenix"
        )

    def test_explicit_langsmith(self):
        assert resolve_tracing_provider(_settings(TRACING_PROVIDER="langsmith")) == (
            "langsmith"
        )

    def test_empty_provider_disables_tracing(self):
        assert resolve_tracing_provider(_settings(TRACING_PROVIDER="")) == "none"

    def test_unknown_provider_disables_tracing(self):
        assert resolve_tracing_provider(_settings(TRACING_PROVIDER="both")) == "none"

    @pytest.mark.parametrize("value", ["NONE", " LangSmith ", "PHOENIX"])
    def test_case_and_whitespace_normalized(self, value):
        expected = value.strip().lower()
        assert resolve_tracing_provider(_settings(TRACING_PROVIDER=value)) == expected


class TestApplyTracingEnv:
    def test_langsmith_writes_true_and_keys(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)

        settings = _settings(
            TRACING_PROVIDER="langsmith",
            LANGSMITH_API_KEY="sk-test",
            LANGSMITH_PROJECT="proj",
        )
        provider = apply_tracing_env(settings)

        assert provider == "langsmith"
        import os

        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGSMITH_API_KEY"] == "sk-test"
        assert os.environ["LANGSMITH_PROJECT"] == "proj"

    def test_none_forces_langsmith_false(self, monkeypatch):
        import os

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_OTEL_ENABLED", "true")

        settings = _settings(TRACING_PROVIDER="none")
        provider = apply_tracing_env(settings)

        assert provider == "none"
        assert os.environ["LANGSMITH_TRACING"] == "false"
        assert os.environ["LANGSMITH_OTEL_ENABLED"] == "false"

    def test_phoenix_forces_langsmith_false_and_sets_phoenix_env(self, monkeypatch):
        import os

        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
        monkeypatch.delenv("PHOENIX_PROJECT_NAME", raising=False)

        settings = _settings(
            TRACING_PROVIDER="phoenix",
            PHOENIX_COLLECTOR_ENDPOINT="http://phoenix:6006/v1/traces",
            PHOENIX_PROJECT_NAME="my-proj",
            PHOENIX_API_KEY="pk-1",
        )
        provider = apply_tracing_env(settings)

        assert provider == "phoenix"
        assert os.environ["LANGSMITH_TRACING"] == "false"
        assert os.environ["PHOENIX_COLLECTOR_ENDPOINT"] == "http://phoenix:6006/v1/traces"
        assert os.environ["PHOENIX_PROJECT_NAME"] == "my-proj"
        assert os.environ["PHOENIX_API_KEY"] == "pk-1"


class TestInitTracingPhoenixGate:
    @pytest.fixture(autouse=True)
    def _reset_phoenix_state(self):
        phoenix_module._tracer_provider = None
        phoenix_module._initialized = False
        yield
        phoenix_module._tracer_provider = None
        phoenix_module._initialized = False

    def test_phoenix_registers_once(self, monkeypatch):
        calls: list[dict] = []

        def fake_register(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(shutdown=lambda: None)

        monkeypatch.setitem(
            __import__("sys").modules,
            "phoenix.otel",
            SimpleNamespace(register=fake_register),
        )
        # Also patch import path used inside init_phoenix
        import types

        fake_mod = types.ModuleType("phoenix")
        fake_otel = types.ModuleType("phoenix.otel")
        fake_otel.register = fake_register
        fake_mod.otel = fake_otel
        monkeypatch.setitem(__import__("sys").modules, "phoenix", fake_mod)
        monkeypatch.setitem(__import__("sys").modules, "phoenix.otel", fake_otel)

        settings = _settings(TRACING_PROVIDER="phoenix")
        assert init_tracing(settings) == "phoenix"
        assert len(calls) == 1
        assert calls[0]["auto_instrument"] is True
        assert calls[0]["batch"] is True
        assert calls[0]["protocol"] == "http/protobuf"
        assert calls[0]["project_name"] == "lamb-agent"

        # Second init must not re-register
        assert init_tracing(settings) == "phoenix"
        assert len(calls) == 1

    def test_none_does_not_register(self, monkeypatch):
        called = {"n": 0}

        def fake_init_phoenix(**_kwargs):
            called["n"] += 1
            return True

        monkeypatch.setattr(
            "src.infra.tracing.phoenix.init_phoenix", fake_init_phoenix
        )
        assert init_tracing(_settings(TRACING_PROVIDER="none")) == "none"
        assert called["n"] == 0

    def test_langsmith_does_not_register(self, monkeypatch):
        called = {"n": 0}

        def fake_init_phoenix(**_kwargs):
            called["n"] += 1
            return True

        monkeypatch.setattr(
            "src.infra.tracing.phoenix.init_phoenix", fake_init_phoenix
        )
        assert (
            init_tracing(
                _settings(TRACING_PROVIDER="langsmith", LANGSMITH_API_KEY="k")
            )
            == "langsmith"
        )
        assert called["n"] == 0

    def test_phoenix_register_failure_is_soft(self, monkeypatch):
        def boom(**_kwargs):
            raise RuntimeError("collector down")

        import types

        fake_mod = types.ModuleType("phoenix")
        fake_otel = types.ModuleType("phoenix.otel")
        fake_otel.register = boom
        fake_mod.otel = fake_otel
        monkeypatch.setitem(__import__("sys").modules, "phoenix", fake_mod)
        monkeypatch.setitem(__import__("sys").modules, "phoenix.otel", fake_otel)

        # Must not raise
        assert init_tracing(_settings(TRACING_PROVIDER="phoenix")) == "phoenix"
        assert phoenix_module.is_phoenix_initialized() is False

    def test_shutdown_tracing_flushes_provider(self, monkeypatch):
        shut_calls = {"n": 0}

        def fake_register(**_kwargs):
            return SimpleNamespace(shutdown=lambda: shut_calls.__setitem__("n", shut_calls["n"] + 1))

        import types

        fake_mod = types.ModuleType("phoenix")
        fake_otel = types.ModuleType("phoenix.otel")
        fake_otel.register = fake_register
        fake_mod.otel = fake_otel
        monkeypatch.setitem(__import__("sys").modules, "phoenix", fake_mod)
        monkeypatch.setitem(__import__("sys").modules, "phoenix.otel", fake_otel)

        init_tracing(_settings(TRACING_PROVIDER="phoenix"))
        assert phoenix_module.is_phoenix_initialized() is True
        shutdown_tracing()
        assert shut_calls["n"] == 1
        assert phoenix_module.is_phoenix_initialized() is False
