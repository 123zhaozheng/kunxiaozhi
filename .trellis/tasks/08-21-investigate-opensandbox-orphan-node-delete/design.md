# Design: OpenSandbox local-forget and force-remove

## Boundaries

LambChat Mongo is the occupancy source of truth for Admin recovery. OpenSandbox remains the source of truth for live containers, but it is not required to unblock the UI.

Keep fail-closed on the existing happy path:

- `PUT /api/settings/opensandbox-nodes` still rejects healthy in-use node removal (`opensandbox_node_in_use`) and occupied legacy switches (`opensandbox_legacy_mode_in_use`).
- Inventory terminate still tries provider kill first; confirmed 404 still reconciles locally.

Add two operator hatches, both `settings:manage`:

| Action | When | Writes |
|---|---|---|
| Forget local sandbox | Row `unknown`/`creating`, or node health `unknown`/`unavailable` | binding terminal + reservation pull |
| Force-remove node | Node health `unknown`/`unavailable`, or occupancy remains after provider is not a reliable control plane | all node bindings terminal + delete capacity doc + pull node; last node also sets `mode=legacy` |

Do not call OpenSandbox create/connect/kill on either hatch. Best-effort remote kill stays on the existing terminate button.

## Data flow

```text
UI ConfirmDialog
  -> forget-local  /  force-remove
  -> OpenSandboxCapacityStorage admin purge (token taken from stored docs, not a user lease)
  -> node config + revision (force-remove only)
  -> GET nodes/inventory refresh
     used_sandboxes drops; unknown rows gone; mode may be legacy
```

Admin purge uses the binding's own `reservation_id` + `allocation_token`. That still fences a concurrent new allocation: a newer token is not overwritten. Missing token still force-terminates that exact `node_id + sandbox_id` document, because this path exists for broken historical rows.

## Contracts

### Forget local sandbox

`DELETE /api/opensandbox/sandboxes/{node_id}/{sandbox_id}?confirm=true&local_only=true`

- `confirm=true` required (same as terminate).
- `local_only=true` skips adapter entirely.
- Allowed when displayed/provider state would be `unknown` or `creating`, or node `health_state` in `{unknown, unavailable}`.
- Healthy `running`/`paused` without those node health flags returns `409 opensandbox_local_forget_not_allowed` so operators still use real terminate there.
- Success: `{ node_id, sandbox_id, action: "forget_local", state: "terminated", local_only: true }`.
- Default inventory query excludes terminal states unless the caller explicitly filters `state=terminated`.

Reuse the existing terminate route rather than a second verb, so the UI can retry the same delete with `local_only=true` after a 502.

### Force-remove node

`POST /api/settings/opensandbox-nodes/{node_id}/force-remove`

Body: `{ "confirm": true, "expected_revision": "<rev>" }`

- Revision mismatch -> 409 `opensandbox_nodes_revision_conflict`.
- Healthy node with non-terminal occupancy and `health_state=healthy` -> 409 `opensandbox_node_in_use`. Operators must drain via terminate/forget-local first, or the node must be unknown/unavailable.
- Never-probed `unknown` is allowed (production trap); the confirm copy must say the node may actually still be live.
- After occupancy purge: `$pull` the node, `delete_one` capacity `_id=node_id`, bump revision.
- If no managed nodes remain: `mode=legacy`, `nodes=[]` in the stored document. `get_current()` already synthesizes `legacy-default` from scalar settings when mode is legacy.
- Fan-out the existing `OPENSANDBOX_NODES` settings notification and `reset_session_sandbox_manager()`.

### Ordinary save cleanup

`OpenSandboxNodeStorage.save()` after a successful unused-node removal must `delete_one` each `removed_ids` capacity document. Occupancy checks stay. This is required even when nobody uses force-remove: otherwise `x` can resurrect if the same node id is re-added.

`remove()` should use the same non-terminal reservation predicate as `save()`, not `if cap.get("reservations")` (that blocks leftover terminal array entries). Prefer deleting the capacity doc over tightening `remove()`; `save()` is the UI path.

## Logging

`src/api/routes/opensandbox_admin.py` and `src/api/routes/settings.py` must use `get_logger(__name__)`.

| Event | Level | Fields |
|---|---|---|
| Inventory probe exception | WARNING | node_id, sandbox_id, exc type |
| Terminate provider failure | ERROR | node_id, sandbox_id, exc type |
| Local forget | INFO | node_id, sandbox_id, actor, local_only |
| Force-remove | WARNING | node_id, actor, released_count, switched_to_legacy |
| Probe exception | WARNING | node_id, exc type, health_state written |

Never log API keys. Keep UI details as stable codes; `last_error` may keep exception type names.

## Frontend

`OpenSandboxNodesPanel` stays the only Admin surface.

- Unknown/creating rows: keep terminate; add a local-forget confirm that calls delete with `local_only=true`.
- After terminate 502 `opensandbox_provider_action_failed`, show the error and allow the same row's local-forget action (do not auto-forget).
- Occupied or unknown/unavailable nodes: trash becomes force-remove (API), not draft filter. Unused healthy nodes keep draft-remove + save.
- Force-remove confirm copy must mention: local occupancy and `x/10` will be cleared; remote containers may remain until TTL; last node switches to 单节点兼容.
- Refresh nodes + inventory after either hatch; capacity badge reads from the GET nodes payload.

Source-level panel tests in `openSandboxNodesPanel.test.ts` should assert the new API symbols and confirm copy. Backend tests extend `tests/api/test_opensandbox_admin_routes.py` and `tests/infra/sandbox/test_opensandbox_node_storage.py`.

## Compatibility

- Legacy mode behavior unchanged: inventory stays empty; scalar OpenSandbox settings remain the runtime config.
- No change to user-facing capacity-full modal.
- No Daytona/E2B changes.

## Rollback

Revert the two endpoints and panel buttons. Existing fail-closed save path remains. Capacity documents already deleted by a force-remove are not reconstructed; operators would re-add the node and let occupancy start at 0.
