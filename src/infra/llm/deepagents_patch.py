"""Monkey-patch deepagents defaults so summarization degrades gracefully on unknown models."""

from __future__ import annotations

from src.kernel.config import settings


def apply_deepagents_patches() -> None:
    import deepagents.middleware.summarization as _summarization

    current = _summarization.compute_summarization_defaults
    if getattr(current, "_kunxiaozhi_patched", False):
        return

    original = current

    def _patched_compute_summarization_defaults(model):
        defaults = original(model)
        profile = getattr(model, "profile", None)
        has_profile = (
            profile is not None
            and isinstance(profile, dict)
            and "max_input_tokens" in profile
            and isinstance(profile["max_input_tokens"], int)
        )
        if has_profile:
            return defaults

        fallback_max_input_tokens = int(getattr(settings, "DEEPAGENT_DEFAULT_MAX_INPUT_TOKENS", 0))
        if fallback_max_input_tokens <= 0:
            return defaults

        trigger_tokens = max(int(fallback_max_input_tokens * 0.85), 1)
        keep_tokens = max(int(fallback_max_input_tokens * 0.10), 1)
        return {
            "trigger": ("tokens", trigger_tokens),
            "keep": ("tokens", keep_tokens),
            "truncate_args_settings": {
                "trigger": ("tokens", trigger_tokens),
                "keep": ("tokens", keep_tokens),
            },
        }

    _patched_compute_summarization_defaults._kunxiaozhi_patched = True  # type: ignore[attr-defined]
    _summarization.compute_summarization_defaults = _patched_compute_summarization_defaults
