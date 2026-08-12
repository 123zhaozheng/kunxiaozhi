# Research: Current Authentication and First-Login Password Security

- Query: Audit account creation/login channels, password hashing and validation, first-login password state, JWT/session authorization, SSE/WebSocket enforcement, frontend auth routing, migration constraints, and tests for the strong-password/mandatory-first-login-password-change task.
- Scope: internal
- Date: 2026-08-11

## Findings

### Password hashing and validation (confirmed facts)

- Passwords are hashed and verified with bcrypt in `src/infra/auth/password.py:38-69`. `_truncate_password` encodes UTF-8 and silently truncates at 72 bytes before both hashing and verification (`:10-35`, `:48-51`, `:66-69`). Distinct values sharing the first 72 bytes therefore authenticate identically; a new strength contract must either reject values over the bcrypt byte limit or migrate to a hash with a larger input limit (for example Argon2id).
- `UserCreate.password` and `UserUpdate.password` only require `min_length=6` and remain optional in `src/kernel/schemas/user.py:31-37` and `:39-57`. `ResetPasswordRequest.new_password` also only requires `min_length=6` at `:143-148`. `LoginRequest.password` has no length/strength validation (`:130-135`).
- Registration UI duplicates the six-character rule (`frontend/src/components/auth/AuthPage.tsx:238-250`), and reset/profile UI do the same (`frontend/src/components/auth/ResetPassword.tsx:41-52`, `frontend/src/components/profile/tabs/ProfilePasswordTab.tsx:21-41`). These are UX checks only; backend schemas/storage are the enforcement boundary.
- User creation hashes supplied passwords off the event loop in `src/infra/user/storage.py:159-180`; OAuth users with no password get a random `secrets.token_urlsafe(32)` before hashing (`:160-166`). Updates hash a supplied password at `:312-316`. Authentication looks up username, then email, and calls bcrypt verification at `:464-492`.

### User schema, storage, and migration shape (confirmed facts)

- Public and DB user models have no first-login/password-change flag. `User` fields end at status/timestamps in `src/kernel/schemas/user.py:59-71`; `UserInDB` adds only `password_hash` and email/reset token fields at `:91-99`.
- There is no database migration framework. The project relies on application compatibility/defaults (backend spec `.trellis/spec/backend/database-guidelines.md`, especially schema conventions). `UserStorage._ensure_indexes` runs indexes and `_migrate_legacy_users`, but that migration only changes `email_verified` and `is_active` nulls (`src/infra/user/storage.py:86-136`). A new field must be added with a Pydantic default and, if query/update semantics require materialized values, an idempotent application migration/backfill.
- `UserStorage.create` persists `password_hash` as `None` when a non-OAuth `UserCreate` has no password (`src/infra/user/storage.py:159-180`), although `UserInDB.password_hash` is typed as `str` (`src/kernel/schemas/user.py:91-98`). Normal UI registration always sends a password, but admin/API callers can submit the optional field and should be handled explicitly by the new contract.
- Admin account creation is a separate channel: `POST /api/users/` accepts `UserCreate`, forces `skip_verification=True`, and calls `UserManager.register` (`src/api/routes/user.py:29-39`). This path is permission-protected but currently accepts the same optional six-character password schema.

### Account creation and login channels (confirmed facts)

1. **Username/email registration and password login**
   - `POST /api/auth/register` checks `ENABLE_REGISTRATION`, optional Turnstile, and calls `UserManager.register` (`src/api/routes/auth/core.py:47-70`). It returns a user/verification status, not tokens (`:113-118`). `UserManager.register` assigns first-user admin/default roles and calls storage create (`src/infra/user/manager.py:35-59`).
   - `POST /api/auth/login` calls `UserManager.login` (`src/api/routes/auth/core.py:121-143`). Login rejects unverified/inactive users, updates `updated_at`, and creates an idle-backed access/refresh pair through `create_token_pair` (`src/infra/user/manager.py:61-118`).
   - Registration's first-user shortcut sets `skip_verification=True` only when roles are absent and no users exist (`src/infra/user/manager.py:45-56`); it does not mark a password as trusted or force a change.

2. **Admin/API-created users**
   - `POST /api/users/` is protected by `user:write`, but its payload is the same `UserCreate` with optional six-character password (`src/api/routes/user.py:29-39`; schema `src/kernel/schemas/user.py:31-37`). There is no separate temporary-password or must-change marker.

3. **Generic OAuth (Google/GitHub/Apple)**
   - OAuth start/callback routes are public and use an opaque state stored/verified with client IP (`src/api/routes/auth/oauth.py:75-113`, `:174-242`, `:245-276`). Callback exchanges provider code, finds/binds/creates a user, and issues `create_token_pair` (`src/infra/auth/oauth.py:215-282`, especially `:257-275`).
   - Existing users are matched by OAuth id or email; email matches are bound to the provider (`src/infra/auth/oauth.py:427-447`). New users are denied when `ENABLE_REGISTRATION` is false (`:449-455`), otherwise created without a user-supplied password (`:480-491`), causing storage to generate a random password (`src/infra/user/storage.py:160-166`). No first-login state is set for either existing or newly created OAuth users.
   - Browser callback tokens are placed in a URL fragment by the backend (`src/api/routes/auth/oauth.py:236-242`, `:263-276`) and read/stored by `frontend/src/components/auth/OAuthCallback.tsx:30-68`; the fragment is not sent in HTTP requests, but the frontend currently does not explicitly clear the hash after reading.

4. **OA SSO deep-link and synchronized registration/provisioning**
   - Frontend `/auth/login` accepts exact `Accesstoken` plus legacy `token`/`oa_token` aliases and redirects to `/auth/oa` (`frontend/src/components/auth/AuthPage.tsx:127-136`; parser contract `.trellis/spec/frontend/oa-sso-entry.md`). `/auth/oa` strips all accepted token query keys before calling the backend (`frontend/src/components/auth/OaSsoLogin.tsx:25-30`, `:57-81`).
   - `POST /api/auth/login/oa-sso` is public, rate-limited at 10 requests/minute per client IP, exchanges the portal token for a workcode, and then calls OA provisioning/login (`src/api/routes/auth/oa_sso.py:25-65`). OA exchange encrypts `channel_id-token` with configured SM2 public key, supports one refresh retry, and returns a workcode (`src/infra/auth/oa_sso.py:36-86`).
   - `login_or_provision_from_workcode` looks up username=workcode. If missing and auto-provision is enabled, it creates a user with `password=secrets.token_urlsafe(32)`, skips email verification, assigns admin for the first account or the configured default role, then issues `create_token_pair` (`src/infra/auth/oa_login.py:21-75`, specifically `:34-53`, `:65-75`). This is the highest-risk bypass for the requested feature: an OA-provisioned account receives a usable random password that the owner never chose, and no mandatory first-login change is recorded.

5. **Password reset and profile password change**
- `POST /api/auth/forgot-password` is public, rate-limited, stores a reset token/expiry, and sends email (`src/api/routes/auth/verification.py:29-114`). `POST /api/auth/reset-password` is public and updates `password_hash` from `new_password` after token/expiry checks (`:117-160`), but only the six-character schema rule applies.
- Reset-password updates the MongoDB hash and clears the reset token, but does not enumerate/revoke Redis login sessions or otherwise invalidate already-issued JWTs (`src/api/routes/auth/verification.py:148-160`; session removal API exists only as `src/infra/auth/session.py:146-151`).
   - The frontend exposes `authApi.changePassword(oldPassword,newPassword)` to `/api/auth/change-password` (`frontend/src/services/api/auth.ts:279-296`) and calls it from `ProfilePasswordTab` (`frontend/src/components/profile/tabs/ProfilePasswordTab.tsx:17-56`). No backend route matching `/api/auth/change-password` exists in `src/api/routes/auth` (routes included by `src/api/routes/auth/__init__.py:19-23`), so the current UI action returns a route-level 404. `TURNSTILE_REQUIRE_ON_PASSWORD_CHANGE` is configured/exposed (`src/kernel/config/base.py:353-358`, `src/api/routes/auth/oauth.py:56-71`) but has no functioning password-change endpoint.

### JWT, idle sessions, and request authorization (confirmed facts)

- JWT access/refresh creation includes optional opaque `sid` (`src/infra/auth/jwt.py:18-51`, `:54-91`). `create_token_pair` creates one Redis-backed session and puts the same sid in both tokens (`:154-162`). Redis session state is `auth:login-session:{sid}` with user id, last activity, and refresh-token TTL (`src/infra/auth/session.py:14-15`, `:64-81`).
- JWT verification requires `sub`, `exp`, and `iat`, and carries `sid` into `TokenPayload` (`src/infra/auth/jwt.py:120-151`; schema `src/kernel/schemas/user.py:101-110`). Legacy/missing sid is rejected by `assert_active` (`src/infra/auth/session.py:97-119`).
- `get_current_user` and `get_current_user_required` verify JWT then call `assert_active` before request-state or process auth-cache use (`src/api/deps.py:88-123`, `:130-218`). Required auth then reloads the user and role permissions from MongoDB (`:190-211`). WebSocket authentication also verifies token, asserts idle session, reloads user/roles (`src/api/deps.py:221-281`).
- Public refresh decodes the refresh JWT, verifies `type`, user existence, checks `assert_active`, and rotates both tokens with the same sid without touching activity (`src/api/routes/auth/core.py:161-215`).
- Explicit activity is separate: `GET /api/auth/activity` asserts only; `POST /api/auth/activity` calls `touch` (`src/api/routes/auth/core.py:242-263`). The backend idle-session contract is captured in `.trellis/spec/backend/auth-idle-sessions.md`; frontend activity behavior is captured in `.trellis/spec/frontend/auth-idle-monitor.md`.
- `AuthMiddleware` has a broad public-prefix bypass for auth endpoints, static resources, `/api/agents`, and SPA pages (`src/api/middleware/auth.py:17-56`). Protected API routes still rely on their own `Depends` guards; any new first-login guard must be placed at the dependency/route contract level, not assumed from middleware alone.

### SSE and WebSocket enforcement (confirmed facts and gaps)

- Chat session SSE (`GET /api/chat/sessions/{session_id}/stream`) requires `get_current_user_required` and session ownership, then re-checks `assert_active` on 60-second timeout/traffic boundaries; heartbeats do not touch activity (`src/api/routes/chat.py:616-641`, `:645-687`). Redis errors keep retrying, while idle expiry ends the stream.
- The generic agent SSE endpoint (`POST /api/{agent_id}/stream`) requires auth at request start (`src/api/routes/agent/__init__.py:471-477`) but its event generator only iterates agent events and cleans trace context; it has no periodic `assert_active` check (`:509-533`). A long-running stream can therefore continue after idle expiry unless downstream task behavior stops it. Any mandatory first-login enforcement must be checked before this endpoint and, if the product requires mid-stream revocation, add periodic checks.
- `/ws` authenticates either query token or an initial auth message, checks `assert_active` during handshake, and re-checks at most every 60 seconds on timeout or client messages; idle expiry closes code 4001, Redis errors are retried (`src/api/routes/websocket.py:67-118`, `:126-162`). WebSocket heartbeats/messages do not touch activity.
- WeCom bot WebSockets under `src/infra/agent/wecom/` are server-to-OA integrations, not browser user sessions; they do not use JWT/user `sid` and are outside first-login enforcement.

### Frontend auth state/routing (confirmed facts)

- `AuthProvider` stores access/refresh tokens in localStorage, refreshes expired access tokens, fetches `/api/auth/me`, and mounts the idle monitor only when both token and user exist (`frontend/src/hooks/useAuth.tsx:107-120`, `:121-189`). Login, OAuth callback, and OA SSO all set tokens then fetch user data (`:229-329`; `frontend/src/components/auth/OaSsoLogin.tsx:69-96`; `frontend/src/components/auth/OAuthCallback.tsx:53-68`). None inspect a first-login/password-change flag.
- `ProtectedRoute` checks only authenticated state and permissions, then renders the target route (`frontend/src/components/auth/ProtectedRoute.tsx:66-141`). `App.tsx` has public login/OA/reset/verify routes and protected application routes, but no mandatory password-change route or gate (`frontend/src/App.tsx:406-420`, `:600-608`).
- `authFetch`/token manager retries 401 with refresh and preserves tokens on non-401 refresh errors (`frontend/src/services/api/tokenManager.ts:49-70`, `:80-127`; `frontend/src/services/api/authenticatedRequest.ts:27-65`). A first-login API response must be compatible with this flow and should not be encoded only in an unsigned client-side JWT decode.

### Existing tests and missing coverage (confirmed facts)

- Auth tests cover OAuth registration enable/disable and role metadata (`tests/infra/auth/test_username_permission_and_registration.py:41-111`), OA existing/provisioned/missing-user paths (`tests/infra/auth/test_oa_login.py:10-70`), OA client timeout (`tests/infra/auth/test_oa_sso.py:9-17`), idle-session Lua/expiry/user mismatch (`tests/infra/auth/test_login_idle_session_feature.py:9-71`), and password hashing off the event loop (`tests/infra/test_user_storage_password_blocking.py:73-123`).
- Frontend tests cover OA token key precedence/removal (`frontend/src/components/auth/__tests__/oaSsoToken.test.ts:8-41`) and only OAuth URL construction (`frontend/src/services/api/__tests__/auth.test.ts:6-16`).
- No tests assert a strong password policy (composition, length/byte cap, common-password rejection, Unicode handling, or bcrypt truncation), first-login state creation/clearing, reset/change-password policy parity, OA/OAuth first-login behavior, route-level mandatory gate, or generic agent SSE expiry. There is no backend change-password route test because the route is absent.

## Confirmed Security Gaps / Risks

1. **Mandatory first-login change is absent and bypassable by every token channel.** No user field, response field, dependency guard, frontend route, or backend password-change endpoint exists. OA auto-provisioning explicitly creates a random password and immediately issues normal tokens (`src/infra/auth/oa_login.py:47-75`), making it the clearest requested-feature bypass.
2. **Password policy is weak and inconsistent by design.** All creation/update/reset schemas accept six characters; login accepts any string; UI checks are not authoritative. Bcrypt silently truncates over 72 bytes.
3. **Frontend password-change control is broken.** `ProfilePasswordTab` calls a non-existent backend route, and Turnstile's password-change setting is dead configuration.
4. **Generic agent SSE lacks idle re-checks.** Long streams may outlive an idle session, unlike chat SSE and `/ws`.
5. **Password reset does not revoke existing sessions.** A reset token changes the hash but leaves previously issued sid/JWT pairs active until normal expiry/idle timeout.
6. **Schema migration needs explicit treatment.** Mongo documents have no migration runner; absent/new field defaults must be safe for existing users, and any backfill must be idempotent and non-locking.

## Recommended Design Options and Tradeoffs

- Add a persisted boolean such as `must_change_password` (default `False` for existing trusted users) to `User`, `UserInDB`, and `UserUpdate`, plus a token/API response indicator. Mark it `True` for OA auto-provisioned accounts and any other account receiving a generated/temporary password; decide whether OAuth-created accounts should also be forced to set a local password or remain passwordless/social-only.
- Enforce a single backend password validator reused by `UserCreate`, `UserUpdate`, `ResetPasswordRequest`, and change-password input. Define exact minimum length, character requirements, maximum UTF-8 bytes, whitespace policy, and whether breached/common-password checks are in scope. If retaining bcrypt, reject >72 bytes rather than silently truncating; Argon2id avoids this cap but requires a hash-format migration and dependency/CPU-cost decision.
- Add an authenticated `POST /api/auth/change-password` accepting current/new password (or a separate first-login endpoint that does not require current random password). Verify the old password for ordinary changes, atomically update hash and clear `must_change_password`, invalidate/rotate sessions as product policy dictates, and apply Turnstile only when configured. Ensure reset-password also clears the flag if a reset is considered proof of user control.
- Enforce the gate server-side after JWT + idle-session validation and before normal user/RBAC access: permit only password-change, logout, activity/status, and perhaps profile/bootstrap endpoints while `must_change_password=True`; return a stable 403/409 machine-readable code. Add the same gate to WebSocket handshake and SSE entry; decide whether existing transports should be closed when the flag is set mid-connection.
- Frontend should receive the authoritative flag from `/api/auth/me`/login response, route to a dedicated first-login password screen, and have `ProtectedRoute` redirect every protected route until the flag clears. Do not rely on localStorage or unsigned JWT payload claims. OA/OAuth callbacks must run the same post-login routing.
- Migration choices: (a) default missing field to `False` and only set it for newly provisioned/generated-password accounts (lowest disruption); (b) backfill all legacy accounts to `True` (stronger posture, but requires a recovery UX and coordinated rollout); or (c) store a version/timestamp to distinguish accounts created before policy launch. Existing refresh tokens should continue carrying sid; a forced-change gate can be evaluated from current Mongo user state on each protected request.

## Recommended Test Matrix

- Validator: minimum/maximum boundaries, Unicode byte length, whitespace, composition/common-password rules, and rejection before hashing; assert no bcrypt >72-byte silent equivalence.
- Account channels: normal registration, first-user/admin creation, admin `/api/users/`, OAuth existing/new, OA existing/new auto-provision, and reset-password all set/clear the chosen flag correctly.
- Auth responses/dependencies: login and `/me` expose the flag; protected endpoints return the stable forced-change response; allowed password-change/activity/logout endpoints remain reachable; auth-cache hits still evaluate current flag.
- Password change: old-password verification, strong-policy parity, Turnstile success/failure, atomic flag clearing, session invalidation/rotation, replay/race behavior, and reset-token one-time use.
- Transport: WebSocket handshake denied/limited while forced; chat SSE and generic agent SSE behavior at entry and at periodic idle checks; no heartbeat/activity touch bypass; existing stream closure on mid-session flag change if selected.
- Frontend: login/OAuth/OA callback routes to first-login screen, `ProtectedRoute` cannot bypass via deep link, change success returns to intended path, cross-tab state converges, and refresh/non-401 errors preserve correct state.

## Related Specs

- `.trellis/spec/backend/auth-idle-sessions.md` - sid creation/refresh, atomic idle checks, auth ordering, SSE/WebSocket checks, and required session tests.
- `.trellis/spec/frontend/auth-idle-monitor.md` - token storage, explicit activity events, refresh error behavior, and cross-tab logout.
- `.trellis/spec/frontend/oa-sso-entry.md` - exact OA `Accesstoken` parser, aliases, and URL stripping.
- `.trellis/spec/backend/database-guidelines.md` - Mongo/Pydantic schema defaults and application-level migration convention.
- `.trellis/spec/backend/error-handling.md` - dependency/route HTTP error mapping and streaming cleanup.

## Remaining User-Owned Product Decisions

- Exact strong-password policy and whether to use bcrypt-with-byte-cap or Argon2id.
- Which account classes must change a password: OA-generated only, OAuth-created too, admin-created temporary passwords, all legacy users, or a combination.
- Whether reset-password clears the first-login flag; whether first-login password change requires the old generated password.
- Allowed unauthenticated/limited endpoints during the forced-change state and stable status/code contract.
- Whether changing a password revokes all existing sessions, only the current sid, or rotates tokens in place.
- Whether generic agent SSE must be periodically rechecked/closed after flag changes, and how to handle existing transports.

## Caveats / Not Found

- No backend `POST /api/auth/change-password` implementation was found; only the frontend client and profile UI reference it.
- No existing first-login/password-change field or migration was found in source, tests, or config.
- The audit did not inspect external OA provider documentation; it records only the repository's SM2 request/response contract.
- Existing working-tree changes are in unrelated trace/task files; no auth product files were modified.
