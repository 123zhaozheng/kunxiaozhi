# Idle Login Sessions

## Scenario: Enforcing an administrator-configurable idle login timeout

### 1. Scope / Trigger

Use this contract whenever changing login token issuance, refresh, authenticated dependencies, activity reporting, or long-lived authenticated transports.

The product uses a per-login idle session in addition to normal JWT `exp`. Continuous explicit browser activity may keep a session alive indefinitely; an absolute maximum session lifetime is a separate feature.

### 2. Signatures

```python
# src/infra/auth/session.py
async def create_session(user_id: str) -> str
async def assert_active(sid: str | None, user_id: str) -> dict
async def touch(sid: str | None, user_id: str) -> dict
async def remove(sid: str | None) -> None

# src/infra/auth/jwt.py
async def create_token_pair(user_id: str, username: str) -> tuple[str, str]
def create_access_token(user_id: str, *, sid: str | None = None, ...) -> str
def create_refresh_token(user_id: str, username: str, *, sid: str | None = None, ...) -> str

# src/api/routes/auth/core.py
GET  /api/auth/activity -> LoginActivityResponse
POST /api/auth/activity -> LoginActivityResponse
```

JWT access and refresh tokens issued by one password, OAuth, or OA SSO login carry the same opaque `sid`. Refresh rotation preserves that `sid`; a new login creates a new one.

### 3. Contracts

Setting:

```text
LOGIN_IDLE_TIMEOUT_HOURS
default = 3
minimum = 0.25
maximum = 168
step = 0.25
hot reload = yes
```

Redis state:

```text
key   = auth:login-session:{sid}
value = {user_id, last_activity_at, last_activity_epoch}
ttl   = REFRESH_TOKEN_EXPIRE_DAYS
```

`assert_active` checks only; ordinary API requests, refresh, SSE heartbeats, WebSocket frames, polling, and server push never update activity. Only `POST /api/auth/activity` calls `touch`.

Both Redis operations use Lua so read/check/delete and read/check/write are atomic. Keep the argument contract aligned with the scripts:

```text
assert ARGV = [user_id, now_epoch, timeout_seconds]
touch  ARGV = [user_id, now_epoch, timeout_seconds, now_iso, cleanup_ttl]
```

The assert expiry expression must therefore use `ARGV[2] - last >= ARGV[3]`. Never use a non-atomic Python GET/check/SET sequence: a stale touch could resurrect an expired session.

Authentication ordering is mandatory:

```text
verify JWT -> assert_active(sid, sub) -> auth/request cache -> user/RBAC lookup
```

The public refresh route performs its own idle check before rotating tokens. Established SSE and WebSocket connections re-check at most every 60 seconds even when traffic is continuous; checking never touches activity.

### 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| JWT invalid or expired | HTTP 401 |
| Missing legacy `sid` | HTTP 401; one-time re-login after rollout |
| Redis record missing, expired, or user mismatch | HTTP 401 |
| `now - last_activity >= current timeout` | Atomically delete record, then HTTP 401 |
| Redis unavailable | HTTP 503; do not misreport as idle expiry |
| Activity POST before deadline | Atomically update timestamp and cleanup TTL |
| Activity POST after deadline | Delete/reject; never recreate the session |
| Refresh while active | Rotate both JWTs with the same `sid`; do not touch |
| Refresh after idle expiry | HTTP 401; issue no tokens |
| Setting is bool, NaN, Infinity, below min, or above max | HTTP 400; do not persist |
| SSE/WebSocket idle expiry | End stream / close socket; always run existing cleanup |

Numeric settings with definition-level `minimum` / `maximum` are enforced in `SettingsStorage.set`; Python booleans are explicitly rejected even though `bool` subclasses `int`.

### 5. Good / Base / Bad Cases

- Good: a real pointer or keyboard action causes the browser to POST activity; Redis atomically advances the deadline and every token/cache/transport path observes it.
- Base: a quiet page continues polling and receiving heartbeats; the session still expires after the configured idle duration.
- Bad: refreshing tokens, serving SSE events, or receiving WebSocket heartbeats calls `touch`, allowing an unattended page to remain logged in.
- Bad: checking idle state after `_auth_cache` returns, which creates a stale acceptance window.
- Bad: implementing Lua argument positions in the fake Redis test but not locking the production script expression.

### 6. Tests Required

- Assert password, OAuth, and OA SSO logins create a server session and share one `sid` across each token pair.
- Assert refresh preserves `sid`, succeeds inside the idle window, and issues nothing after expiry.
- Assert dependency request-state and token-cache hits still call `assert_active` first.
- Assert Redis user mismatch, missing key, boundary equality, atomic delete, and atomic touch/no-resurrection behavior.
- Lock the production Lua argument expression (`ARGV[2]` current time, `ARGV[3]` timeout) in a regression test.
- Assert valid setting boundaries succeed and bool/NaN/Infinity/out-of-range values fail before persistence.
- Assert established SSE/WebSocket connections re-check during both quiet and continuously active transport traffic; heartbeats do not touch.
- Assert Redis failure maps to 503 and not 401.

### 7. Wrong vs Correct

```python
# Wrong: refresh or every authenticated request silently counts as human activity.
payload = verify_token(token)
await touch(payload.sid, payload.sub)
return cached_user

# Correct: every auth path checks before cache shortcuts; only the activity API touches.
payload = verify_token(token)
await assert_active(payload.sid, payload.sub)
return cached_user
```

```lua
-- Wrong: ARGV[1] is user_id, not the current timestamp.
if tonumber(ARGV[1]) - last >= tonumber(ARGV[2]) then

-- Correct: assert args are [user_id, now_epoch, timeout_seconds].
if tonumber(ARGV[2]) - last >= tonumber(ARGV[3]) then
```

