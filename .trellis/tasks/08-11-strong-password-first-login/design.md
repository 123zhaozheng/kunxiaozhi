# Design: Strong Password and First-Login Password Change

## Boundaries

```text
account creation -> must_change_password=true
login/OAuth/OA -> limited JWT pair -> /me -> forced-password route
forced change -> atomic credential update/version bump -> clear client tokens
next login -> normal JWT pair -> ordinary RBAC and business routes
```

The backend remains authoritative. Frontend routing improves UX but is not an authorization boundary.

## Data Model and Compatibility

Add compatible defaults to user schemas and persistence:

- `must_change_password: bool = False`
- `credential_version: int = 0`
- `password_changed_at: datetime | None = None`

Creation code explicitly writes `must_change_password=true` for every new account channel. Missing fields on legacy MongoDB documents deserialize to the defaults, so rollout does not lock existing users. An idempotent application migration may materialize defaults, but correctness cannot depend on it.

Token payloads add `credential_version`. Legacy tokens are treated as version `0`, so existing sessions survive deployment until their user version changes. Refresh and protected dependencies compare the token version to the current user after JWT and Redis idle checks.

## Password Policy Service

Create one context-aware backend validator, reused by every password-setting boundary. It performs deterministic checks before `zxcvbn`, normalizes only comparison inputs with Unicode normalization/casefold, and hashes the original password unchanged. `zxcvbn` receives username/email tokens and rejects scores 0-1.

Generated OA/OAuth temporary secrets bypass this human validator only inside the account-provisioning service. They remain unknown to users and always create a restricted account.

CPU-heavy bcrypt remains behind the project's blocking-I/O helper. Profile `zxcvbn`; move scoring off the event loop if necessary.

## Authentication and Authorization

Token issuance remains common across password, OAuth and OA. Restricted accounts receive valid idle-backed tokens so they can call the small completion API surface.

Split authenticated dependencies into:

- base identity: JWT -> idle session -> current user -> credential-version check;
- normal identity: base identity -> reject `must_change_password` -> RBAC;
- password-change identity: base identity without the normal-state gate.

Use a structured error body containing `code="PASSWORD_CHANGE_REQUIRED"` with HTTP 403. Do not encode this as 401, because token refresh must not erase a valid restricted session.

Allowed restricted routes are `/api/auth/me`, GET/POST activity, refresh, change-password, and the existing client-side logout flow. Every business route, SSE entry and WebSocket handshake uses the normal dependency. Long-lived transports keep the existing 60-second idle checks; generic agent SSE is brought into parity.

## Password Change Contract

Use one authenticated change-password endpoint with a mode derived from server state:

- `must_change_password=true`: current password is optional; authenticated first-login proof is sufficient.
- normal account: current password is required and verified.

The new password must pass the shared policy and differ from the existing password. Storage performs one targeted update that sets the new hash, clears the flag, records `password_changed_at`, and increments `credential_version`. Reset-password performs the same credential mutation after reset-token verification.

Once the database update succeeds, best-effort Redis session cleanup may remove the current sid, but token invalidation relies on credential-version comparison. The response tells the frontend to clear both tokens and return to login. Concurrent/replayed changes either fail old-password/version checks or produce only one authoritative final version.

## Frontend Flow

Extend the user API model with `must_change_password`. `AuthProvider` always fetches `/me` after local/OAuth/OA token establishment. A dedicated public-to-restricted route renders the password form; `ProtectedRoute` redirects restricted users there and prevents return-path loops.

On success, clear tokens across tabs using the existing storage/logout mechanisms and show the normal login page. Mirror deterministic policy checks in a shared frontend helper, but display backend validation responses as authoritative.

## Rollout and Rollback

Roll out schema-compatible reads before any backfill. New-account writers then set the flag, followed by frontend routing and server gates in the same release. Because new fields default safely, rollback consists of disabling new writers/gates and reverting the frontend; existing documents remain readable by older code if extra fields are ignored.

Do not partially deploy the writer without the forced-change UI and allowed endpoint path. A feature/config gate may keep enforcement disabled until backend and frontend artifacts are both present, but tests must cover the enabled production state.

## Security and Operational Notes

- Never log passwords, zxcvbn inputs, hashes, tokens or OA portal tokens.
- Rate-limit change/reset routes using existing auth patterns.
- Preserve the exact OA `Accesstoken` parsing and URL stripping behavior.
- Credential-version reads must not be bypassed by request or auth caches.
- Redis failure continues to follow the existing fail-closed 503 contract.
