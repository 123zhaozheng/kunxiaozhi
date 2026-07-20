"""Tracing module initialization.

Keep package imports free of src.kernel.config so Settings.__init__ can call
apply_tracing_env without circular import (package __init__ runs before submodules).
"""

from src.infra.tracing.decorators import traced
from src.infra.tracing.provider import (
    apply_tracing_env,
    init_tracing,
    resolve_tracing_provider,
    shutdown_tracing,
)

__all__ = [
    "LangSmithTracer",
    "tracer",
    "traced",
    "resolve_tracing_provider",
    "apply_tracing_env",
    "init_tracing",
    "shutdown_tracing",
]


def __getattr__(name: str):
    """Lazy export LangSmith symbols that transitively need settings."""
    if name in {"LangSmithTracer", "tracer"}:
        from src.infra.tracing.langsmith_client import LangSmithTracer, tracer

        if name == "LangSmithTracer":
            return LangSmithTracer
        return tracer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
