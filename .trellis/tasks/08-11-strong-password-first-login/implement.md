# Implementation Plan: Strong Password and First-Login Password Change

## Ordered Checklist

- [ ] Add direct `zxcvbn==4.5.0` dependency with `uv`, refresh `uv.lock`, and record license review.
- [ ] Add shared backend password policy schemas/helpers and focused boundary tests before changing account routes.
- [ ] Remove bcrypt silent truncation for new inputs by rejecting over-72-byte values before hashing; preserve verification compatibility for existing hashes.
- [ ] Add compatible user fields and storage methods for atomic credential mutation/version increment.
- [ ] Set `must_change_password=true` in local registration, admin creation, generic OAuth creation and OA provisioning; cover existing/new account branches.
- [ ] Add `credential_version` to access/refresh tokens and enforce current-version checks in refresh, HTTP dependencies, WebSocket auth and stream entry paths.
- [ ] Add the restricted-user dependency/error contract and ensure only `/me`, activity, refresh and change-password can use it.
- [ ] Implement first/ordinary change-password behavior, reset-password parity, audit logging and token/session invalidation semantics.
- [ ] Add 60-second idle rechecks to generic agent SSE without turning heartbeats/events into activity.
- [ ] Extend frontend user/API types, add shared policy feedback, implement the dedicated forced-change screen and route all local/OAuth/OA flows through it.
- [ ] Add ProtectedRoute and cross-tab tests proving deep links, refresh and alternate login channels cannot bypass the state.
- [ ] Run backend and frontend focused suites, then full lint/type/build and auth regression tests.

## Validation Commands

```powershell
uv run pytest tests/infra/auth tests/api -q
uv run pytest tests/infra/auth/test_login_idle_session_feature.py -q
pnpm --dir frontend test -- --run
pnpm --dir frontend run lint
pnpm --dir frontend run build
```

Use the repository's authoritative narrower test targets discovered during implementation when full `tests/api` is too broad, but the final check must include every changed auth/SSE/WebSocket path.

## Risk and Rollback Points

- Token schema/version comparison can log out users if legacy default handling is wrong; land compatibility tests before enabling version checks.
- A server gate without the forced-change UI can lock new users out; backend and frontend enforcement must ship together.
- `zxcvbn` scoring must be deterministic in tests and must not block the event loop under load.
- If rollout must be disabled, stop setting `must_change_password`, disable the normal-state gate, and leave additive database fields intact.

## Review Gates

- Verify all account creation channels were found by search, not only OA.
- Verify caches cannot bypass current credential version or `must_change_password`.
- Verify 403 `PASSWORD_CHANGE_REQUIRED` never triggers the 401 token-clearing path.
- Verify no password, token, hash or user-input strength detail enters logs/traces.
