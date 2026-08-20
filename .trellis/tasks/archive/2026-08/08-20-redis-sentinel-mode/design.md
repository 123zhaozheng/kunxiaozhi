# Redis Sentinel — Technical Design

## Boundaries

- Change Redis **connection construction only**: `src/infra/storage/redis.py`, `src/infra/task/arq_settings.py`, and the settings/i18n/env surfaces that declare the new fields.
- Do not change Storage APIs, ARQ job payloads, or any `get_redis_client()` call site.
- `close_redis_client()` must still close the shared pool (standalone `ConnectionPool` or Sentinel-backed pool).

## Mode selection

```text
hosts = strip(REDIS_SENTINEL_HOSTS)
master = strip(REDIS_SENTINEL_MASTER)

if hosts and master:
    SENTINEL MODE
elif hosts or master:
    FAIL CLOSED  # misconfig, do not silently use REDIS_URL
else:
    STANDALONE   # today's from_url(REDIS_URL)
```

Parse hosts as comma-separated `host:port`. Split each token on the last `:`. Reject empty tokens and missing ports.

## Data flow

### Standalone (default)

Unchanged:

```text
REDIS_URL + REDIS_PASSWORD
  -> ConnectionPool.from_url(...)
  -> Redis(connection_pool=...)
```

Isolated pools still create a private `from_url` pool with `auto_close_connection_pool=True`.

### Sentinel

```text
REDIS_SENTINEL_HOSTS + REDIS_SENTINEL_MASTER
REDIS_PASSWORD            -> Redis data-node auth
REDIS_SENTINEL_PASSWORD   -> Sentinel process auth (optional)
REDIS_URL path            -> db index
REDIS_URL scheme          -> ssl if rediss://
REDIS_URL username        -> data-node username if present

  storage factory:
    Sentinel(sentinels, sentinel_kwargs={password?} , password=node_password, ...)
    -> master_for(master_name, db=..., decode_responses=True, ...)

  ARQ:
    RedisSettings(host=[(h,p),...], sentinel=True, sentinel_master=...,
                  database=db, password=node_password, ssl=rediss)
```

Shared client: cache the Sentinel-backed **connection pool** (same `get_redis_connection_pool` name) and wrap with `Redis(connection_pool=...)`. Isolated clients: `master_for` / private Sentinel pool with `auto_close_connection_pool=True`.

Do not point `REDIS_URL` at port 26379. Sentinel addresses live only in `REDIS_SENTINEL_HOSTS`.

## Contracts

| Setting | Role |
|---|---|
| `REDIS_SENTINEL_HOSTS` | `host:port,host:port` |
| `REDIS_SENTINEL_MASTER` | Sentinel monitor name (`mymaster`, etc.) |
| `REDIS_SENTINEL_PASSWORD` | Optional Sentinel auth |
| `REDIS_PASSWORD` | Data-node password (existing) |
| `REDIS_URL` | Standalone URL; in Sentinel mode only path/scheme/user/password-fallback |

All three new fields + existing Redis pair are restart-required. Secrets: `REDIS_SENTINEL_PASSWORD` `is_sensitive=True`. Match `REDIS_URL`: omit `frontend_visible` (defaults false) but add `settingDesc` i18n keys like today's Redis fields.

Helper functions stay in `src/infra/storage/redis.py` (parse hosts, enabled?, db from URL) and are reused by `build_arq_redis_settings` so both paths cannot drift. Prefer a small shared helper module only if importing storage from arq_settings creates a cycle; otherwise keep parse helpers in `arq_settings.py`'s sibling or `src/infra/storage/redis.py` and import from arq_settings (arq_settings already imports nothing from redis.py — a one-way import is fine). Better: put parse/enable/db helpers in `src/infra/storage/redis.py` or a tiny `src/infra/storage/redis_settings.py` if needed to avoid pulling asyncio redis into arq_settings. **Recommended:** `src/infra/task/arq_settings.py` stays dumb; add `parse_redis_endpoint(settings)` in `src/infra/storage/redis.py` **or** a new `src/kernel/config` helper. Simplest surgical option: helpers in `src/infra/storage/redis.py` plus a thin `build_sentinel_params(settings)` used by both files. If arq_settings importing redis.py is heavy, put helpers in `src/infra/task/arq_settings.py` and import them from redis.py — redis.py already is the factory. **Pick:** helpers live next to the factory in `redis.py` (`_sentinel_hosts`, `_sentinel_enabled`, `_redis_db_index`, `_node_password`) and `arq_settings.py` imports those (non-private names: `sentinel_enabled`, `parse_sentinel_hosts`, `redis_db_index`, `redis_node_password`).

## ARQ sentinel password

`arq>=0.28.0`. Implementer must inspect installed `RedisSettings` fields. If `sentinel_kwargs` exists, pass `{"password": REDIS_SENTINEL_PASSWORD}` when set. If it does not exist, ARQ cannot send a distinct sentinel password; then document that Sentinel process auth for ARQ is unsupported unless it matches node credentials, and fail closed when `REDIS_SENTINEL_PASSWORD` is set and ARQ cannot consume it.

## Compatibility / rollout

- Empty sentinel fields: bit-identical standalone path.
- k8s/docker: no required new env. Optional comment in `.env.example` only; do not break `REDIS_URL` examples.
- Rollback: unset the two sentinel fields and restart → previous standalone behavior.

## Trade-offs

- Additive env vars vs custom URL scheme: env vars keep `from_url` working and split sentinel vs node passwords.
- Fail-closed partial config vs silent fallback: silent fallback would hide a bad production deploy.
- No extra reconnect layer: Sentinel rediscovers master for **new** connections; existing Pub/Sub / blocking `xread` sockets can still die across failover until the caller retries. That is accepted for this task.

## Ops / rollback

1. Keep `REDIS_URL` pointed at a dummy or last-known URL whose **path** is the intended db (`redis://unused:6379/3` is enough in sentinel mode).
2. Set hosts + master (+ passwords).
3. Restart API/worker.
4. If connect fails: clear sentinel fields, restart, back to standalone `REDIS_URL` (current master IP if needed).
