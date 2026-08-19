# Research: OpenSandbox Multi-Node Implementation Map

- Query: Map the approved fail-closed OpenSandbox multi-node MVP to existing LambChat implementation patterns.
- Scope: internal repository research
- Date: 2026-08-19

## Recommended Reuse Points

### Secrets and Admin configuration

Use the WeCom network configuration path as the closest provider-specific Admin CRUD precedent:

* Pydantic internal/update/response separation, `extra="forbid"`, optional write-only secret, `has_*` response flag, and `expected_revision` optimistic concurrency are in [src/kernel/schemas/wecom_network.py:42](../../../../src/kernel/schemas/wecom_network.py#L42) and [src/kernel/schemas/wecom_network.py:101](../../../../src/kernel/schemas/wecom_network.py#L101).
* The storage layer encrypts only the secret field before Mongo persistence, keeps a revision and last-known-good payload, and conditionally updates `{_id, revision}` to reject stale writers ([src/infra/agent/wecom/network_config.py:78](../../../../src/infra/agent/wecom/network_config.py#L78), [src/infra/agent/wecom/network_config.py:131](../../../../src/infra/agent/wecom/network_config.py#L131)).
* Routes use `require_permissions("settings:manage")`, return redacted responses, and distinguish test/save operations ([src/api/routes/settings.py:45](../../../../src/api/routes/settings.py#L45), [src/api/routes/settings.py:95](../../../../src/api/routes/settings.py#L95)). Existing `/api/settings` router registration in [src/api/main.py:710](../../../../src/api/main.py#L710) means adding node endpoints to `src/api/routes/settings.py` needs no new app registration.
* Frontend `WeComNetworkSettings` is a self-contained settings panel with load/form state, action state, revision preservation, masked password replacement, and status feedback ([frontend/src/components/panels/WeComNetworkSettings.tsx:23](../../../../frontend/src/components/panels/WeComNetworkSettings.tsx#L23), [frontend/src/components/panels/WeComNetworkSettings.tsx:74](../../../../frontend/src/components/panels/WeComNetworkSettings.tsx#L74)). Reuse this interaction model for node save/validation/health actions.

Use the field-level encryption helper from model/MCP storage, not plaintext nested JSON:

* `encrypt_value`/`decrypt_value` use a Fernet marker and preserve legacy plaintext reads ([src/infra/mcp/encryption.py:87](../../../../src/infra/mcp/encryption.py#L87), [src/infra/mcp/encryption.py:125](../../../../src/infra/mcp/encryption.py#L125)).
* `ModelStorage` encrypts `api_key`, decrypts only for internal use, detects encrypted values, and has a bounded plaintext migration ([src/infra/agent/model_storage.py:75](../../../../src/infra/agent/model_storage.py#L75), [src/infra/agent/model_storage.py:106](../../../../src/infra/agent/model_storage.py#L106)). Apply the same per-node `api_key` treatment in a dedicated OpenSandbox node storage document; do not put raw keys in generic `OPENSANDBOX_NODES` JSON returned by `SettingsStorage`.
* Response masking in model routes is explicit (`mask_api_key`) ([src/kernel/schemas/model.py:167](../../../../src/kernel/schemas/model.py#L167), [src/api/routes/agent/model.py:49](../../../../src/api/routes/agent/model.py#L49)). For nodes, return `has_api_key` and never return decrypted keys; accept an empty secret as “preserve” and an explicit `clear_api_key` as delete.

The generic `JsonSchemaEditor` already supports repeatable arrays, password fields, number/toggle/select controls, add/remove, and layout widths ([frontend/src/components/panels/JsonSchemaEditor.tsx:106](../../../../frontend/src/components/panels/JsonSchemaEditor.tsx#L106), [frontend/src/components/panels/JsonSchemaEditor.tsx:145](../../../../frontend/src/components/panels/JsonSchemaEditor.tsx#L145)). It is suitable for non-secret node metadata or a first UI shell, but it cannot preserve masked secrets or show live health/capacity rows. A dedicated `OpenSandboxNodesPanel.tsx` should use the same classes and `ConfirmDialog` for disable/drain/remove confirmations.

### Mongo atomicity, indexes, and compensation

There is no project-wide Mongo transaction helper and no observed `start_transaction`/`with_transaction` use. Assume deployments may not provide a replica-set transaction. Build correctness from single-document atomic operations plus idempotent compensation:

* `find_one_and_update` with `$inc` is the established atomic-counter pattern ([src/infra/session/trace_storage.py:392](../../../../src/infra/session/trace_storage.py#L392)); use it for node `active_count` with a filter `enabled=true, active_count < max_sandboxes` and return the updated reservation document.
* `Settings`/WeCom storage use conditional `update_one` and inspect `matched_count`/`acknowledged` to detect stale writers ([src/infra/agent/wecom/network_config.py:150](../../../../src/infra/agent/wecom/network_config.py#L150)). Use the same token filter for binding finalization and reservation release.
* Index initialization is lazy and generally idempotent. Sandbox currently creates a unique `user_id` index only ([src/infra/sandbox/session_manager.py:379](../../../../src/infra/sandbox/session_manager.py#L379)); add an awaited `ensure_indexes()` for capacity/binding indexes rather than relying on a background task during the create race.
* Existing collection access is direct Motor through a domain class (`ModelStorage`, WeCom storage). Keep all node/reservation operations in a dedicated `OpenSandboxNodeStorage`/`OpenSandboxCapacityStorage`; do not add raw collection calls to routes or the manager.

Suggested documents:

```text
opensandbox_nodes: _id=node_id, config (domain/image/timeout/work_dir/proxy), encrypted_api_key,
  enabled, max_sandboxes, revision, updated_at/by, health fields, drain state
opensandbox_node_capacity: _id=node_id, enabled, max_sandboxes, active_count, reconcile timestamps
user_sandbox_bindings: existing unique user_id plus provider, node_id, sandbox_id,
  reservation_id, allocation_token, allocation_state, lease_expires_at
```

The node config and capacity docs may be combined if updates are carefully scoped, but separate capacity counters make `$inc` and reconciliation easier. Reservations should have a unique reservation ID and terminal/released state so compensation can be retried safely. Add indexes for `{node_id, sandbox_id}` and `{allocation_state, lease_expires_at}`; retain the current unique `user_id` index. A unique `(node_id, sandbox_id)` index should be introduced only after duplicate historical data is checked.

### Redis lock, lease, and fencing

Reuse the token lock implementation in `src/infra/tool/mcp_global.py` rather than copying an ad hoc `SETNX` helper:

* `acquire_distributed_lock` generates a UUID token and performs atomic `SET NX EX` ([src/infra/tool/mcp_global.py:227](../../../../src/infra/tool/mcp_global.py#L227)).
* `release_distributed_lock` checks token ownership in Lua; `renew_distributed_lock` extends TTL only for the owner ([src/infra/tool/mcp_global.py:254](../../../../src/infra/tool/mcp_global.py#L254), [src/infra/tool/mcp_global.py:288](../../../../src/infra/tool/mcp_global.py#L288)).
* `_renew_lock_until_stopped` supplies the renewal task pattern for long provider calls ([src/infra/tool/mcp_global.py:301](../../../../src/infra/tool/mcp_global.py#L301)).

The task concurrency module has a shorter per-user lock (`SET NX EX`, polling, value-checked Lua release) at [src/infra/task/concurrency.py:189](../../../../src/infra/task/concurrency.py#L189), but its five-second TTL is too short for OpenSandbox readiness. Add a sandbox-specific key/TTL using the MCP helper or extract a neutral shared helper. Keep a Mongo `allocation_token`/fencing token in the binding: Redis expiry alone must never authorize an old provider-create result to overwrite a newer binding. On lock loss, stop work and compensate; do not release another worker's lock.

## Exact Affected Existing Files

### Backend settings and schemas

* `src/kernel/config/base.py`: add a compatibility `OPENSANDBOX_NODES` setting only if config remains settings-backed; prefer a pointer/revision setting when node secrets live in `opensandbox_nodes`.
* `src/kernel/config/definitions.py`: define metadata/default/visibility or mark legacy scalar fields; do not expose encrypted node documents through generic `SettingItem`.
* `src/kernel/config/service.py`: add node-config key(s) to `_SANDBOX_AFFECTED_SETTINGS` so every replica soft-resets the scheduler after a revision change ([src/kernel/config/service.py:45](../../../../src/kernel/config/service.py#L45)). Existing settings pub/sub fan-out is sufficient.
* `src/kernel/schemas/opensandbox.py` (new): strict node create/update/response, masked secret fields, revision, health/capacity summaries, drain action payloads. Use Pydantic URL/positive integer validators and `extra="forbid"` like WeCom.

### Storage, scheduler, lifecycle

* `src/infra/sandbox/session_manager.py`: replace one global OpenSandbox adapter with a node registry/scheduler; cache `(node_id, sandbox_id, backend, provider_obj)`; use recorded node for reconnect/stop/renew; preserve pause and user binding behavior; acquire distributed user lock before any new create.
* `src/infra/sandbox/node_storage.py` (new): encrypted node CRUD, revision checks, legacy scalar-to-`legacy-default` conversion, redacted responses, and node tombstone/drain semantics.
* `src/infra/sandbox/capacity_storage.py` (new or combined with node storage): atomic reserve/release, idempotent reservation status transitions, stale lease scans, index initialization, and reconciliation updates.
* `src/infra/sandbox/node_scheduler.py` (new): validate snapshot, choose least-utilized healthy enabled node with deterministic tie-break, and expose health/capacity state. Keep provider SDK calls in adapters and route them through `run_blocking_io` as required by [sandbox provider spec](../../../../.trellis/spec/backend/sandbox-providers.md).
* `src/infra/sandbox/session_manager.py` or `src/infra/sandbox/node_reconciler.py` (new): background reconciliation should use `SandboxManagerSync.list_sandbox_infos` per node with metadata ownership filter; classify unknown/timeouts conservatively and never release on a failed list call.
* `src/infra/sandbox/__init__.py`: export any storage/scheduler reset or lifecycle hooks needed by config hot reload. `reset_session_sandbox_manager` already exists at [src/infra/sandbox/session_manager.py:1182](../../../../src/infra/sandbox/session_manager.py#L1182).
* `src/infra/backend/opensandbox.py`: likely only add node context to logs/metrics; keep backend provider methods unchanged unless the adapter needs a node-aware display ID.
* `src/infra/sandbox/base.py`: audit `SandboxFactory` callers. Its process-local registry and `close_sandbox` cannot be the capacity ledger; any factory create path must receive a node-specific config or be explicitly excluded from user-bound scheduler allocation ([src/infra/sandbox/base.py:211](../../../../src/infra/sandbox/base.py#L211)).

### Admin API and frontend

* `src/api/routes/settings.py`: add `GET /opensandbox-nodes`, `PUT /opensandbox-nodes` (revision-checked full replacement), optional `POST /opensandbox-nodes/{id}/probe`, and explicit drain/disable action. Reuse `require_permissions("settings:manage")`, HTTP 409 for revision conflict, and redacted response models from WeCom routes ([src/api/routes/settings.py:95](../../../../src/api/routes/settings.py#L95)).
* `src/infra/agent/wecom/network_config.py` is a pattern only; do not place OpenSandbox node secrets in the WeCom collection. New storage should live under `src/infra/sandbox/` and use `settings.MONGODB_DB`.
* `frontend/src/services/api/settings.ts`: add typed node list/update/probe methods next to existing WeCom methods ([frontend/src/services/api/settings.ts:76](../../../../frontend/src/services/api/settings.ts#L76)).
* `frontend/src/types/settings.ts`: add `OpenSandboxNode`, masked secret, revision, health/capacity, update, and operation response types ([frontend/src/types/settings.ts:108](../../../../frontend/src/types/settings.ts#L108)).
* `frontend/src/components/panels/OpenSandboxNodesPanel.tsx` (new): repeatable table/cards, add/edit node modal or inline form, password replacement/clear, capacity and health display, and drain confirmation. Mount from `SettingsPanel` when the platform is OpenSandbox; the generic settings list remains the legacy scalar editor.
* `frontend/src/components/panels/SettingsPanel.tsx`: route OpenSandbox category rendering to the dedicated panel and avoid rendering encrypted node payload as generic JSON. Existing category/subcategory grouping and custom WeCom branch provide the insertion point ([frontend/src/components/panels/SettingsPanel.tsx:676](../../../../frontend/src/components/panels/SettingsPanel.tsx#L676)).
* i18n locale files (`frontend/src/i18n/locales/*.json`): add node labels, validation errors, health states, drain/data-loss warnings. Existing settings panel uses `t(setting.description)` and localized subcategory labels ([frontend/src/components/panels/SettingsPanel.tsx:173](../../../../frontend/src/components/panels/SettingsPanel.tsx#L173)).

### New background/runtime wiring

* `src/infra/sandbox/node_reconciler.py` (new) and runtime startup/shutdown wiring in `src/infra/runtime_services.py` or the application lifespan: periodic per-node list/reconcile, stale reservation cleanup, and graceful cancellation. Verify existing runtime service ownership before adding a task; do not create a second scheduler per process.
* `src/infra/sandbox/observability.py` (new or existing logging hooks): counters/histograms for reserve/create/reconnect/release, node health, active-vs-observed drift, and fail-closed errors. Never log API keys or full endpoint credentials.

## Test Files and Commands

### Backend tests to add or extend

* `tests/infra/sandbox/test_opensandbox_node_storage.py`: encryption/decryption, masked response, secret preserve/clear, revision conflict, legacy scalar conversion, indexes, drain/remove rules.
* `tests/infra/sandbox/test_opensandbox_capacity.py`: atomic `$inc` reservation at max, release idempotency, stale lease handling, over-capacity decrease, reconciler drift and timeout behavior.
* `tests/infra/test_session_sandbox_manager.py`: extend current OpenSandbox fakes for recorded-node reconnect, cache tuple node ID, distributed lock/fencing, duplicate create race, pause slot retention, failure compensation, and fail-closed node loss. Existing tests start at [tests/infra/test_session_sandbox_manager.py:134](../../../../tests/infra/test_session_sandbox_manager.py#L134).
* `tests/infra/test_opensandbox_proxy_config.py` and `tests/infra/test_sandbox_factory.py`: verify each node creates a node-specific `ConnectionConfigSync` and factory behavior does not bypass scheduler capacity ([tests/infra/test_opensandbox_proxy_config.py:1](../../../../tests/infra/test_opensandbox_proxy_config.py#L1)).
* `tests/api/test_opensandbox_node_settings_routes.py` (new): FastAPI `ASGITransport` pattern from [tests/api/test_wecom_network_settings_routes.py:92](../../../../tests/api/test_wecom_network_settings_routes.py#L92), admin permission, masked key, revision 409, validation, probe status, drain/remove restrictions.
* `tests/kernel/config/test_sandbox_setting_refresh.py`: add the node-config key to affected-set coverage and assert reset; existing test structure is at [tests/kernel/config/test_sandbox_setting_refresh.py:30](../../../../tests/kernel/config/test_sandbox_setting_refresh.py#L30).
* `tests/infra/backend/test_opensandbox_backend.py`: keep existing SDK wrapper tests green; no live service calls.

### Frontend tests to add

* `frontend/src/components/panels/__tests__/openSandboxNodesPanel.test.ts`: source/layout regression for repeatable rows, password input, capacity/health columns, drain confirmation, and no raw API key rendering. Existing source/layout tests use Node's `node:test` and `readFileSync` ([frontend/src/components/panels/__tests__/usersPanelFormLayout.test.ts:1](../../../../frontend/src/components/panels/__tests__/usersPanelFormLayout.test.ts#L1)).
* `frontend/src/services/api/__tests__/settings.test.ts`: mock `fetch`, assert endpoint/method/body and revision field. Existing API tests use `globalThis.fetch` replacement and restore it in `finally` ([frontend/src/services/api/__tests__/role.test.ts:1](../../../../frontend/src/services/api/__tests__/role.test.ts#L1)).
* Extend `jsonSchemaEditorLayout.test.ts` only if the generic editor remains used for non-secret node metadata; otherwise add a dedicated panel test and keep the node payload out of generic JSON.

Recommended commands from repository tooling:

```text
uv run pytest tests/infra/test_session_sandbox_manager.py tests/infra/test_sandbox_factory.py tests/infra/test_opensandbox_proxy_config.py tests/api/test_opensandbox_node_settings_routes.py tests/kernel/config/test_sandbox_setting_refresh.py
uv run pytest tests/infra/sandbox
uv run ruff check src/infra/sandbox src/api/routes/settings.py src/kernel/schemas/opensandbox.py tests
uv run mypy src/infra/sandbox src/api/routes/settings.py src/kernel/schemas/opensandbox.py
cd frontend && pnpm exec eslint src/components/panels/OpenSandboxNodesPanel.tsx src/services/api/settings.ts
cd frontend && pnpm run build
```

The project-wide gates remain `uv run pytest`, `uv run ruff check .`, `uv run mypy src/`, and `cd frontend && pnpm run build`; they are exposed as `make test`, `make lint`, `make typecheck`, and `make check-all` ([Makefile:150](../../../../Makefile#L150)).

## Implementation Risks and Guardrails

* **Secret leakage:** generic settings admin mode returns actual values when `mask_sensitive=False`, and nested JSON has no field-level masking ([src/infra/settings/storage.py:35](../../../../src/infra/settings/storage.py#L35)). Store encrypted keys in a dedicated collection and return only `has_api_key`; add tests asserting serialized responses contain no secret.
* **No Mongo transaction precedent:** do not assume transactions. Use single-document atomic reservation and conditional token updates; compensation must be retry-safe. If transactions are later required, gate them on deployment capability and retain the non-transactional recovery path.
* **Redis lock expiry:** OpenSandbox readiness can exceed the five-second task lock. Use token renewal and Mongo fencing; never rely on an in-process `asyncio.Lock` for replica safety.
* **Legacy binding ambiguity:** current `user_sandbox_bindings` stores only `sandbox_id` and has one user index ([src/infra/sandbox/session_manager.py:442](../../../../src/infra/sandbox/session_manager.py#L442)). Probe configured nodes on first reconnect and persist `node_id`; fail closed if no node can prove ownership rather than creating a replacement silently.
* **Factory bypass:** `SandboxFactory._sandbox_registry` is process-local. User lifecycle allocation must enter through the scheduler; factory direct callers need an explicit compatibility path and must not be counted as managed capacity.
* **Configuration races:** use WeCom-style revision checks for full node-list replacement and publish the existing settings key for soft reset. Disable/drain/remove must be monotonic and must not kill existing bindings.
* **Provider list uncertainty:** a failed or stale OpenSandbox list cannot justify releasing reservations. Reconciliation should be conservative, metadata-filtered, paginated, and observable.
* **Fail-closed UX:** node outage should report unavailable and retain binding. Any future recreate action must be explicit, auditable, and labeled as filesystem data loss; no automatic migration belongs in MVP.
* **Frontend scope drift:** a generic JSON textarea is cheaper but cannot safely handle write-only secrets, health, revision conflicts, or drain confirmation. Keep the dedicated node panel small and reuse WeCom/modal/form primitives.

## Caveats / Not Found

No existing OpenSandbox node storage, reservation ledger, distributed sandbox lock, or background reconciler exists. No repository Mongo transaction usage was found. The installed SDK and existing tests are mockable, but this artifact does not validate a live server's list consistency, pause resource accounting, or deployment-specific Mongo replica-set support.
