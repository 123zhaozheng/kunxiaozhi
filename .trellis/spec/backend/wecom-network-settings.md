# WeCom Deployment Network Settings

> Executable contracts for administrator-managed WSS/media routing, immediate reload, partial success, and rollback.

## Scenario: Route WeCom through a DMZ without restarting the application

### 1. Scope / Trigger

- Trigger: a deployment must route WeCom long connections and inbound media through a DMZ reverse gateway or HTTP CONNECT forward proxy.
- The network policy is deployment-wide. Persona WeCom documents continue to own only bot credentials and bot behavior.
- The configuration applies only to WeCom traffic; it must not set process-wide proxy environment variables.

### 2. Signatures

```python
# API prefix: /api/settings; permission: settings:manage
GET  /wecom-network
POST /wecom-network/test
PUT  /wecom-network

async def request_wecom_network_reload(
    revision: str,
    *,
    requested_by: str | None = None,
    timeout_seconds: float = 20.0,
) -> list[dict[str, Any]]: ...

class WeComBotManager:
    async def reload_network_config(
        self,
        revision: str,
        *,
        authentication_timeout_seconds: float = 15.0,
    ) -> list[dict[str, Any]]: ...
```

MongoDB uses one document in collection `wecom_network_config`:

```text
_id = "current"
config = WeComNetworkConfig  # forward_proxy_password encrypted at rest
revision = opaque UUID hex
last_known_good = {config, revision, updated_at, updated_by}
```

### 3. Contracts

Request fields:

| Field | Contract |
|---|---|
| `mode` | `direct`, `reverse_gateway`, or `forward_proxy` |
| `websocket_url` | Required WSS URL for reverse gateway; forced to the official URL otherwise |
| `media_gateway_url` | Required HTTPS URL for reverse gateway |
| `forward_proxy_url` | Required HTTP/HTTPS URL for forward proxy |
| `forward_proxy_username` | Optional; credentials must not be embedded in the URL |
| `forward_proxy_password` | Empty or omitted preserves the stored secret |
| `clear_forward_proxy_password` | Explicitly deletes the stored proxy secret |
| `ca_bundle_path` | Optional absolute/runtime-readable CA PEM path |
| `expected_revision` | Optimistic concurrency token from GET |
| timeout and byte-limit fields | Bounded by `WeComNetworkConfig` |

Response fields:

- Secrets are never returned. `has_forward_proxy_password` reports presence.
- `status` is `connected`, `partial_failure`, `rolled_back`, `saved_unverified`, `test_ok`, or `test_failed`.
- `results[]` contains `preset_id`, `aibotid`, `state`, reason fields, and `node_id` for every configured bot owned by a responding runtime.

Environment bootstrap keys are `WECOM_NETWORK_MODE`, `WECOM_WEBSOCKET_URL`,
`WECOM_MEDIA_GATEWAY_URL`, `WECOM_FORWARD_PROXY_URL`,
`WECOM_FORWARD_PROXY_USERNAME`, `WECOM_FORWARD_PROXY_PASSWORD`,
`WECOM_CA_BUNDLE_PATH`, `WECOM_CONNECT_TIMEOUT_SECONDS`,
`WECOM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS`, and `WECOM_MEDIA_MAX_BYTES`.
They are defaults only while MongoDB has no administrator-saved document.

Inbound media uses a project-owned `httpx.AsyncClient` with `trust_env=False`,
TLS verification, timeout, and a streaming size limit. Outbound Agent
`reveal_file` media uses SDK WebSocket chunk upload, so it follows the same WSS
route while retaining the existing one-file and ownership checks.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Reverse mode URL is not `wss` / media URL is not `https` | HTTP 422; do not save |
| Proxy/gateway URL contains credentials | HTTP 422 |
| CA file missing or invalid | Test reports `test_failed`; authenticated reload reports bot failures |
| `expected_revision` is stale, including first-write race | HTTP 409; do not overwrite |
| No configured bots | Save and return `saved_unverified` |
| Every configured bot authenticates | Promote candidate and return `connected` |
| At least one bot authenticates, at least one fails | Keep candidate and return `partial_failure` with failed bots |
| No configured bot authenticates | CAS rollback candidate to the previous config, reload rollback revision, return `rolled_back` |
| Runtime does not respond | Treat as no successful bot; total-failure rollback applies |
| Media exceeds `media_max_bytes` | Abort stream with `media_too_large`; do not upload partial data |

### 5. Good / Base / Bad Cases

- Good: two bots authenticate and one has a bad secret; keep the new DMZ settings and identify the failed bot.
- Good: direct mode explicitly passes `proxy=None` to WebSocket and `trust_env=False` to HTTP so unrelated host proxy variables cannot leak into WeCom behavior.
- Base: no bots exist; store the administrator configuration for later validation.
- Bad: restart FastAPI or the whole pod to apply a WeCom network setting.
- Bad: roll back because one bot has invalid credentials while other bots use the new route successfully.
- Bad: log a full media callback URL or proxy password.

### 6. Tests Required

| Test | Assertion point |
|---|---|
| `tests/kernel/schemas/test_wecom_network.py` | mode URL validation and password preserve/clear semantics |
| `tests/infra/agent/wecom/test_network.py` | scoped proxy options, HTTPS callbacks, reverse media target encoding |
| `tests/infra/agent/wecom/test_network_config.py` | proxy password encryption and legacy plaintext read |
| `tests/infra/agent/wecom/test_runtime_isolation.py` | distributed reload result aggregation |
| `tests/api/test_wecom_network_settings_routes.py` | secret redaction, partial-success keep, total-failure rollback |
| inbound attachment and collector delivery suites | inbound media still materializes; reveal-file remains single-file and ownership-scoped |

### 7. Wrong vs Correct

#### Wrong

```python
os.environ["HTTPS_PROXY"] = admin_value
await restart_application()
if any(result["state"] != "connected" for result in results):
    await rollback()
```

#### Correct

```python
transport = WeComNetworkTransport(candidate)
results = await request_wecom_network_reload(saved.revision)
connected = {item["preset_id"] for item in results if item["state"] == "connected"}
if configured_bots and not connected:
    rollback = await storage.rollback(saved.revision, updated_by=user.sub)
elif len(connected) < len(configured_bots):
    status = "partial_failure"  # keep candidate and show failed bots
```
