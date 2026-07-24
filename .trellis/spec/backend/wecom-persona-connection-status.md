# Persona WeCom Connection Status (Plaza UI)

> Executable contracts for Redis-backed WS health, API, and plaza card UX.

---

## Scenario: Plaza WeCom badge vs admin live status

### 1. Scope / Trigger

- Global persona with `persona_wecom_config` (non-empty `aibotid`) is bound to one AI Bot WebSocket (`wecom-aibot-sdk`).
- **List** exposes `has_wecom` only (no secrets). **Live** state requires `channel:manage`.
- UI surface: `PersonaPlaza` / `PersonaPresetCard` only — not Welcome.

### 2. Signatures

```python
# src/infra/agent/wecom/status.py
async def write_wecom_status(
    preset_id: str,
    *,
    state: ConnectionState,
    reason_code: WeComStatusReasonCode | str | None = None,
    reason_detail: str | None = None,
    node_id: str | None = None,
    aibotid: str | None = None,
    network_revision: str | None = None,
) -> None: ...
async def read_wecom_status(preset_id: str) -> WeComConnectionStatus | None: ...
async def resolve_wecom_status(preset_id: str, *, has_wecom: bool) -> dict[str, Any]: ...
```

```python
# src/kernel/schemas/wecom_status.py
class WeComConnectionStatus(BaseModel):
    preset_id: str
    state: Literal["connected", "connecting", "reconnecting", "disconnected", "failed"]
    reason_code: Literal["replaced", "reconnect_exhausted", "auth_failed", "lease_lost", "disconnected"] | None
    reason_detail: str | None
    updated_at: datetime | None
    node_id: str | None
    aibotid: str | None
    network_revision: str | None
```

```python
# Routes (prefix /api/persona-presets), all status/reconnect need channel:manage
GET  /{preset_id}/wecom/status
POST /{preset_id}/wecom/reconnect  # -> reload_preset(preset_id)
POST /wecom/status  # body: {"preset_ids": list[str]}  # max 200, returns {"statuses": list[WeComConnectionStatus]}
```

### 3. Contracts

| Key / field | Type | Notes |
|-------------|------|--------|
| Redis `wecom:status:{preset_id}` | JSON | TTL 7d for diagnostics; `connected` is fresh for only 60s |
| `PersonaPreset.has_wecom` | `bool` | `true` iff global preset has wecom config with `aibotid` |
| Poll interval (frontend) | 15s | While plaza mounted; ids with `has_wecom` only |
| `reason_code` | enum | Maps SDK/manager events (see research `07-01-persona/research/wecom-status-ui.md`) |

**Wrong vs correct (batch status):**

- Wrong: `GET /wecom/status?preset_ids=...` with `statuses` as object map.
- Correct: `POST /wecom/status` with `{"preset_ids": [...]}` and `statuses` as **array**; client builds `Record<preset_id, status>`.

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| No Redis row, `has_wecom` true | `state: disconnected`, `reason_code: null` |
| Stored `connected` older than 60s or invalid `updated_at` | resolve as `disconnected`, `reason_detail: status_stale` |
| Batch id without wecom config | entry with `reason_detail: wecom_not_configured` |
| Reconnect without `channel:manage` | HTTP 403 |
| Reconnect API node is not the preferred owner | HTTP 503; never report false success |
| `disconnected_event` (new connection elsewhere) | `reason_code: replaced`; SDK stops auto-reconnect |

### 5. Good / Base / Bad Cases

- **Good**: Bot publishes `connected` on `authenticated`; manager publishes `lease_lost` after failed lease refresh.
- **Base**: Admin polls POST batch; static「已接企微」for all users when `has_wecom`.
- **Bad**: Exposing `secret` or raw Redis on list API.
- **Bad**: Polling status for users without `channel:manage`.

### 6. Tests Required

| Test | Assertion |
|------|-----------|
| `tests/infra/agent/wecom/test_status.py` | reason mapping, Redis round-trip |
| `tests/api/test_persona_wecom_status_routes.py` | permissions, batch POST, reconnect calls manager |
| `frontend/.../wecomConnectionPresentation.test.ts` | tone/reconnect visibility per state |

### 7. Wrong vs Correct

#### Wrong

```typescript
// Batch poll — GET query string (backend is POST)
authFetch(`/persona-presets/wecom/status?preset_ids=${ids.join(",")}`)
```

#### Correct

```typescript
authFetch(`/persona-presets/wecom/status`, {
  method: "POST",
  body: JSON.stringify({ preset_ids: ids }),
})
// Map response.statuses[] -> Record by preset_id
```

---

## Scenario: WeCom userid → LambChat user_id (session & Web parity)

### Contracts

- Inbound: WeCom `sender_id` / single-chat `chat_id` = enterprise **userid** (e.g. `10325`).
- Runtime owner: `UserStorage.get_by_username(sender_id).id` → Mongo **user id** (e.g. `6a2a…`); used for `submit`, `cancel`, projects, `move_to_project`.
- Redis `wecom:session:v2:{aibotid}:{chat_type}:{chat_id}` → bot- and chat-type-scoped custom `session_id`.

### On each normal message (before `submit`)

1. `_reconcile_wecom_session_owner` — only if `session.user_id == wecom_userid`, migrate to `mapped_user_id` (`set_user_id_if_matches`).
2. `_reconcile_wecom_channel_project` — migrate or create `type=channel` project under `mapped_user_id` (preset name).
3. `_bind_wecom_session_to_project` — `move_to_project` when owner already `mapped_user_id`.

### Wrong vs Correct

#### Wrong

Using `sender_id` as `user_id` in `submit` while Web lists sessions for `User.id`.

#### Correct

Map once per message; legacy sessions with `user_id=10325` are migrated on next WeCom message.

### Edge cases

- No `users.username == sender_id` → send a visible binding error and stop; never persist the raw WeCom userid as a Mongo owner id.
- `session.user_id` neither wecom userid nor mapped id → reconcile skips (manual DB fix).
- Duplicate channel projects (old on `10325`, new on mapped user) → prefer mapped user's project; old project's sessions may need rebinding if `project_id` pointed at old id.

---

## Scenario: Isolate the WeCom transport from Web chat

### 1. Scope / Trigger

- Trigger: enabling Persona WeCom bots in a deployment that also serves Web HTTP/SSE/WebSocket traffic.
- Production uses a separate WeCom runtime process so SDK handshakes, reconnect loops, and callbacks do not share the FastAPI event loop.
- `embedded` remains a compatibility mode; it is not a hard fault-isolation boundary.

### 2. Signatures

```python
# src/infra/agent/wecom/mode.py
def get_wecom_runtime_mode(value: object | None = None) -> Literal[
    "embedded", "external", "disabled"
]: ...

# src/infra/agent/wecom/control.py
async def request_wecom_reload(
    preset_id: str,
    *,
    requested_by: str | None = None,
    wait_for_result: bool = True,
    timeout_seconds: float = 5.0,
) -> bool: ...

# Process entry point
# WECOM_RUNTIME_MODE=external python -m src.infra.agent.wecom.runtime
```

### 3. Contracts

| Contract | Required behavior |
|----------|-------------------|
| `WECOM_RUNTIME_MODE=embedded` | FastAPI starts WeCom in a background task; API readiness never awaits the initial handshake |
| `WECOM_RUNTIME_MODE=external` | FastAPI does not import/start the SDK; a separate process owns `WeComBotManager` |
| `WECOM_RUNTIME_MODE=disabled` | No bot starts and reconnect requests fail closed |
| Redis control channel | `wecom:control`, action `reload_preset`, unique `command_id` |
| Redis result key | `wecom:control:result:{command_id}`, short TTL, only the owning runtime writes `status=ok` |
| Connected state | Only an SDK `authenticated` event may set `CONNECTED`; `connect()` returning is not authentication |
| Shutdown | `WeComBot.stop()` awaits SDK `disconnect()` and drains/cancels project-owned status tasks |

The SDK may catch an opening-handshake timeout internally, schedule another attempt, and return from
`connect()`. Therefore `connect()` completion means only that the SDK supervisor was started.

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Invalid runtime mode | Log a warning and fall back to `embedded` for backward compatibility |
| External runtime has no Redis subscriber | Reconnect returns HTTP 503; never report success |
| Redis publish/read fails or owner ACK times out | Return HTTP 503; Web chat remains available |
| Non-owner runtime receives a broadcast command | It may reconcile ownership but must not win the ACK result race |
| Initial SDK handshake fails but retry is scheduled | State is `reconnecting`, not `connected` |
| FastAPI shuts down in embedded mode | Cancel startup task, then await bot disconnect/cleanup |

### 5. Good / Base / Bad Cases

- **Good**: API and WeCom runtime run as separate containers/processes with shared MongoDB and Redis; killing the WeCom runtime does not stop Web chat.
- **Base**: local development uses `embedded`; a slow handshake yields to the event loop and does not delay API readiness.
- **Bad**: treating `await client.connect()` as proof of authentication.
- **Bad**: calling the async SDK `disconnect()` without `await`.
- **Bad**: importing `setup_wecom_handler()` from FastAPI while mode is `external`.

### 6. Tests Required

| Test | Assertion point |
|------|-----------------|
| `tests/infra/agent/wecom/test_bot_lifecycle.py` | failed initial handshake is not connected; disconnect is awaited; status tasks drain |
| `tests/infra/agent/wecom/test_runtime_isolation.py` | mode normalization, external ACK/timeout/failure containment, runtime lifecycle |
| `tests/api/test_startup_warmups.py` | embedded startup is non-blocking; external mode never starts the SDK |
| `tests/api/test_persona_wecom_status_routes.py` | reconnect crosses the control boundary and maps a missing owner to HTTP 503 |

### 7. Wrong vs Correct

#### Wrong

```python
await client.connect()
self._set_connection_state(ConnectionState.CONNECTED)
client.disconnect()
```

#### Correct

```python
self._ws_client = client
await client.connect()
if not client.is_connected:
    self._set_connection_state(ConnectionState.RECONNECTING)

# The authenticated callback is the only path to CONNECTED.
await client.disconnect()
```
