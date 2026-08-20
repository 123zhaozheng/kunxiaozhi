# Support Redis Sentinel mode

## Goal

Production can use Redis Sentinel to discover the current master. Environments that keep today's single-node `REDIS_URL` continue to work unchanged.

## Background

- Current Redis contract is `REDIS_URL` + `REDIS_PASSWORD` only (`src/kernel/config/base.py:167-168`). Both require restart (`src/kernel/config/constants.py:25-26`). `REDIS_URL` is not frontend-visible (schema default `frontend_visible=False`).
- Every application Redis client goes through `create_redis_client` / `get_redis_client` / `get_redis_connection_pool` (`src/infra/storage/redis.py:58-79`). The factory uses `ConnectionPool.from_url(settings.REDIS_URL)` (standalone / `rediss` only). Isolated pools (WebSocket, event merger, WeCom lease, sandbox scheduler) use the same factory.
- ARQ parses one `host:port` from `REDIS_URL` (`src/infra/task/arq_settings.py:9-26`). Upstream ARQ already supports `sentinel=True` + host list + `sentinel_master`; this project does not enable it.
- Deploy examples use `redis://host:6379/...`. There is no Sentinel client today.

## Requirements

- R1. Business call sites keep using `get_redis_client` / `create_redis_client`. No per-feature Redis constructors.
- R2. Sentinel is opt-in. Default remains standalone `REDIS_URL` (+ optional `REDIS_PASSWORD`), including `rediss://`.
- R3. Enable Sentinel only when both `REDIS_SENTINEL_HOSTS` and `REDIS_SENTINEL_MASTER` are non-empty. Hosts format: comma-separated `host:port` (example `s1:26379,s2:26379`). If exactly one of the two is set, fail closed at client/settings build (do not silently fall back).
- R4. When Sentinel is on, both the storage factory and ARQ resolve the current master via Sentinel. Database index still comes from `REDIS_URL` path (`/0` local default, `/3` in k8s examples).
- R5. `REDIS_PASSWORD` remains the Redis data-node password (ARQ keeps URL-embedded password fallback). Optional `REDIS_SENTINEL_PASSWORD` is only for Sentinel process auth; empty means do not send a separate sentinel password.
- R6. New settings follow the existing Redis pattern: `Settings` + `SETTING_DEFINITIONS` + restart-required + `is_sensitive` for secrets + i18n `settingDesc`. Keep `frontend_visible` unset/false like `REDIS_URL`.
- R7. Tests: sentinel-off keeps `from_url`; sentinel-on builds Sentinel / ARQ sentinel settings; db index and password precedence match today's ARQ tests.

## Acceptance Criteria

- [ ] AC1. With Sentinel unset/empty, existing `REDIS_URL` / `REDIS_PASSWORD` behavior and tests stay unchanged. (R2, R7)
- [ ] AC2. With both Sentinel fields set, `create_redis_client` / shared pool use Sentinel `master_for`, not a sentinel port as a data node. Isolated pools use the same Sentinel path. (R1, R3, R4)
- [ ] AC3. With Sentinel on, `build_arq_redis_settings` sets `sentinel=True`, the parsed host list, and `sentinel_master`. (R4)
- [ ] AC4. Sentinel mode database index matches `REDIS_URL` path. (R4)
- [ ] AC5. New settings exist in definitions/constants/i18n; changing them requires restart. (R6)
- [ ] AC6. `.env.example` documents the new fields. k8s/docker Redis env stays valid without Sentinel vars. (R2)
- [ ] AC7. Partial Sentinel config (only hosts or only master) fails closed rather than connecting standalone by accident. (R3)

## Out Of Scope

- Redis Cluster.
- A new reconnect framework for Pub/Sub, blocking `xread`, or other long-lived connections during failover. Document the operational caveat only.
- Forcing existing environments onto Sentinel.
- Changing MongoDB or other backends.

## Key Decisions

- Dual-mode, confirmed: standalone keeps working; Sentinel is additive opt-in.
- Config surface: `REDIS_SENTINEL_HOSTS`, `REDIS_SENTINEL_MASTER`, optional `REDIS_SENTINEL_PASSWORD`.
- Do not encode Sentinel into a custom `REDIS_URL` scheme.
