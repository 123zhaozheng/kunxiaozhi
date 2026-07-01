# WeCom connection status UI — research snapshot

Persisted from brainstorm session for implement/check agents.

## LambChat stack

- `wecom-aibot-sdk` 1.0.8, `WSClient` in `src/infra/agent/wecom/bot.py`
- `WeComBotManager`: Redis lease `wecom:lease:{aibotid}`, rebalance 20s, `reload_preset` on config change
- `ConnectionState` in-process only today; no status API

## SDK behaviors affecting `reason_code`

| Source | Suggested code |
|--------|----------------|
| `event.disconnected_event` / `on_server_disconnect` | `replaced` |
| `WSReconnectExhaustedError` | `reconnect_exhausted` |
| `WSAuthFailureError` / auth errcode | `auth_failed` |
| Manager lost lease refresh | `lease_lost` |
| Generic `disconnected` / `FAILED` | `disconnected` |

## APIs (PRD)

- List: `has_wecom`
- `GET` status (batch for plaza poll)
- `POST reconnect` → `manager.reload_preset`

## Frontend

- `PersonaPresetCard` only (not Welcome)
- Static badge all users; live dot + reconnect for `channel:manage`