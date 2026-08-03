# Research: Backend idle-login session expiration

- Query: Find the backend contracts and implementation points for an administrator-configurable idle login timeout (for example, log a user out after three hours without activity), covering password/OAuth/OA SSO issuance, refresh, HTTP dependencies, WebSocket authentication, Redis state, hot reload, and numeric validation.
- Scope: internal
- Date: 2026-08-03

## Findings

### Existing token and login flows

- JWT creation is centralized in `src/infra/auth/jwt.py:18-48` (`create_access_token`) and `:51-85` (`create_refresh_token`). Tokens currently contain `sub`, `iat`, `exp`; only refresh tokens contain `type="refresh"` and neither token has a per-login identifier (`jti`/`sid`). Access lifetime is read from `settings.ACCESS_TOKEN_EXPIRE_HOURS` and refresh lifetime from `settings.REFRESH_TOKEN_EXPIRE_DAYS` on every issuance.
- Password login issues both tokens in `src/infra/user/manager.py:61-123` (access at `:112`, refresh at `:114-117`). This is called by `src/api/routes/auth/core.py:106-133`.
- OAuth callback issues both tokens in `src/infra/auth/oauth.py:246-276` (access `:269`, refresh `:270`), after user lookup/provision and `touch_updated_at`.
- OA SSO/workcode login issues both tokens in `src/infra/auth/oa_login.py:65-77` (access `:70`, refresh `:71`) after optional provisioning and account-active checks.
- Refresh endpoint is `src/api/routes/auth/core.py:145-196`: it decodes the submitted refresh JWT (`:158`), validates `type == "refresh"` (`:161-166`), checks that the user still exists (`:174-181`), then rotates access and refresh tokens (`:184-187`). It is deliberately public in `AuthMiddleware.PUBLIC_PREFIXES` (`src/api/middleware/auth.py:42-44`), so an idle-session check must be explicitly added to this route/service rather than relying on route auth dependencies.
- `TokenPayload` only models `sub`, `username`, roles/permissions, `exp`, and `iat` (`src/kernel/schemas/user.py:95-110`); adding a session identifier to the JWT does not require exposing it in API responses, but the verifier/session guard needs to read it from raw claims.

### HTTP authentication/dependency path

- `get_current_user_required` in `src/api/deps.py:120-189` accepts the middleware-parsed payload (`request.state.auth_payload`) or verifies the bearer token, then loads the user and dynamic roles. It has a per-process token cache (`_auth_cache`, TTL 45s, `:20-57`) and request-state short-circuits (`:138-151`), so an idle guard placed after these branches would be bypassed for cached requests. The guard must run before either cache return, or cache entries must carry a session-check timestamp while Redis activity is checked on every request.
- Optional auth (`get_current_user`, `src/api/deps.py:87-117`) returns a parsed JWT without a user lookup. If “any authenticated activity” is intended, it also needs to touch/check the session whenever credentials are supplied; otherwise optional routes can remain idle-only and will not keep a session alive.
- `UserContextMiddleware` verifies the token and stores `request.state.auth_payload` before routes (`src/api/middleware/user_context.py:17-40`), but does not check user existence, roles, Redis state, or idle timeout. `AuthMiddleware` only checks presence of `Authorization` for non-public HTTP paths (`src/api/middleware/auth.py:66-91`). Thus neither middleware alone enforces expiration today.
- Most protected routes use `Depends(get_current_user_required)` (backend quality spec requires this), so dependency-level enforcement gives a narrow change surface. A middleware-level check would cover unguarded routes but must skip/coordinate `/api/auth/refresh` and preserve public browser/API behavior.

### WebSocket path

- `/ws` authenticates via query token or first `{"type":"auth","token":...}` message in `src/api/routes/websocket.py:65-116`; it calls `get_current_user_from_websocket` at `:92`, then registers the connection at `:118-122`.
- `get_current_user_from_websocket` (`src/api/deps.py:192-253`) verifies JWT, loads the user and roles, but has no Redis/session activity check. The post-auth receive loop (`src/api/routes/websocket.py:124-138`) blocks on `receive_text`; any client message is currently just logged (`:128-130`). A socket can therefore remain connected forever unless the route adds idle-aware timeout/ping handling.
- Existing WebSocket tests are in `tests/api/test_deps_auth_cache.py` (offloaded verification) and `tests/api/routes/test_websocket_route.py` (auth-message flow). They monkeypatch `get_current_user_from_websocket`, `run_blocking_io`, rate limiter, and connection manager; idle tests should preserve those seams.

### Settings and hot reload

- JWT settings are defined in `src/kernel/config/base.py:243-247` and exposed to admin settings via `src/kernel/config/_definitions_extra.py:9-31` (`ACCESS_TOKEN_EXPIRE_HOURS`, `REFRESH_TOKEN_EXPIRE_DAYS`, `SettingCategory.SECURITY`, `SettingType.NUMBER`). A new idle setting should be added in both places so env/default loading and the admin UI/API share one source of truth.
- Admin updates go through `PUT /api/settings/{key}` (`src/api/routes/settings.py:181-205`) guarded by `require_permissions("settings:manage")` (`:185`). `SettingsService.set` persists, calls `refresh_settings(key)`, then publishes a Redis change (`src/infra/settings/service.py:118-138`).
- `refresh_settings` updates the process-global `settings` object immediately (`src/kernel/config/service.py:304-...`), and `SettingsPubSub` refreshes other instances on `settings:changed` (`src/infra/settings/pubsub.py:44-80`; channel constant `src/infra/task/constants.py:14`). The idle guard should read the live `settings` value per request; no process restart is needed unless the setting is incorrectly classified as restart-required.
- `SettingsStorage.set` only checks generic NUMBER/BOOLEAN/STRING types (`src/infra/settings/storage.py:149-179`); it does not enforce range, integer-ness, or finite values. A timeout definition needs explicit validation (for example, integer minutes, `0` = disabled, positive bounded maximum) either in storage’s key-specific validation or a reusable definition-level min/max contract. Reject NaN/Infinity and booleans (Python `bool` is an `int` subclass) explicitly.

### Redis primitives available

- Shared async Redis clients are provided by `src/infra/storage/redis.py:37-84` (`get_redis_client`, `create_redis_client`) with `decode_responses=True`; `RedisStorage` has `set(..., ttl=...)`, `get`, `exists`, `expire`, and `ttl` helpers (`:95-151`). Existing code also uses direct Redis `eval` scripts for atomic lease/TTL operations. A per-login store can use a dedicated key namespace such as `auth:session:{sid}` and an atomic Lua touch/check script.

## Recommended contract

1. **Setting:** add `AUTH_IDLE_TIMEOUT_MINUTES` (or equivalent explicitly auth-scoped name) as a SECURITY/number setting. Recommend default `0` (disabled, preserving current behavior), integer values `0..43200` (0..30 days), with a product minimum such as 5 minutes for non-zero values. Document that this is inactivity, not absolute token lifetime. Return the setting through existing `/api/settings` and apply updates immediately through `refresh_settings`/pubsub.
2. **Session identifier:** generate a cryptographically random `sid`/`jti` once per login and include it in both access and refresh JWTs. Refresh rotation must preserve the same `sid`; a brand-new password/OAuth/OA login creates a new one. Centralize issuance (for example, a `create_login_tokens` helper) so the four call sites above cannot diverge.
3. **Redis record:** on login, write `auth:session:{sid}` with at least `user_id`, `issued_at`, and `last_activity` (Unix seconds), TTL equal to the current idle timeout (or a bounded cleanup TTL when disabled). Do not key solely by user ID: multiple browser/device logins must expire independently. Use an atomic “check exists + compare elapsed + update last_activity + refresh TTL” operation; never resurrect a deleted key during a racing request.
4. **HTTP guard:** after cryptographic JWT verification and before `_auth_cache`/request-state returns in required and optional dependencies, require `sid` when idle timeout is enabled, check/touch Redis, and raise HTTP 401 with the project’s Chinese invalid/expired-token style when absent/expired. Keep user/role cache behavior unchanged after the guard. The refresh endpoint must decode/validate the refresh token, run the same session check/touch (so a refresh is activity), then rotate tokens while preserving `sid`; reject missing/expired session records.
5. **WebSocket guard:** call the same session check at handshake (`get_current_user_from_websocket`). During the receive loop, treat validated client activity/heartbeat as a touch. To enforce a quiet socket, use a timeout/ping task or `asyncio.wait_for` around `receive_text` based on the current idle setting; close and disconnect when the Redis record expires. Existing connections should observe a newly shortened timeout on their next check; a setting increase applies on the next activity/touch.
6. **Hot-reload semantics:** evaluate `last_activity` against the current timeout on every guard call, not only Redis key TTL. This makes shortening effective immediately even if old keys retain longer TTLs; update `EXPIRE` to the new timeout on successful touch. Setting 0 bypasses checks (and may delete/let keys age out). Existing tokens minted before deployment without `sid` are a rollout concern—either reject them when timeout is enabled (force re-login) or provide a one-time compatibility path that creates a session record, with an explicit security tradeoff.

## Compatibility / rollout risks

- Existing access/refresh JWTs have no `sid`; enabling a non-zero timeout without a migration policy can either fail all existing sessions or accidentally allow untracked sessions. Prefer a documented force re-login on first use when enabled, or gate the feature behind `sid` presence and keep timeout disabled until most tokens roll over.
- Refresh is a public endpoint and currently accepts any cryptographically valid refresh JWT plus an existing user. Without an explicit Redis check, idle sessions can be revived indefinitely by refresh calls.
- `_auth_cache` can hide Redis expiry for up to 45 seconds (or longer through request state) unless guard ordering is fixed. This is a correctness/security issue, not just a performance detail.
- A frontend heartbeat/polling loop and WebSocket heartbeats count as activity under a request-based policy; product wording should clarify whether background polling should keep users logged in. If “human interaction only” is required, activity must be explicitly signaled rather than touching on every authenticated request.
- Multi-instance deployments depend on Redis availability. Decide fail-open versus fail-closed when Redis is unavailable; fail-closed is safer for enforced timeout but can log users out during a Redis outage. Log/metric guard failures.
- Changing timeout does not retroactively rewrite every key. Timestamp comparison handles this safely; key TTL alone would make shortening ineffective until the old TTL elapses.

## Test targets

- `tests/infra/auth/` and `tests/api/test_deps_auth_cache.py`: token claims include stable `sid` across access/refresh rotation; every password/OAuth/OA issuance path creates a Redis session; missing/expired sid returns 401; cached-user path still performs idle check/touch; optional dependency behavior is explicit.
- `tests/api/routes/test_oauth_routes.py`, `tests/infra/test_oauth_service.py`, `tests/infra/auth/test_oa_login.py`: verify all login providers use the common session issuance helper and honor configured `expires_in`.
- Add refresh route tests (`tests/api/routes`): valid active sid rotates tokens and touches activity; expired/missing sid is rejected even when refresh JWT `exp` is valid; refresh preserves sid.
- Add Redis/session-store unit tests with a fake Redis: atomic touch rejects missing keys, detects elapsed timeout, updates `last_activity`, refreshes TTL, and handles concurrent expiry without resurrection; timeout 0 bypasses/disabled behavior.
- Extend `tests/api/routes/test_websocket_route.py` and `tests/api/test_deps_auth_cache.py`: handshake rejects expired sid, heartbeat touches activity, idle receive timeout closes socket and manager disconnects, and existing auth-message/offloaded JSON behavior remains.
- `tests/infra/settings/test_settings_storage.py` / `tests/infra/settings/test_settings_service.py`: admin setting appears in definitions, accepts valid integer bounds, rejects negative/fractional/NaN/Infinity/boolean values, broadcasts and hot-refreshes without restart, and reset restores default disabled value.

## Related specs

- `.trellis/spec/backend/index.md`
- `.trellis/spec/backend/quality-guidelines.md` (typed APIs, auth dependencies, async I/O, Redis through storage classes)
- `.trellis/spec/backend/error-handling.md` (401 dependency errors and explicit exception handling)

## Caveats / Not Found

- No existing logout/revocation endpoint or per-login session store was found; idle expiration will be the first server-side session lifecycle. Do not infer logout semantics from `UserStorage.touch_updated_at`, which is only a user activity statistic.
- No minimum/range metadata is currently honored by `SettingsStorage`; adding only a definition field without storage/API validation would not enforce numeric bounds.
- The current WebSocket protocol has no documented heartbeat message type; implementation should define one (or treat all valid client frames as activity) before claiming idle enforcement for connected sockets.
