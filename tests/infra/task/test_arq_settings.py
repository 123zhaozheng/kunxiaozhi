from __future__ import annotations

from types import SimpleNamespace

import pytest
from arq.connections import RedisSettings

from src.infra.task.arq_settings import build_arq_redis_settings


def test_build_arq_redis_settings_uses_project_redis_url() -> None:
    settings = SimpleNamespace(
        REDIS_URL="redis://redis.example:6380/2",
        REDIS_PASSWORD=None,
        REDIS_SENTINEL_HOSTS="",
        REDIS_SENTINEL_MASTER="",
        REDIS_SENTINEL_PASSWORD=None,
    )

    redis_settings = build_arq_redis_settings(settings)

    assert redis_settings.host == "redis.example"
    assert redis_settings.port == 6380
    assert redis_settings.database == 2
    assert redis_settings.sentinel is False


def test_build_arq_redis_settings_prefers_explicit_project_password() -> None:
    settings = SimpleNamespace(
        REDIS_URL="redis://:url-password@redis.example:6379/0",
        REDIS_PASSWORD="settings-password",
    )

    redis_settings = build_arq_redis_settings(settings)

    assert redis_settings.password == "settings-password"


def test_build_arq_redis_settings_supports_rediss_scheme() -> None:
    settings = SimpleNamespace(
        REDIS_URL="rediss://redis.example:6379/0",
        REDIS_PASSWORD=None,
    )

    redis_settings = build_arq_redis_settings(settings)

    assert redis_settings.ssl is True


def _sentinel_settings(**overrides) -> SimpleNamespace:
    values = {
        "REDIS_URL": "redis://unused:6379/3",
        "REDIS_PASSWORD": None,
        "REDIS_SENTINEL_HOSTS": "s1:26379,s2:26379",
        "REDIS_SENTINEL_MASTER": "mymaster",
        "REDIS_SENTINEL_PASSWORD": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_arq_redis_settings_uses_sentinel_when_configured() -> None:
    redis_settings = build_arq_redis_settings(_sentinel_settings())

    assert redis_settings.sentinel is True
    assert redis_settings.host == [("s1", 26379), ("s2", 26379)]
    assert redis_settings.sentinel_master == "mymaster"
    assert redis_settings.database == 3
    assert redis_settings.ssl is False


def test_build_arq_redis_settings_sentinel_prefers_explicit_node_password() -> None:
    redis_settings = build_arq_redis_settings(
        _sentinel_settings(
            REDIS_URL="redis://:url-password@unused:6379/3",
            REDIS_PASSWORD="settings-password",
        )
    )

    assert redis_settings.password == "settings-password"


def test_build_arq_redis_settings_partial_sentinel_fails_closed() -> None:
    with pytest.raises(ValueError, match="partially configured"):
        build_arq_redis_settings(
            SimpleNamespace(
                REDIS_URL="redis://localhost:6379/0",
                REDIS_PASSWORD=None,
                REDIS_SENTINEL_HOSTS="s1:26379",
                REDIS_SENTINEL_MASTER="",
            )
        )

    with pytest.raises(ValueError, match="partially configured"):
        build_arq_redis_settings(
            SimpleNamespace(
                REDIS_URL="redis://localhost:6379/0",
                REDIS_PASSWORD=None,
                REDIS_SENTINEL_HOSTS="",
                REDIS_SENTINEL_MASTER="mymaster",
            )
        )


def test_build_arq_redis_settings_sentinel_password_matches_arq_support() -> None:
    settings = _sentinel_settings(REDIS_SENTINEL_PASSWORD="sentinel-secret")
    if "sentinel_kwargs" in RedisSettings.__dataclass_fields__:
        redis_settings = build_arq_redis_settings(settings)
        assert redis_settings.sentinel_kwargs == {"password": "sentinel-secret"}
    else:
        with pytest.raises(ValueError, match="sentinel_kwargs"):
            build_arq_redis_settings(settings)
