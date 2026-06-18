from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

from arq.connections import RedisSettings


def build_arq_redis_settings(settings: Any) -> RedisSettings:
    """Build arq Redis settings from 昆小智's Redis configuration."""
    parsed = urlparse(settings.REDIS_URL)
    database = 0
    if parsed.path and parsed.path != "/":
        database = int(parsed.path.lstrip("/"))

    password = settings.REDIS_PASSWORD or (unquote(parsed.password) if parsed.password else None)
    username = unquote(parsed.username) if parsed.username else None

    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=database,
        username=username,
        password=password,
        ssl=parsed.scheme == "rediss",
    )
