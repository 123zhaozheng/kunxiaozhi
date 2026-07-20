"""Phoenix OpenInference / OTEL registration helpers."""

from __future__ import annotations

from typing import Any, Optional

from src.infra.logging import get_logger

logger = get_logger(__name__)

_tracer_provider: Any = None
_initialized: bool = False


def is_phoenix_initialized() -> bool:
    return _initialized


def init_phoenix(
    *,
    endpoint: str,
    project_name: str,
    api_key: Optional[str] = None,
) -> bool:
    """Register Phoenix OTEL exporter + OpenInference LangChain instrumentor.

    Returns True on success, False on skip/failure. Never raises to the caller.
    Process-level once-guard: subsequent calls are no-ops.
    """
    global _tracer_provider, _initialized

    if _initialized:
        logger.debug("Phoenix tracing already initialized; skipping re-register")
        return True

    try:
        from phoenix.otel import register
    except ImportError:
        logger.error(
            "Phoenix tracing requested but arize-phoenix-otel is not installed"
        )
        return False

    try:
        kwargs: dict[str, Any] = {
            "project_name": project_name or "lamb-agent",
            "endpoint": endpoint,
            "auto_instrument": True,
            "batch": True,
            "protocol": "http/protobuf",
            "verbose": False,
        }
        if api_key:
            kwargs["api_key"] = api_key

        _tracer_provider = register(**kwargs)
        _initialized = True
        logger.info(
            "Phoenix tracing registered: project=%s endpoint=%s",
            project_name,
            endpoint,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to register Phoenix tracing (endpoint=%s); continuing without Phoenix",
            endpoint,
        )
        _tracer_provider = None
        _initialized = False
        return False


def shutdown_phoenix() -> None:
    """Flush and shut down the Phoenix tracer provider if initialized."""
    global _tracer_provider, _initialized

    if not _initialized or _tracer_provider is None:
        return

    try:
        shutdown = getattr(_tracer_provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
        logger.info("Phoenix tracing shut down")
    except Exception:
        logger.exception("Error shutting down Phoenix tracing")
    finally:
        _tracer_provider = None
        _initialized = False
