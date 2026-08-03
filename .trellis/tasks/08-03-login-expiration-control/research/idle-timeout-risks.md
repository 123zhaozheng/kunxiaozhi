# Research: Idle-login timeout reliability risks

- Query: Pressure-test an admin-configurable "log out after N hours without operation" policy, including activity semantics, token refresh, multi-tab/device behavior, infrastructure failures, and long-lived streams.
- Scope: internal / mixed (repository inspection plus security/operability reasoning)
- Date: 2026-08-03

## Findings

### Existing auth flow and anchors

- JWTs are stateless. `create_access_token` and `create_refresh_token` put only `sub`, `iat`, `exp` (plus refresh `type`) in the token; there is no session id, token version, last-activity claim, or revocation handle (`src/infra/auth/jwt.py:18-88`). `TokenPayload` likewise has no such fields (`src/kernel/schemas/user.py:102-110`).
- Every refresh request validates the presented refresh JWT, verifies that the user still exists, and then issues a brand-new access **and refresh** token with a full configured lifetime (`src/api/routes/auth/core.py:137-194`). This makes the current behavior rolling and allows an active client to extend login indefinitely unless a separate absolute/idle check is added.
- Password login calls the same token helpers (`src/infra/user/manager.py:61-123`); OAuth callback and OA SSO issue tokens independently (`src/infra/auth/oauth.py:267-279`, `src/infra/auth/oa_login.py:65-78`). A timeout policy implemented only in `/login` or one route would leave entry points inconsistent.
- Required HTTP routes enforce auth through `get_current_user_required`, which verifies the JWT then loads the current user/roles and caches the result for 45 seconds (`src/api/deps.py:17-40`, `src/api/deps.py:101-181`). An idle check in this dependency covers most APIs, but does not automatically cover the public `/api/auth/refresh` path or WebSocket authentication.
- WebSocket authentication is performed once at connection setup (`src/api/routes/websocket.py:66-121`), after which a loop only receives client text/heartbeats (`src/api/routes/websocket.py:122-138`). A connected socket can therefore outlive an idle deadline unless explicitly timed/closed. SSE stream authorization is also only at open (`src/api/routes/chat.py:616-669`); heartbeats are emitted by the stream generator and should not be treated as user activity.
- Frontend requests proactively refresh expired access tokens (`frontend/src/services/api/tokenManager.ts:39-62`) and retry 401s (`frontend/src/services/api/fetch.ts:47-82`, `frontend/src/services/api/authenticatedRequest.ts:35-67`). Refresh calls are deduplicated per tab via a module-level promise (`frontend/src/services/api/tokenManager.ts:64-120`), but tabs/devices have separate promises and can race. Existing refresh-token rotation is not single-use/replay-protected.
- The frontend already reacts to `X-Force-Relogin` for role changes (`frontend/src/services/api/fetch.ts:64-68`; server emission `src/api/routes/user.py:66-79`). Idle expiry should return a normal 401/clear auth state, or define a distinct reason/header if UX needs to distinguish it.
- `ACCESS_TOKEN_EXPIRE_HOURS` and `REFRESH_TOKEN_EXPIRE_DAYS` are already admin settings (`src/kernel/config/_definitions_extra.py:9-30`) and global defaults (`src/kernel/config/base.py:243-247`). Numeric settings currently receive type-only validation (no finite/positive/min/max checks) in `src/infra/settings/storage.py:147-181`; `bool` is an `int` in Python, so `true` can accidentally pass a number check.
- Setting writes refresh the local global settings object and publish a best-effort Redis notification (`src/infra/settings/service.py:118-145`, `src/infra/settings/service.py:223-245`). A publish/Redis outage is logged and does not fail the write; other API instances may continue enforcing stale timeout values until restarted or otherwise refreshed (`src/infra/settings/pubsub.py:60-83`).

### Release-blocking design risks

1. **Idle vs. absolute semantics must be explicit.** Merely shortening access-token `exp` does not implement "three hours without operation"; merely counting refresh as activity permits a background client to stay logged in forever. MVP should define a server-side idle window and (if desired) a separate absolute session cap. If only idle is in scope, say that clearly and document that continuous qualifying activity keeps the session alive.
2. **Define qualifying activity, server-side.** Count authenticated, user-initiated operations (for example chat submission, navigation/API reads that represent user actions) using the server's auth dependency. Do not count JWT refresh, SSE heartbeats, WebSocket heartbeats, browser polling, retries, static assets, or background reconnects; otherwise an unattended tab bypasses the policy. Avoid a per-request database write; throttle/coalesce activity updates and make the update atomic.
3. **Choose account-wide vs. token/session scope.** With no session identifier today, a per-user `last_activity` means activity in tab/device A keeps tab/device B alive. That is the least surprising MVP for "user login state" but must be documented. Per-device idle expiry requires a server-side session id/jti and revocation store and is a larger follow-up.
4. **Config changes need deterministic behavior.** Existing tokens embed their original `exp`; changing a setting affects newly minted tokens only. For an idle setting, every request should compare current time with persisted last activity and current configured window. Decreasing the value must expire old sessions immediately on their next protected request/refresh; increasing it must not resurrect a session that already crossed an absolute cap (if one exists). Decide whether zero disables the feature or is invalid; do not let a bad value silently create immediately-expiring or unbounded tokens.
5. **Redis failure and multi-instance consistency are security decisions.** If last activity is stored only in Redis, fail-open on an outage allows bypass; fail-closed can log everyone out. If stored only in process memory, load-balanced requests disagree. Prefer a durable/shared store or explicit fail-closed behavior with metrics and a clear admin error. Settings pub/sub is already best-effort, so tests must cover an instance that missed a config-change notification.
6. **Refresh endpoint is an escape hatch.** `/api/auth/refresh` is intentionally public (`src/api/middleware/auth.py:41-59`) and currently accepts a valid refresh token regardless of last activity. Apply the same idle/absolute checks atomically before issuing replacement tokens; a refresh after idle expiry must be 401 and must not rotate/extend the session. Do not treat every refresh as qualifying activity.
7. **Legacy/forced-relogin compatibility.** Existing tokens have no idle metadata and may remain valid until their embedded `exp`. Define a migration rule (for example, derive initial activity from `iat`, enforce the new policy on first request, and reject tokens older than the configured maximum) and test it. Role-change forced relogin currently works through a response header only; do not accidentally allow an idle-expired token to continue because a 45-second auth cache returns a stale payload (`src/api/deps.py:31-58`, `src/api/deps.py:127-151`).
8. **Long-running SSE/WebSocket behavior.** Decide whether an open stream/socket counts as active only when it carries a real user command, and whether an idle timer closes it. At minimum, protect new stream/socket handshakes and ensure heartbeats cannot reset last activity. If background task execution continues after logout, document it and ensure subsequent result-fetch APIs reject the expired user.
9. **Refresh races and replay.** Concurrent tabs can refresh the same token and each receive a new pair; without single-use refresh-token state, an attacker or stale tab can replay a token until its JWT expiry. Deduplicate only within one tab is insufficient (`frontend/src/services/api/tokenManager.ts:64-120`). For MVP, make idle checks race-safe (compare-and-set last activity / session state); consider refresh-token rotation/reuse detection as a separate security hardening item.
10. **Client-side bypasses.** Never trust a browser timer, localStorage timestamp, or the presence of a non-expired access token. Enforce on every protected API, refresh, SSE/WS handshake, and any alternate login path. WebSocket query-token support (`src/api/routes/websocket.py:24-37`) also exposes tokens in URLs/logs; do not add idle bypasses to that path.

### Recommended MVP boundary

- Implement one admin setting with an explicit unit (hours or minutes), bounded finite positive range, and a documented disabled value if needed. Reuse the existing settings permission/UI, but add range validation and reject booleans/NaN/infinity.
- Enforce a shared, account-level idle timestamp in the backend auth layer and refresh endpoint. Update it only for a narrow, documented set of qualifying user actions; throttle writes. Return 401 once `now - last_activity >= timeout` and clear/rotate state so a refresh cannot revive it.
- Apply the policy consistently to password, OAuth, and OA SSO issued sessions; existing tokens should be accepted only under an explicit migration rule and should be covered by tests.
- Keep absolute lifetime, per-device sessions, global token revocation, refresh-token reuse detection, and cancellation of already-running agent jobs out of MVP unless the product owner confirms they are required. They should be named follow-ups because idle-only semantics do not provide those guarantees.

### Acceptance-test matrix (release gate)

- Setting validation: accepted boundary values and disabled behavior; reject zero/negative (unless documented), fractional/overflow, NaN/Infinity, strings, and booleans; API and UI show the same unit/default.
- Normal flow: login via password, OAuth, and OA SSO; qualifying activity before the deadline keeps the account alive; no activity beyond the deadline causes the next protected API to return 401 and the frontend to clear tokens/redirect.
- Refresh: refresh within idle window succeeds; refresh after idle expiry returns 401 and does not issue either token; repeated/concurrent refreshes across two tabs cannot extend an already-expired session.
- Non-activity: SSE heartbeat, WebSocket heartbeat, browser polling, automatic retry/reconnect, static requests, and refresh-only traffic do not reset idle time. A real user command does reset it once (throttled).
- Scope: activity in another tab/device follows the documented account-wide policy; a session-specific policy test should fail fast or be explicitly out of scope.
- Config changes: lowering timeout expires an older idle session on its next request; increasing timeout does not resurrect an already-expired session; all instances converge after setting change, and behavior during Redis/pub-sub outage matches the documented fail-open/closed policy.
- Long-lived transports: an SSE/WS handshake after expiry is rejected; an established connection either closes at the deadline or follows a documented exception, and heartbeats never bypass expiry.
- Legacy/security: token issued before the feature rollout follows migration policy; role-change `X-Force-Relogin` remains intact; tampered client timestamps/localStorage cannot bypass server checks.

## Related specs

- `.trellis/spec/backend/error-handling.md` (401 dependency errors and SSE cleanup)
- `.trellis/spec/backend/database-guidelines.md` (MongoDB storage, async access, schema defaults)
- `.trellis/spec/backend/quality-guidelines.md`
- `.trellis/spec/frontend/quality-guidelines.md`

## Caveats / Not Found

- No existing session/revocation model or server-side token `jti` was found; implementing per-device idle expiry or immediate global logout would require new state and is not a small JWT-only change.
- No test currently exercises idle timeout, refresh-after-idle, or cross-instance settings convergence; the listed matrix should be added before release.
- The product request says "3 hours without operation" while the current PRD still asks an open question about absolute login lifetime. Treat this as a decision gate: do not ship until idle-only versus idle-plus-absolute semantics are approved.
