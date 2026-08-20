# Redis Sentinel Dual Mode

## Scenario: Optional Sentinel with unchanged standalone `REDIS_URL`

### 1. Scope / Trigger

Use this contract when changing Redis connection construction. All application clients must keep going through `create_redis_client` / `get_redis_client`. Do not add per-feature Redis constructors or encode Sentinel into a custom `REDIS_URL` scheme.

### 2. Signatures

```python
sentinel_enabled(config: Any | None = None) -> bool
parse_sentinel_hosts(config: Any | None = None) -> list[tuple[str, int]]
redis_db_index(config: Any | None = None) -> int
redis_node_password(config: Any | None = None) -> str | None
create_redis_client(*, isolated_pool: bool = False, socket_timeout: Any = ...) -> Redis
build_arq_redis_settings(settings: Any) -> RedisSettings
```

### 3. Contracts

| Key | Role | Restart |
|---|---|---|
| `REDIS_URL` | Standalone URL. In Sentinel mode: db path, `rediss` ssl, username, password fallback | yes |
| `REDIS_PASSWORD` | Redis **data-node** password | yes |
| `REDIS_SENTINEL_HOSTS` | Comma-separated `host:port` (example `s1:26379,s2:26379`) | yes |
| `REDIS_SENTINEL_MASTER` | Sentinel monitor name (example `mymaster`) | yes |
| `REDIS_SENTINEL_PASSWORD` | Optional **Sentinel process** password. Empty = no distinct sentinel auth | yes |

- Enable Sentinel only when **both** `REDIS_SENTINEL_HOSTS` and `REDIS_SENTINEL_MASTER` are non-empty.
- Empty both fields: `ConnectionPool.from_url(REDIS_URL)` and ARQ single-host parse. Local/k8s without new env vars stay valid.
- Do not point `REDIS_URL` at port 26379. Sentinel addresses live only in `REDIS_SENTINEL_HOSTS`.
- `frontend_visible` defaults false (same as `REDIS_URL`). Admins with `settings:manage` still see Redis fields in Settings → Redis. Saving writes MongoDB (`database > env`) but the process connection pool does not rebuild until restart.
- Shared and isolated pools use the same mode. Isolated Sentinel clients set `auto_close_connection_pool=True`; the shared pool does not.
- If `REDIS_SENTINEL_PASSWORD` is set, merge node `socket_*` timeouts into `sentinel_kwargs` so redis-py defaults are not dropped.
- ARQ: `sentinel=True`, `host=list[tuple[str,int]]`, `sentinel_master`. If `REDIS_SENTINEL_PASSWORD` is set and installed `RedisSettings` has no `sentinel_kwargs` (arq 0.28), fail closed.
- Failover: new connections rediscover the master. Existing Pub/Sub / blocking `xread` sockets are not auto-reconnected.

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Hosts and master both empty | Standalone `from_url` / ARQ host:port |
| Hosts and master both set | `Sentinel.master_for` + ARQ sentinel settings |
| Only hosts or only master set | `ValueError` — do not silently fall back to `REDIS_URL` |
| Host token missing `host:port` | `ValueError` on parse |
| `REDIS_SENTINEL_PASSWORD` set, ARQ lacks `sentinel_kwargs` | `ValueError` |
| `REDIS_PASSWORD` set | Data-node password; wins over URL-embedded password |

### 5. Good/Base/Bad Cases

- Good: `REDIS_SENTINEL_HOSTS=s1:26379,s2:26379`, `REDIS_SENTINEL_MASTER=mymaster`, `REDIS_URL=redis://unused:6379/3`, `REDIS_PASSWORD` = node password, `REDIS_SENTINEL_PASSWORD` empty (or same as node, left unset).
- Base: only `REDIS_URL=redis://localhost:6379/0`; Sentinel fields empty.
- Bad: `REDIS_URL=redis://sentinel:26379/0` with Sentinel fields empty (talks to Sentinel as a data node). Bad: setting `REDIS_SENTINEL_PASSWORD` on arq 0.28 while the worker must start.

### 6. Tests Required

- Empty Sentinel fields still call `ConnectionPool.from_url` and ARQ single-host parse (`tests/infra/test_redis_storage.py`, `tests/infra/task/test_arq_settings.py`).
- Both fields set: factory and isolated pool use `Sentinel.master_for`; ARQ `sentinel is True` and host is a list.
- Partial config (hosts-only and master-only) raises `ValueError`.
- Db index matches `REDIS_URL` path; `REDIS_PASSWORD` wins over URL password.
- `REDIS_SENTINEL_PASSWORD` + ARQ without `sentinel_kwargs` raises.

### 7. Wrong vs Correct

#### Wrong

```python
pool = redis.ConnectionPool.from_url(settings.REDIS_URL)  # also used when Sentinel is configured
# or custom redis+sentinel:// stuffed into REDIS_URL
```

#### Correct

```python
if sentinel_enabled():
    client = Sentinel(parse_sentinel_hosts(), ...).master_for(master_name)
else:
    pool = redis.ConnectionPool.from_url(settings.REDIS_URL, ...)
```
