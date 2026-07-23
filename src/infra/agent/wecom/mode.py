from __future__ import annotations

from typing import Literal, cast

from src.infra.logging import get_logger
from src.kernel.config import settings

logger = get_logger(__name__)

WeComRuntimeMode = Literal["embedded", "external", "disabled"]
_VALID_RUNTIME_MODES = {"embedded", "external", "disabled"}


def get_wecom_runtime_mode(value: object | None = None) -> WeComRuntimeMode:
    raw = settings.WECOM_RUNTIME_MODE if value is None else value
    normalized = str(raw or "").strip().lower()
    if normalized not in _VALID_RUNTIME_MODES:
        logger.warning(
            "Invalid WECOM_RUNTIME_MODE=%r; falling back to embedded",
            raw,
        )
        return "embedded"
    return cast(WeComRuntimeMode, normalized)
