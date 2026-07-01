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
async def publish_wecom_status(preset_id: str, payload: dict[str, Any]) -> None: ...
async def read_wecom_status(preset_id: str) -> WeComConnectionStatus | None: ...
async def resolve_wecom_status_for_preset(preset_id: str) -> WeComConnectionStatus: ...
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
| Redis `wecom:status:{preset_id}` | JSON | TTL 7d; written on bot state change |
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
| Batch id without wecom config | entry with `reason_detail: wecom_not_configured` |
| Reconnect without `channel:manage` | HTTP 403 |
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