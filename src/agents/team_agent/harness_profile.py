"""Team HarnessProfile: omit native Todo during DeepAgents assembly.

The default harness keeps one native `TodoListMiddleware` for Fast and Search.
Team uses `update_sop` instead, so its model-specific profile excludes that
single exact middleware class while the request-layer filter remains a fallback.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.agents.core.harness_prompt_overrides import build_default_harness_profile
from src.infra.logging import get_logger

logger = get_logger(__name__)

@contextmanager
def team_harness_profile(model: Any) -> Iterator[None]:
    """Install a model-specific Team overlay only while the graph is assembled."""
    from deepagents._models import get_model_identifier, get_model_provider
    from deepagents.profiles.harness import harness_profiles as hp

    identifier = get_model_identifier(model)
    provider = get_model_provider(model)
    key = f"{provider}:{identifier}" if provider and identifier and ":" not in identifier else None
    if key is None:
        logger.debug("[team_harness_profile] Cannot resolve profile key for %r; skipping", model)
        yield
        return

    hp._ensure_harness_profiles_loaded()
    original = hp._HARNESS_PROFILES.get(key)
    try:
        hp.register_harness_profile(key, build_default_harness_profile(todo_enabled=False))
        logger.debug("[team_harness_profile] Installed Team profile under %r", key)
        yield
    finally:
        if original is None:
            hp._HARNESS_PROFILES.pop(key, None)
        else:
            hp._HARNESS_PROFILES[key] = original
        logger.debug("[team_harness_profile] Restored profile for %r", key)
