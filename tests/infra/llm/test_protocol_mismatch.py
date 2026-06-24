from __future__ import annotations

import pytest

from src.infra.llm.client import _assert_protocol_api_base_consistent


@pytest.mark.parametrize(
    "api_base",
    [
        "https://proxy.example.com/v1/chat/completions",
        "https://proxy.example.com/openai/v1",
        "https://proxy.example.com/v1/chat/completions/",
    ],
)
def test_fast_fail_anthropic_protocol_with_openai_api_base(api_base: str) -> None:
    with pytest.raises(ValueError, match="Protocol/api_base mismatch"):
        _assert_protocol_api_base_consistent("anthropic", api_base)


@pytest.mark.parametrize(
    "api_base",
    [
        "https://proxy.example.com/v1/messages",
        "https://proxy.example.com/v1/messages/",
    ],
)
def test_fast_fail_openai_protocol_with_anthropic_api_base(api_base: str) -> None:
    with pytest.raises(ValueError, match="Protocol/api_base mismatch"):
        _assert_protocol_api_base_consistent("openai", api_base)


@pytest.mark.parametrize(
    "protocol, api_base",
    [
        # Bare hostnames without path markers — dual-route proxies must not fire.
        ("anthropic", "https://api.anthropic.com"),
        ("openai", "https://api.openai.com"),
        ("anthropic", "https://proxy.example.com"),
        ("openai", "https://proxy.example.com"),
        # OpenAI protocol with OpenAI-compatible base — normal case.
        ("openai", "https://api.openai.com/v1/chat/completions"),
        ("openai", "https://proxy.example.com/openai/v1"),
        # Anthropic protocol with Anthropic base — normal case.
        ("anthropic", "https://api.anthropic.com/v1/messages"),
        # Empty/None api_base — never fires.
        ("anthropic", ""),
        ("openai", None),
        # Generic /v1 path without chat/messages marker — not a strong signal.
        ("anthropic", "https://proxy.example.com/v1"),
        ("openai", "https://proxy.example.com/v1"),
    ],
)
def test_fast_fail_does_not_fire_on_compatible_or_ambiguous_bases(
    protocol: str,
    api_base: str,
) -> None:
    # Should not raise.
    _assert_protocol_api_base_consistent(protocol, api_base)
