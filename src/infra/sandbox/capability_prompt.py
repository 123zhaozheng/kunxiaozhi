"""Sandbox image/template capability description prompt section builder."""

from __future__ import annotations


def build_sandbox_capability_section(description: str | None) -> str:
    """Build a system-prompt section from admin-authored sandbox capability text.

    Returns the description as-is after strip. Empty or whitespace-only input
    returns ``""`` so callers skip injection. No built-in framing is added —
    admins own the full wording (heading, capability bounds, limits, etc.).
    """
    return (description or "").strip()
