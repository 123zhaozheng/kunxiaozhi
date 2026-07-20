"""Tracing provider facade: LangSmith vs Phoenix (mutually exclusive)."""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

from src.infra.logging import get_logger

logger = get_logger(__name__)

TracingProvider = Literal["none", "langsmith", "phoenix"]
VALID_PROVIDERS = frozenset({"none", "langsmith", "phoenix"})


def resolve_tracing_provider(settings: Any) -> TracingProvider:
    """Resolve the sole tracing provider setting, defaulting invalid values to none."""
    raw = getattr(settings, "TRACING_PROVIDER", "none")
    value = str(raw or "none").strip().lower()
    if value in VALID_PROVIDERS:
        return value  # type: ignore[return-value]

    logger.warning("Unknown TRACING_PROVIDER=%r; tracing disabled", raw)
    return "none"


def apply_tracing_env(settings: Any) -> TracingProvider:
    """Sync env vars for LangSmith SDK and optional Phoenix env.

    Always writes LANGSMITH_TRACING true/false so stale env cannot leak.
    Forces LANGSMITH_OTEL_ENABLED off to avoid double OTEL with OpenInference.
    """
    provider = resolve_tracing_provider(settings)

    if provider == "langsmith":
        os.environ["LANGSMITH_TRACING"] = "true"
        api_key = getattr(settings, "LANGSMITH_API_KEY", None)
        if api_key:
            os.environ["LANGSMITH_API_KEY"] = str(api_key)
        project = getattr(settings, "LANGSMITH_PROJECT", None)
        if project:
            os.environ["LANGSMITH_PROJECT"] = str(project)
        api_url = getattr(settings, "LANGSMITH_API_URL", None)
        if api_url:
            os.environ["LANGSMITH_API_URL"] = str(api_url)
        sample_rate = getattr(settings, "LANGSMITH_SAMPLE_RATE", None)
        if sample_rate is not None:
            os.environ["LANGSMITH_SAMPLE_RATE"] = str(sample_rate)
        if not api_key:
            logger.warning(
                "TRACING_PROVIDER=langsmith but LANGSMITH_API_KEY is empty"
            )
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    # Avoid LangSmith OTEL hybrid colliding with OpenInference → Phoenix.
    # Always force false so a prior env true cannot double-export under Phoenix.
    if provider != "langsmith":
        os.environ["LANGSMITH_OTEL_ENABLED"] = "false"
    else:
        os.environ.setdefault("LANGSMITH_OTEL_ENABLED", "false")

    if provider == "phoenix":
        endpoint = getattr(settings, "PHOENIX_COLLECTOR_ENDPOINT", None)
        if endpoint:
            os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = str(endpoint)
        project = getattr(settings, "PHOENIX_PROJECT_NAME", None)
        if project:
            os.environ["PHOENIX_PROJECT_NAME"] = str(project)
        api_key = getattr(settings, "PHOENIX_API_KEY", None)
        if api_key:
            os.environ["PHOENIX_API_KEY"] = str(api_key)

    return provider


def init_tracing(settings: Any) -> TracingProvider:
    """Apply env and optionally register Phoenix. Safe to call once per process."""
    provider = apply_tracing_env(settings)
    logger.info("Tracing provider resolved: %s", provider)

    if provider == "phoenix":
        from src.infra.tracing.phoenix import init_phoenix

        endpoint = (
            getattr(settings, "PHOENIX_COLLECTOR_ENDPOINT", None)
            or "http://localhost:6006/v1/traces"
        )
        project = getattr(settings, "PHOENIX_PROJECT_NAME", None) or "lamb-agent"
        api_key: Optional[str] = getattr(settings, "PHOENIX_API_KEY", None) or None
        if api_key == "":
            api_key = None
        init_phoenix(endpoint=str(endpoint), project_name=str(project), api_key=api_key)

    return provider


def shutdown_tracing() -> None:
    """Shut down Phoenix exporter if active."""
    from src.infra.tracing.phoenix import shutdown_phoenix

    shutdown_phoenix()
