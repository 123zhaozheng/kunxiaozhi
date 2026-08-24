# Research: production unknown-node delete trap

- Query: Why Admin cannot delete unknown OpenSandbox nodes/sandboxes, why `x/10` occupancy stays, and why multi-node cannot switch back to legacy.
- Scope: internal
- Date: 2026-08-21

## Problem restated

Admin needs to recover from a multi-node incident where some hosted entries show `unknown`, OpenSandbox is not a reliable control plane, and local Mongo occupancy must be cleared so the cluster can return to single-node mode.

## Causal chain

```text
node/sandbox unreachable
  -> list/probe/connect raises (not a confirmed 404)
  -> UI shows unknown; terminate still calls OpenSandbox
  -> kill/connect fails -> HTTP 502
  -> Mongo binding + reservation stay non-terminal
  -> used_sandboxes (x in x/10) stays
  -> save() refuses node removal (opensandbox_node_in_use)
  -> save(legacy) refuses mode switch (opensandbox_legacy_mode_in_use)
```

This is the 08-18 fail-closed contract working as designed, not a random UI glitch.

## Unknown sources

| Surface | When it becomes `unknown` | Evidence |
|---|---|---|
| Node health badge | Default / never probed. Capacity upsert inserts `health_state: "unknown"`. Probe success writes `healthy`; probe failure writes `unavailable` (not unknown). | `node_storage.py:210`, `capacity_storage.py:54`, `settings.py:88-104` |
| Inventory row state | `get_sandbox()` raises anything other than confirmed 404. Bare `except Exception` sets `provider_state = "unknown"` and does not log. | `opensandbox_admin.py:152-185`, `session_manager.py:307-320` |
| Binding default | `sandbox_state` missing -> `"unknown"`. Missing `allocation_state` also counts as occupying capacity. | `opensandbox_admin.py:148`, `node_storage.py:120-121` |

Important split: a node card saying 未知 often means “never probed”, not “OpenSandbox returned unknown”. An inventory row saying 未知 means “this refresh could not talk to the node”.

## Delete paths today

### Inventory terminate (calls OpenSandbox)

`OpenSandboxNodesPanel.runAction("terminate")` -> `DELETE /api/.../sandboxes/{node}/{sandbox}?confirm=true`.

`_action()` always talks to the adapter first:

- `connect` / `get_sandbox_unchecked` / `kill`
- only a provider 404 reconciles Mongo
- any other exception becomes `opensandbox_provider_action_failed` (502) and Mongo is unchanged

Terminate is allowed for `unknown` in `_ACTION_STATES`, so the button can appear, then fail.

### Node remove (local draft only)

`removeNode()` only filters the React draft. Persistence is `PUT /opensandbox-nodes`. `save()` then:

1. Blocks if removed node has non-terminal reservations or bindings (`opensandbox_node_in_use`)
2. Upserts capacity for remaining nodes
3. Does **not** `delete_one` the removed node's capacity document

There is no Admin API that deletes Mongo occupancy without a successful provider kill.

### Mode switch

`MULTI_NODE -> LEGACY` is blocked if **any** existing node still has non-terminal reservations or bindings (`opensandbox_legacy_mode_in_use`). Combined with `multi_node` requiring at least one node, a single stuck unknown occupancy freezes the whole mode toggle.

## Capacity `x/10`

`used_sandboxes` is counted from `opensandbox_node_capacity.reservations` where `allocation_state` is not in `{released, terminated, destroyed, not_found}`. Default `unknown` occupies a slot.

`release()` / `release_binding()` exist and are used after a confirmed 404 or successful kill. They are not reachable from the UI when OpenSandbox is down.

`ensure_node()` pulls terminal reservations out of the array, but that only helps after a successful release.

## Why logs were missing

| Location | What happens on failure |
|---|---|
| Inventory probe `opensandbox_admin.py:184-185` | `except Exception: provider_state = "unknown"` — no logger |
| Node probe capacity write `settings.py:110-112` | `except Exception: pass` |
| Node probe error detail `settings.py:91-92` | stores `type(exc).__name__` only |
| Provider action `opensandbox_admin.py:293-294` | HTTP 502 with a stable code; original exception is chained but UI never sees it |

This matches the production report: special multi-node cases produced unknown rows and almost no actionable log line.

## Previous research gap

`08-18-investigate-opensandbox-operations-lifecycle` PRD:

- R1: do not hard-delete a node while bindings/reservations exist
- R3 / R4: provider query failure must not release slots; fail-closed, keep reservation
- Out of scope: live OpenSandbox failure drills

Those rules prevent over-allocation. They do not provide an operator recovery hatch. Production now needs an explicit, confirmed local-forget path that the previous task treated as forbidden.

## Product decision

2026-08-21: option **A**.

- Unknown sandbox rows get an explicit local-forget hatch (no OpenSandbox required).
- Unreachable / unknown nodes get cascade force-remove: bindings + capacity ledger + node config; last node switches to `legacy`.
- Healthy in-use nodes stay fail-closed.
- Remote containers may remain until OpenSandbox TTL.

