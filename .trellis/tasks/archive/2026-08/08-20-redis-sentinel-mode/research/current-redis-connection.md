# Current Redis connection contract

Evidence collected 2026-08-20 before Sentinel work.

## Settings

- `REDIS_URL` default `redis://localhost:6379/0` (`src/kernel/config/base.py:167`).
- `REDIS_PASSWORD` optional (`base.py:168`).
- Restart required: `src/kernel/config/constants.py:25-26`.
- Definitions: `src/kernel/config/definitions.py:959-974`. Category REDIS / connection. `is_sensitive=True`. No `frontend_visible` (schema default false).
- i18n `settingDesc.REDIS_URL` / `REDIS_PASSWORD` exist in zh/en/ja/ko/ru.

## Factory

`src/infra/storage/redis.py`:

- `_redis_pool_kwargs()`: encoding utf-8, decode_responses, max_connections 50, timeouts, optional password.
- `get_redis_connection_pool()`: `lru_cache` + `ConnectionPool.from_url(settings.REDIS_URL, **kwargs)`.
- `create_redis_client(isolated_pool=False)`: shared pool, or private `from_url` pool with `auto_close_connection_pool=True`.
- No `redis.sentinel` / `Sentinel` import.

Callers (non-exhaustive): session auth, ARQ payloads, WeCom, pubsub, MCP cache, websocket, event merger, sandbox scheduler, dual-writer streams. All go through this factory except ARQ's own pool via `build_arq_redis_settings`.

## ARQ

`src/infra/task/arq_settings.py` `urlparse`s `REDIS_URL` for host/port/db/user/password/`rediss` ssl. Tests in `tests/infra/task/test_arq_settings.py` cover host/port/db, password preference, `rediss`.

Dependency: `arq>=0.28.0` (`pyproject.toml`). Upstream `RedisSettings` supports `sentinel` + `sentinel_master` + host as `list[tuple[str,int]]`.

## Deploy

- `.env.example:66-67` standalone URL.
- docker-compose `REDIS_URL=redis://redis:6379/0`.
- k8s `redis://REDIS_HOST:6379/3` + `REDIS_PASSWORD` secret.

## Implication

Sentinel must be an additive branch in the factory and ARQ builder. Empty new fields must keep `from_url` and the existing ARQ parse path.
