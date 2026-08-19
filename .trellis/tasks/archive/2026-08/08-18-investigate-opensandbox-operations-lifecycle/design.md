# Design: OpenSandbox Multi-Node Scheduling and Administration

## 1. Architecture

LambChat adds a control layer in front of independently deployed OpenSandbox servers:

```text
Admin UI -> node config/inventory API -> encrypted config + capacity ledger
Chat request -> resolve Agent capability -> sandbox admission before durability
             -> existing run/session/message flow only after admission succeeds
Agent node -> reuse admitted user binding -> node-specific OpenSandbox adapter
```

OpenSandbox remains responsible for container runtime and TTL. LambChat owns node configuration, admission capacity, user affinity, lifecycle bookkeeping, Admin actions, and user-facing capacity rejection.

## 2. Node Configuration

Use a dedicated `opensandbox_node_config` Mongo collection with one revisioned `_id="current"` document:

```text
mode: "legacy" | "multi_node"
nodes[]:
  id, domain, encrypted_api_key, image, timeout=3600, work_dir,
  use_server_proxy, max_sandboxes, enabled, priority
revision, updated_at, updated_by
```

- Encrypt each API key with the existing Fernet helper; API responses return only `has_api_key`.
- PUT replaces the list using `expected_revision`; empty secrets preserve existing values and explicit clear removes them.
- Node IDs are immutable. Nodes with bindings/reservations must be disabled/drained before removal.
- `mode=legacy` synthesizes `legacy-default` from existing scalar settings. Multi-node is explicitly activated.
- Reuse the existing settings notification/soft-reset path to invalidate manager configuration across replicas without stopping sandboxes.

Node routes under `/api/settings` require `settings:manage`:

```text
GET  /opensandbox-nodes
PUT  /opensandbox-nodes
POST /opensandbox-nodes/{node_id}/probe
```

## 3. Atomic Capacity Ledger

Use one `opensandbox_node_capacity` document per logical node. Reservation identity and occupancy live in the same document:

```text
_id: node_id
enabled, max_sandboxes
reservations[]:
  reservation_id, user_id, allocation_token, allocation_state,
  sandbox_id?, lease_expires_at?, created_at, updated_at
last_reconcile_at, last_health_at, health_state, last_error
```

Atomic admission uses `find_one_and_update` guarded by `enabled=true` and `size(reservations) < max_sandboxes`, then pushes the reservation. Release uses an idempotent `$pull`. Capacity is derived from the array size, avoiding a counter/reservation split-brain window.

All non-terminal states, including paused and unknown, occupy a slot. Capacity reduction never evicts sandboxes; it marks the node over-capacity and blocks new reservations.

## 4. Distributed Allocation

1. Reuse an existing healthy binding without reserving another slot.
2. For a new/terminal binding, acquire a renewable per-user Redis token lease and re-read the binding.
3. Order eligible nodes by reservation ratio, priority, then stable ID.
4. Atomically reserve the first available node; return a typed unavailable error when none succeeds.
5. CAS the binding to `creating` with node/reservation/allocation tokens.
6. Create through the node-specific adapter with ownership metadata.
7. Finalize only while the allocation token matches, then cache the backend.
8. On failure, compensate by token. Ambiguous provider state retains an `unknown` reservation for conservative reconciliation.

Redis expiry never grants write authority; Mongo allocation tokens are the fencing boundary. Redis or Mongo failure during new allocation fails closed.

## 5. Chat Admission and Capacity UX

Current search/team nodes acquire sandboxes after run/session/message persistence. The new admission boundary must run after request validation and Agent resolution but before durable task, session, user-message, trace-event, or SSE creation.

- Determine sandbox capability from the resolved Agent implementation/metadata, not frontend name matching. Fast Agent bypasses admission.
- This admission boundary applies to Web Chat only in this release. WeCom keeps its existing submission path and is explicitly out of scope.
- Admission is race-safe: it reuses or creates/reserves the user's sandbox. Later graph-node `get_or_create` reuses that durable binding.
- Capacity exhaustion, recorded-node outage, and temporary non-allocation return HTTP 503 with stable machine code `sandbox_capacity_unavailable`; user-visible text is always `沙盒资源暂满，请稍后重试`.
- The frontend preserves status/code in a typed API error. On this code it removes any optimistic chat placeholders, keeps text and attachments, opens one informational modal, and does not emit the generic assistant error.
- The modal includes a `?` help control whose content is `如需协助，可反馈数据资产部赵正通`.
- Normal sandbox creation/reuse remains silent. There is no queue, request persistence, or automatic retry.

## 6. Binding and Lifecycle

Extend `user_sandbox_bindings` additively:

```text
provider, node_id, sandbox_id, reservation_id,
allocation_token, allocation_state, lease_expires_at,
sandbox_state, sandbox_created_at, sandbox_last_used_at
```

- Cache entries include node ID. Reconnect, renew, state and actions resolve the recorded node adapter.
- Legacy bindings probe `legacy-default` then configured nodes under the user lease, backfill `node_id`, and conservatively adopt a reservation.
- Node outage keeps binding/reservation and never creates on another node. Authoritative TTL terminal/not-found releases the old reservation and permits a new allocation.
- Default TTL remains 3600 seconds per node. Successful use and Admin renew set expiry to `now + node.timeout`.
- Docker pause is exposed as “冻结”: it calls provider pause, keeps reservation/files/memory, does not renew TTL, and may interrupt active work. The next user access auto-resumes; Admin can resume manually.
- Terminate requires irreversible confirmation, kills the provider sandbox, marks binding terminal, releases the reservation, and allows a fresh sandbox on the user's next request.
- No busy-state tracking is added for Admin actions.

## 7. Reconciliation and Inventory

Lazy conservative reconciliation runs when a node is full, a creating lease expires, or Admin probes/refreshes:

- guard by a per-node Redis lease;
- iterate durable LambChat bindings/reservations and query each managed sandbox by `(node_id, sandbox_id)`;
- refresh managed state and release only on authoritative terminal/absent results;
- never release after timeouts, auth failures, or incomplete managed-state checks;
- do not discover or display externally created sandboxes because the installed OpenSandbox SDK has no node-wide list API and external creation is out of scope.

Admin inventory routes require `settings:manage`:

```text
GET  /api/opensandbox/sandboxes?skip=&limit=&node_id=&state=&search=
POST /api/opensandbox/sandboxes/{node_id}/{sandbox_id}/pause
POST /api/opensandbox/sandboxes/{node_id}/{sandbox_id}/resume
POST /api/opensandbox/sandboxes/{node_id}/{sandbox_id}/renew
DELETE /api/opensandbox/sandboxes/{node_id}/{sandbox_id}
```

Responses contain managed sandboxes only and expose provider state, binding state, user, timestamps, expiry, node and action availability. Actions update provider, binding and reservation coherently.

## 8. Admin UI

Use a dedicated OpenSandbox settings surface with node configuration and sandbox inventory views:

- responsive unframed table/list, pagination, node/state/search filters;
- node health, used/max capacity, last error and manual probe;
- sandbox user/node/status, created/last-used/expires-at and remaining TTL;
- state-appropriate icon actions for freeze, resume, renew and terminate; tooltips explain freeze does not release memory and TTL continues;
- danger confirmation for terminate and warning that lifecycle actions may interrupt active work;
- immediate affected-row refresh after actions;
- immediate load, then 10-second polling only while the page is visible, plus manual refresh.

All strings use i18n. Secrets never reach frontend state.

## 9. Compatibility, Errors, and Rollback

- Legacy mode keeps the current single-node settings and lifecycle behavior.
- New collections and binding fields are additive.
- Disable/capacity decrease never kills existing sandboxes.
- Switching back to legacy is rejected while non-legacy bindings/reservations exist; drain first.
- Backend logs retain specific causes while users see one capacity message.
- Use project domain exceptions and explicit HTTP mapping; log with `[OpenSandbox]` and never include credentials.
- Rollout keeps multi-node disabled until storage, race, admission and UI tests pass together.

## 10. Deferred Work

- Automatic cross-node failover/migration, snapshots/shared storage, persistent waiting queues, busy-state protection, aggregate Prometheus dashboards, and background leader reconciliation.
