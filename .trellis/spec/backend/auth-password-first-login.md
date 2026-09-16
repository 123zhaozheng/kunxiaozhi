# Strong Password and First-Login Enforcement

## Scenario: Credential changes and mandatory first-login password setup

### 1. Scope / Trigger

Use this contract whenever changing user creation, password validation/hash/update/reset, JWT creation/refresh, authenticated dependencies/caches, OA/OAuth login, or authenticated SSE/WebSocket handling.

The backend is authoritative. A frontend redirect alone must never grant a `must_change_password` user access to normal business routes.

### 2. Signatures

```python
# src/infra/auth/password_policy.py
def validate_password(
    password: str,
    *,
    username: str | None = None,
    email: str | None = None,
    current_password: str | None = None,
) -> None: ...

# src/api/deps.py
async def get_current_user_base(...) -> TokenPayload: ...
async def get_current_user_required(...) -> TokenPayload: ...
async def credential_version_is_current(payload: TokenPayload) -> bool: ...

# src/infra/user/storage.py
async def change_password(..., expected_version: int | None = None, ...) -> UserInDB | None: ...
async def reset_password(user_id: str, new_password: str, reset_token: str) -> UserInDB | None: ...
```

```text
POST /api/auth/change-password
Body: {"old_password": string|null, "new_password": string}
Response: {"message": string, "must_change_password": false}

GET /api/auth/me -> User including must_change_password, credential_version,
                    password_changed_at
```

Persisted/token fields:

```text
User.must_change_password: bool = false for missing legacy fields
User.credential_version: int = 0 for missing legacy fields
User.password_changed_at: datetime | null
TokenPayload.credential_version: int = 0 for legacy tokens
```

### 3. Contracts

- Human-selected passwords are 8-64 Unicode characters, at most 72 UTF-8 bytes, and contain uppercase, lowercase, digit, and non-whitespace symbol characters.
- Reject control characters, leading/trailing whitespace, account identifiers of at least three characters, the current password, and offline `zxcvbn` score 0. Hash the original value; normalization is comparison-only.
- Generated OA/OAuth secrets may bypass human policy only through the explicit internal `generated_password` path. Every new local/admin/OAuth/OA account persists `must_change_password=true`.
- Missing fields on legacy users resolve to `must_change_password=false`, `credential_version=0`. Do not lock existing users during rollout.
- Authentication ordering is `JWT -> idle session -> current user -> credential_version -> must_change_password -> RBAC/business`.
- `get_current_user_base` permits restricted users only for `/me`, GET/POST activity, refresh, and change-password. Normal routes use `get_current_user_required`, which returns HTTP 403 with `detail.code = PASSWORD_CHANGE_REQUIRED`.
- First-login change does not require the generated/temporary old password. Ordinary change requires and verifies `old_password`.
- Password change/reset atomically writes the hash, clears the flag/token, records `password_changed_at`, and increments `credential_version`. Reset queries must include token and expiry so concurrent/expired replay fails atomically.
- Refresh, auth caches, existing SSE, and WebSocket connections compare the persisted credential version. Long-lived transports repeat both idle and credential-version checks at most every 60 seconds and fail closed on lookup failure.
- Successful change/reset invalidates old access/refresh tokens. Never log passwords, hashes, reset tokens, OA tokens, or strength inputs.

### 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Weak/identifier/control/>72-byte password | HTTP 400; no hash or credential mutation |
| Restricted user calls normal HTTP route | HTTP 403, `PASSWORD_CHANGE_REQUIRED` |
| Restricted user opens business SSE/WebSocket | Reject handshake/request |
| Token version differs from persisted user | HTTP 401 / close established transport |
| First-login change with no old password | Allowed when authenticated and flag is true |
| Ordinary change missing/wrong old password | HTTP 400 |
| Concurrent credential change/version race | HTTP 409; no overwrite |
| Expired/replayed reset token | Reject; no credential mutation |
| Legacy user/token with missing version fields | Treat version as 0 |
| Redis unavailable during idle check | HTTP 503; do not misreport as password state |

### 5. Good / Base / Bad Cases

- Good: an OA-created user receives a limited token, opens `/auth/change-password`, sets a strong password, is logged out, and logs in again with version 1.
- Base: a legacy version-0 user continues to use the application until a password change increments the version.
- Bad: trusting a JWT `must_change_password` claim, accepting an auth-cache hit without reloading the current version, or checking established streams only when an event arrives.

### 6. Tests Required

- Password policy: 8/64 characters, 72/73 UTF-8 bytes, Unicode casing, controls/whitespace, all four character classes, identifiers, zxcvbn, and legacy bcrypt truncation compatibility.
- Account channels: local/admin/OAuth/OA new users set the flag; generated-secret bypass cannot be used by caller-supplied passwords.
- Dependencies/refresh: request-state and token-cache paths still check version/flag; 403 is distinct from 401/503.
- Change/reset: old-password rules, atomic expected-version update, reset expiry/replay, flag clear, version increment, and token invalidation.
- Realtime: quiet and active SSE/WebSocket connections recheck idle and credential version within 60 seconds and clean up pending tasks.

### 7. Wrong vs Correct

```python
# Wrong: cache acceptance bypasses reset/change revocation.
cached = _get_cached_user(token)
if cached:
    return cached

# Correct: compare against current persisted credential state.
cached = _get_cached_user(token)
user = await UserStorage().get_by_id(cached.sub)
if user and user.credential_version == cached.credential_version:
    return cached
```

```python
# Wrong: reset-token expiry is checked before a token-only update.
await collection.update_one({"reset_token": token}, update)

# Correct: token and expiry participate in the same atomic mutation.
await collection.find_one_and_update(
    {"reset_token": token, "reset_token_expires": {"$gt": now}},
    update,
)
```
