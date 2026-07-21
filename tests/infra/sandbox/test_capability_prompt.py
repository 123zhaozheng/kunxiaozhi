"""Unit tests for sandbox capability prompt section builder."""

from __future__ import annotations

from src.infra.sandbox.capability_prompt import build_sandbox_capability_section


def test_empty_description_returns_empty() -> None:
    assert build_sandbox_capability_section(None) == ""
    assert build_sandbox_capability_section("") == ""


def test_whitespace_only_returns_empty() -> None:
    assert build_sandbox_capability_section("   \n\t  ") == ""


def test_non_empty_returns_admin_text_only() -> None:
    body = "## 沙箱环境能力边界\n\n预装: python3, curl\n网络: 出站仅 https"
    section = build_sandbox_capability_section(body)

    assert section == body
    # No built-in framing injected by the builder.
    assert "以下描述当前沙箱镜像/模板的能力边界" not in section
    assert "勿臆测未写明的工具或外网访问" not in section


def test_strips_surrounding_whitespace_but_keeps_inner() -> None:
    section = build_sandbox_capability_section("  line1\n  line2  \n")
    assert section == "line1\n  line2"
