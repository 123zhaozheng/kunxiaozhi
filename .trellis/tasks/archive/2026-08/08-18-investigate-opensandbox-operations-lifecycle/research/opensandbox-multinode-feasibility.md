# Research: OpenSandbox Multi-Node Feasibility

- Query: Design a capacity-bounded, multi-node OpenSandbox routing model for stateless LambChat replicas while preserving user sandbox reuse and lifecycle behavior.
- Scope: mixed (repository plus installed SDK source; no live server contacted)
- Date: 2026-08-18

## Verdict

This is feasible with a LambChat-side scheduler and durable reservation state. It is **not safe** to implement by adding a list of adapters to the current manager alone: the current user lock is an `asyncio.Lock` per process, and the current Mongo write occurs only after provider creation. Two LambChat replicas can therefore create two sandboxes for one user and/or exceed a node limit.

Recommended MVP: keep OpenSandbox as the provider, add a node-list configuration and a small Mongo reservation/capacity ledger, use a Redis per-user lease for the create critical section, persist `(node_id, sandbox_id)` in each user binding, and route all reconnect/stop calls through the recorded node. Treat a reservation as occupied from create attempt until provider sandbox termination (`kill`), including `paused`, because pause preserves the sandbox and its files and may still count against a node's configured sandbox limit. Reconcile the ledger against each node's `/sandboxes` list and expose drift/health metrics, but do not use a live count as the allocation decision.

Automatic migration of a live sandbox from an unhealthy node is **not** recommended for MVP: OpenSandbox IDs and filesystem state are node-local, and there is no SDK operation that moves a sandbox between servers. The default failover policy should preserve the binding and report unavailable; an explicitly user-approved `recreate_on_node_failure` policy may create a new sandbox with documented data loss.

## Existing Configuration and Admin Surface

### Current shape and persistence

* `Settings` has one scalar OpenSandbox endpoint (`OPENSANDBOX_DOMAIN`, API key, image, timeout, work directory, and proxy flag) at [src/kernel/config/base.py:252](../../../../src/kernel/config/base.py#L252).
* Definitions are individual settings in the `sandbox/opensandbox` subgroup. Domain, image, work directory, and proxy are frontend-visible; API key is sensitive; all depend on `SANDBOX_PLATFORM=opensandbox` ([src/kernel/config/definitions.py:555](../../../../src/kernel/config/definitions.py#L555)).
* `SettingsStorage.set` validates only the declared top-level type, numeric bounds, boolean type, and for JSON that the value is a list/dict; it does not validate a nested node schema ([src/infra/settings/storage.py:156](../../../../src/infra/settings/storage.py#L156)). Values are written as one `system_settings` document keyed by setting name ([src/infra/settings/storage.py:196](../../../../src/infra/settings/storage.py#L196)).
* The settings API exposes grouped values and generic `PUT /api/settings/{key}`; there is no provider-specific transaction/test endpoint ([src/api/routes/settings.py:33](../../../../src/api/routes/settings.py#L33), [src/api/routes/settings.py:181](../../../../src/api/routes/settings.py#L181)).
* `SettingsPanel` renders JSON without `json_schema` as a large free-form textarea ([frontend/src/components/panels/SettingsPanel.tsx:925](../../../../frontend/src/components/panels/SettingsPanel.tsx#L925)). A JSON node array would therefore be editable today, but with no structure, URL validation, capacity validation, or nested secret masking.

### Hot reload and compatibility implications

* Every current OpenSandbox scalar is in `_SANDBOX_AFFECTED_SETTINGS`; a change resets the process singleton without stopping sandboxes or deleting bindings ([src/kernel/config/service.py:45](../../../../src/kernel/config/service.py#L45), [src/kernel/config/service.py:86](../../../../src/kernel/config/service.py#L86)). The reset is called for both single-key and full refresh ([src/kernel/config/service.py:331](../../../../src/kernel/config/service.py#L331), [src/kernel/config/service.py:360](../../../../src/kernel/config/service.py#L360)).
* `SettingsService.set` refreshes the local settings object and publishes the key over the existing Redis settings channel ([src/infra/settings/service.py:117](../../../../src/infra/settings/service.py#L117), [src/infra/settings/service.py:270](../../../../src/infra/settings/service.py#L270)); subscribers call `refresh_settings(key)` on every other replica ([src/infra/settings/pubsub.py:63](../../../../src/infra/settings/pubsub.py#L63)).
* A node list must be treated as one atomic versioned setting. Add `OPENSANDBOX_NODES` to the affected set so all replicas drop their node scheduler/cache after a save. Do not add a separate pub/sub channel.

### Minimal compatible configuration model

For MVP, add a JSON setting named `OPENSANDBOX_NODES` (default `[]`, category `sandbox`, subcategory `opensandbox`, frontend-visible with a custom editor) with this shape:

```json
[
  {
    "id": "osb-a",
    "domain": "http://opensandbox-a:8090",
    "api_key": "...",
    "image": "ubuntu",
    "timeout": 3600,
    "work_dir": "/root",
    "use_server_proxy": true,
    "max_sandboxes": 20,
    "enabled": true,
    "priority": 0
  }
]
```

`id` is an administrator-chosen immutable logical identity, not the URL and not a provider sandbox ID. `max_sandboxes` must be a positive integer; duplicate IDs, blank domains, non-positive timeouts, and duplicate endpoints should be rejected. Keep all per-node defaults aligned with the existing scalar defaults. `priority` is optional for deterministic tie-breaking.

The generic JSON setting is the smallest shape compatible with the current settings system, but nested `api_key` values cannot be safely masked by existing `SettingStorage` (masking is whole-setting, not per JSON field). The secure MVP should therefore add a provider-specific admin GET/PUT endpoint/editor that returns `api_key` as `has_api_key` plus an opaque masked value and supports write-only replacement. Alternatively, use `api_key_env` references and resolve them from process environment; that reduces database secret exposure but makes node secrets unavailable to an admin-only database deployment. Do not mark the whole JSON value sensitive, because the existing panel would then return `********` and make ordinary editing impossible.

Legacy compatibility:

1. If `OPENSANDBOX_NODES` is empty, synthesize one node `legacy-default` from the existing scalar settings. This preserves current deployments and requires no data migration.
2. If the list is non-empty, node entries are authoritative for new creates. Keep the scalar fields readable/writable for one release, but show them as legacy and reject a conflicting platform flip only after all existing bindings are accounted for.
3. A legacy binding with only `sandbox_id` has no node identity. Reconnect by trying the configured nodes (legacy-default first, then enabled nodes) until one returns the sandbox; on success persist `node_id`. Bound IDs must never be interpreted as globally unique because they may be node-local.

## Current Lifecycle and Data Flow

`SessionSandboxManager` creates one adapter based on `settings.SANDBOX_PLATFORM` ([src/infra/sandbox/session_manager.py:349](../../../../src/infra/sandbox/session_manager.py#L349)). The OpenSandbox path is:

1. Process-local cache lookup keyed only by `user_id`.
2. Mongo `user_sandbox_bindings.find_one({user_id})` and read `sandbox_id`.
3. `SandboxSync.connect(sandbox_id)` through the one configured endpoint; if connected, renew timeout, build a backend, save state, and cache it ([src/infra/sandbox/session_manager.py:998](../../../../src/infra/sandbox/session_manager.py#L998)).
4. If no binding/reconnect fails, call `SandboxSync.create`, save the binding, cache the provider object, and build MCP tools ([src/infra/sandbox/session_manager.py:1068](../../../../src/infra/sandbox/session_manager.py#L1068)).
5. Explicit `stop` calls adapter `pause()` (fallback `kill()` on pause failure), removes the local cache, and saves `paused` ([src/infra/sandbox/session_manager.py:1123](../../../../src/infra/sandbox/session_manager.py#L1123)). `close_all` invokes this stop path at shutdown ([src/infra/sandbox/session_manager.py:1152](../../../../src/infra/sandbox/session_manager.py#L1152)).

The current binding has a unique `user_id` index only ([src/infra/sandbox/session_manager.py:379](../../../../src/infra/sandbox/session_manager.py#L379)) and writes `sandbox_id`, `sandbox_state`, last-used, and created timestamps with an upsert ([src/infra/sandbox/session_manager.py:460](../../../../src/infra/sandbox/session_manager.py#L460)). Existing tests assert cache-hit, reconnect, create, and pause semantics but use in-process fakes ([tests/infra/test_session_sandbox_manager.py:134](../../../../tests/infra/test_session_sandbox_manager.py#L134)).

Required binding extension:

```text
user_sandbox_bindings (unique user_id)
  user_id
  provider: "opensandbox"          # optional for legacy documents
  node_id: "osb-a"                 # new; nullable during migration
  sandbox_id: "node-local-id"      # retained field for compatibility
  sandbox_state: creating|running|paused|stopping|terminal|unknown
  reservation_id: UUID              # new, for capacity reconciliation
  allocation_token: UUID            # new, fencing token for create owner
  sandbox_created_at
  sandbox_last_used_at
  lease_expires_at                  # only while creating/recovering
```

The process cache tuple must become `(node_id, sandbox_id, backend, provider_obj)` (or a named record). Reconnect must construct an adapter from the recorded node, not current global scalar settings. `stop`, timeout renewal, state reads, MCP keying, and logs should include node ID. Existing `sandbox_id` consumers can continue to receive the node-local ID; observability must use the pair.

Recommended indexes are the existing unique `user_id`, a compound `{node_id: 1, sandbox_id: 1}` (unique when both are present if the product guarantees one binding per sandbox), and `{allocation_state: 1, lease_expires_at: 1}` for stale lease recovery. A unique `node_id+sandbox_id` index should be added only after checking for historical duplicates; it is not required for correctness of user uniqueness.

## Capacity Allocation Across Stateless Replicas

### Why provider list counts alone are insufficient

The installed SDK can list sandboxes via `SandboxManagerSync.list_sandbox_infos(SandboxFilter(...))`; the underlying endpoint is `GET /sandboxes` and returns paginated `SandboxInfo` with ID, state, metadata, image, expiry, and platform. It can also fetch per-sandbox metrics (`SandboxSync.get_metrics`). This supports health/reconciliation, but a read-count-then-create sequence races across replicas and cannot reserve a slot atomically. List results can also lag provider state and include sandboxes not owned by LambChat unless metadata is filtered.

### Recommended reservation ledger

Use a Mongo collection such as `opensandbox_node_capacity` with one document per logical node:

```text
{ _id: node_id, enabled, max_sandboxes, active_count,
  config_revision, last_health_at, last_reconcile_at }
```

The scheduler selects an enabled node and atomically reserves a slot with `find_one_and_update` using the filter `{enabled: true, active_count: {$lt: max_sandboxes}}` and `$inc: {active_count: 1}`. The returned document is the reservation. Mongo's single-document update is the oversell guard; do not read `active_count` and increment separately. Store a reservation ID and owner token in the user's binding (or a small `opensandbox_reservations` document) so a failed create can release exactly once.

Use the existing Redis primitives for a short per-user distributed mutex: `src/infra/task/concurrency.py` acquires a token with `SET NX EX` and releases it with a value-checking Lua script ([src/infra/task/concurrency.py:189](../../../../src/infra/task/concurrency.py#L189)); the memory lock has the same pattern ([src/infra/memory/distributed.py:69](../../../../src/infra/memory/distributed.py#L69)). A stable key such as `sandbox:create:{user_id}` prevents two replicas from replacing one user's binding concurrently. The lock TTL must exceed provider create readiness timeout, or be renewed; always fence the Mongo update with `allocation_token` so an expired lock holder cannot overwrite a newer binding.

### Allocation sequence

1. Read the user binding. If it has a node and non-terminal sandbox, reconnect there first. A healthy `running` or `paused` sandbox consumes a slot; renew/resume and return it without a new reservation.
2. Acquire the Redis per-user create lease. Re-read the binding after acquiring it (the other replica may have completed creation).
3. Select a node from the current validated config. Use weighted least-utilization (`active_count / max_sandboxes`) with stable priority and round-robin tie-break, skipping disabled/unhealthy/full nodes. Reserve one slot atomically in `opensandbox_node_capacity`; if all reservations fail, return a clear capacity error rather than creating untracked sandboxes.
4. Atomically mark the user binding `allocation_state=creating`, `reservation_id`, `allocation_token`, target `node_id`, and `lease_expires_at`, with a conditional filter that the token is absent/expired. A Mongo transaction can combine this with the node increment if the deployment guarantees a replica set; otherwise use an idempotent compensating sequence and a sweeper.
5. Create through the node-specific `OpenSandboxSandboxAdapter` using its `ConnectionConfigSync` (the current adapter passes `domain`, API key, and `use_server_proxy` at [src/infra/sandbox/session_manager.py:260](../../../../src/infra/sandbox/session_manager.py#L260)). Pass user metadata as today; add a stable `lambchat_user_id` and `lambchat_binding_token` metadata to enable reconciliation.
6. On successful create, conditionally update the binding where `allocation_token` matches with `(sandbox_id, node_id, sandbox_state=running, allocation_state=active)`, clear the lease, and leave the reservation occupied. Build/cache the backend only after this durable update; then release the Redis lease.
7. On provider create failure, conditionally mark the reservation released and decrement `active_count` exactly once. If cleanup/kill fails, leave the reservation in `cleanup_pending` and retry; do not silently reuse the slot. On binding-write failure after provider success, kill the orphan and release the slot, matching current cleanup behavior ([src/infra/sandbox/session_manager.py:1090](../../../../src/infra/sandbox/session_manager.py#L1090)).

### Slot state policy

MVP should count `creating`, `running`, `paused`, `stopping`, and `unknown` non-terminal sandboxes. Release only after `kill`/provider deletion is confirmed or the node reconciliation proves the ID is terminal/absent. Counting paused sandboxes is conservative and preserves the existing pause-for-reuse semantics; otherwise a paused fleet can exceed the administrator's stated maximum when many users resume simultaneously. A later setting may choose `paused_does_not_consume`, but it requires provider-specific evidence and stronger resume reservations.

The reconciler periodically lists each node with metadata filter `lambchat_binding_token` (or enumerates all pages), compares provider states to reservations, repairs counters, and emits drift. It must never release a reservation solely because the list call timed out. Stale `creating` leases are reclaimable only after the lease expires and a provider lookup/metadata search confirms no successful sandbox; if uncertain, keep the slot and alert.

## Node Selection, Health, and Configuration Changes

Health is per node, not global. A cheap periodic `list_sandboxes` or `get_sandbox_info` probe establishes control-plane reachability; a create/readiness probe should be optional because it consumes capacity. Mark a node `healthy`, `degraded`, or `unreachable` with `last_success_at`, error class, and latency. Selection skips unreachable nodes for new creates but always attempts the recorded node for reconnect. Expose `active_count`, configured max, reservations, provider-observed count, drift, create failures, reconnect failures, and latency by node ID (never API key).

Config semantics:

* **Add/enable:** validate first, create/reset the capacity document at count zero, publish the normal settings key, and allow selection after health succeeds (or allow optimistic selection with a short timeout).
* **Disable:** stop new allocations immediately after pub/sub convergence; continue reconnect/stop/renew for existing bindings. Do not kill existing sandboxes.
* **Remove:** reject removal while bindings/reservations exist unless the admin explicitly chooses `drain`; mark disabled and drain first. Hard removal must retain a tombstone/node credentials long enough to stop or reconcile existing bindings. Removing an endpoint from config otherwise makes its node-local sandboxes unreconnectable.
* **Capacity increase:** atomically update `max_sandboxes`; no migration is needed.
* **Capacity decrease:** do not evict sandboxes. New reservations stop when `active_count >= new_max`; show an over-capacity/draining state until natural kill/TTL reduces count.
* **Endpoint/credential/image change:** treat as a node revision. Existing bindings continue to use the old endpoint revision for reconnect if possible; new creates use the new revision. If retaining old credentials is unacceptable, mark the node draining and require an explicit data-loss decision.

## Failure Matrix and Failover Recommendation

| Failure | MVP behavior | Data impact |
|---|---|---|
| Node health probe timeout | Mark degraded/unreachable; skip new allocations; retry recorded bindings with backoff | None if node returns |
| Reconnect to recorded node fails | Keep binding and reservation; return unavailable after bounded retries; alert | None; files remain on node if it recovers |
| Provider create fails before ID | Release reservation; retry another healthy node only if no binding was previously active | None |
| Provider create succeeds, binding update fails | Kill orphan; release reservation; retryable error | New sandbox discarded; existing binding untouched |
| Redis lock unavailable | Do not create; return transient error or use Mongo fencing-only fallback if enabled | None |
| Mongo reservation update fails | Do not create; no slot consumed | None |
| Replica crashes during `creating` | Lease expires; reconciler checks provider metadata before release/reattach | At worst an orphan, cleaned or adopted by token |
| Explicit user stop | Call pause (fallback kill), preserve binding and reservation as current semantics | Pause preserves files; kill loses them |
| TTL expiry/provider auto-termination | Reconnect fails; mark terminal and release slot; next access creates according to policy | Files lost if provider TTL deleted them |
| Node removed while bindings exist | Keep node tombstone/draining; no automatic migration | Preserves data only while old node remains reachable |
| Node permanently lost | Default: surface unavailable and require admin/user decision; optional recreate creates on another node | Recreate loses old filesystem unless external snapshot/volume exists |

Do not silently fail over an existing binding by creating a new sandbox. OpenSandbox's SDK exposes `connect`, `resume`, `pause`, `kill`, `renew`, list, and metrics, but no cross-server transfer or snapshot import in the LambChat adapter. A same-user new sandbox changes the user's working files and can invalidate in-flight sessions. A later opt-in policy can recreate and include a `sandbox_recreated_from_node` event, but it must be presented as data loss.

## OpenSandbox SDK Feasibility (Installed `opensandbox==0.1.14`)

Ground-truth local introspection (no server calls) found:

* `SandboxSync.create(image, *, timeout=timedelta, env, metadata, connection_config, ...)`, `connect(sandbox_id, connection_config, ...)`, and `resume(sandbox_id, connection_config, ...)` are available. The current adapter correctly uses `env` and `timedelta` ([src/infra/sandbox/session_manager.py:259](../../../../src/infra/sandbox/session_manager.py#L259)).
* `SandboxSync.id` is a string on the client object; the SDK has no server-global identity namespace. Therefore `(node_id, sandbox_id)` is the durable identity.
* `get_info()` returns ID, status, expiry, image, platform, metadata, and extensions; `get_metrics()` returns per-sandbox usage. `SandboxManagerSync.list_sandbox_infos(SandboxFilter)` uses paginated `GET /sandboxes`, with state and metadata filters. This makes reconciliation and admin health possible, but not an atomic capacity reservation.
* `pause`, `kill`, `renew(timedelta)`, and `is_healthy` exist. The current adapter maps `renew`, calls `is_healthy`, and pauses on explicit stop ([src/infra/sandbox/session_manager.py:296](../../../../src/infra/sandbox/session_manager.py#L296), [src/infra/sandbox/session_manager.py:321](../../../../src/infra/sandbox/session_manager.py#L321)).
* `ConnectionConfigSync(use_server_proxy=...)` is required in both adapter and factory paths ([src/infra/sandbox/session_manager.py:260](../../../../src/infra/sandbox/session_manager.py#L260), [src/infra/sandbox/base.py:219](../../../../src/infra/sandbox/base.py#L219)); each node must carry its own proxy flag.
* `SandboxInfo.metadata` is suitable for an ownership token and node-independent reconciliation. Metadata should not contain secrets.

Installed source references (read locally): `opensandbox/sync/sandbox.py`, `opensandbox/sync/manager.py`, `opensandbox/sync/adapters/sandboxes_adapter.py`, and `opensandbox/models/sandboxes.py` under the Python 3.13 site-packages directory; package version is `0.1.14`. No live server was contacted, so server version/feature flags and actual pause resource accounting remain unresolved.

## Admin UX/API Impact

MVP needs more than a raw JSON textarea:

* A repeatable node editor with ID, endpoint, secret replacement, image, timeout/work directory, proxy toggle, enabled toggle, and max count; show health, observed count, reserved count, and last error read-only.
* Validate all nodes as one document and save atomically. Return a revision and per-node validation errors. Preserve the existing `SettingUpdateResponse` shape for scalar settings; add a provider-specific response for node operations rather than exposing nested keys through generic masking.
* Add explicit actions for `disable/drain`, `remove after drain`, and `recreate on failure`; defaults must not silently destroy data.
* Keep current scalar fields and UI visible during migration as “legacy default node”; do not change `SANDBOX_PLATFORM` options or the existing single-node path until the node list is selected.

## Tests Required

### MVP tests

* Settings: default/migration from scalar fields; node schema validation; duplicate IDs/endpoints; secret redaction; hot reload reset and Redis fan-out for `OPENSANDBOX_NODES`.
* Mongo: unique user binding; conditional allocation-token updates; node reservation `$inc` refuses `active_count >= max`; release is idempotent; stale lease reconciliation; capacity decrease/drain behavior.
* Distributed races: two manager instances and two users contending for one slot; same user on two replicas creates exactly one provider sandbox; expired lock holder cannot overwrite a newer binding; create failure releases the slot.
* Lifecycle: reconnect uses recorded node even when the current default endpoint differs; legacy binding probes nodes and backfills `node_id`; pause retains reservation; kill/terminal releases it; stop fallback kill is persisted.
* Provider fakes: no real OpenSandbox calls; verify each adapter's `ConnectionConfigSync` has node-specific domain/key/proxy; list pagination and metadata ownership filtering; health timeout classification.
* API/UI: node editor round-trip, masked secret replacement, disable/drain confirmation, over-capacity display, and clear unavailable-vs-recreated messaging.

### Later enhancements

* Provider metrics/Prometheus integration and automatic capacity reconciliation with a durable outbox.
* Weighted capacities (CPU/memory rather than sandbox count), per-tenant quotas, and admission priority.
* Snapshot/volume based migration if the deployed OpenSandbox server supports portable snapshots; this requires an explicit data-copy and consistency protocol.
* A background orphan reaper and repair dashboard, plus controlled canary creates for degraded nodes.

## Unresolved User Decisions

1. Should paused sandboxes consume a node slot? The recommendation is yes for correctness and current pause/reuse semantics; it lowers effective capacity.
2. On permanent node loss, should the product preserve the binding and fail closed (recommended), or opt into automatic recreate with explicit filesystem data loss?
3. Are API keys allowed in the database, or must nodes reference environment/secret-manager keys? This determines whether a custom settings endpoint is mandatory.
4. Does the OpenSandbox deployment guarantee a Mongo replica set (transactions available) and provide a reliable node-level server metric for maximum concurrent sandboxes? If not, use the reservation ledger plus reconciliation and accept temporary conservative over-reservation.
5. Is `max_sandboxes` an all-state object limit or only running containers? The proposed default is all non-terminal states.

## Caveats / Not Found

* No OpenSandbox server was contacted; server version, actual node-local ID collision behavior, pause resource accounting, and list consistency are not verified.
* The repository has no existing sandbox reservation collection, distributed sandbox lock, or provider-specific Admin node API. Existing per-process cache/lock and post-create binding are insufficient for multi-replica capacity guarantees.
* The current generic settings API cannot validate nested JSON or mask nested secrets. Any implementation that only adds a JSON definition without a custom editor/API would be operationally risky.
