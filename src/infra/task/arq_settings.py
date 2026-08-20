from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

from arq.connections import RedisSettings

from src.infra.storage.redis import (
    parse_sentinel_hosts,
    redis_db_index,
    redis_node_password,
    sentinel_enabled,
)

_ARQ_SENTINEL_PASSWORD_UNSUPPORTED = (
    "REDIS_SENTINEL_PASSWORD is set, but the installed arq RedisSettings "
    "does not support sentinel_kwargs; cannot send a distinct Sentinel password."
)


def _arq_supports_sentinel_kwargs() -> bool:
    return "sentinel_kwargs" in getattr(RedisSettings, "__dataclass_fields__", {})


def _sentinel_password(settings: Any) -> str | None:
    password = str(getattr(settings, "REDIS_SENTINEL_PASSWORD", None) or "").strip()
    return password or None


def build_arq_redis_settings(settings: Any) -> RedisSettings:
    """Build arq Redis settings from 昆小智's Redis configuration."""
    parsed = urlparse(settings.REDIS_URL)
    username = unquote(parsed.username) if parsed.username else None
    password = redis_node_password(settings)
    database = redis_db_index(settings)

    if sentinel_enabled(settings):
        redis_kwargs: dict[str, Any] = {
            "host": parse_sentinel_hosts(settings),
            "sentinel": True,
            "sentinel_master": str(settings.REDIS_SENTINEL_MASTER).strip(),
            "database": database,
            "username": username,
            "password": password,
            "ssl": parsed.scheme == "rediss",
        }
        sentinel_password = _sentinel_password(settings)
        if sentinel_password:
            if not _arq_supports_sentinel_kwargs():
                raise ValueError(_ARQ_SENTINEL_PASSWORD_UNSUPPORTED)
            redis_kwargs["sentinel_kwargs"] = {"password": sentinel_password}
        return RedisSettings(**redis_kwargs)

    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=database,
        username=username,
        password=password,
        ssl=parsed.scheme == "rediss",
    )
