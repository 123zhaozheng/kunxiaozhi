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
