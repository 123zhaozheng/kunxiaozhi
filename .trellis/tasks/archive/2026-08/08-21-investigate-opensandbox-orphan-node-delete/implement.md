# Implementation plan

## Order

1. Capacity storage: add admin purge helpers that terminate bindings by stored `node_id` / `sandbox_id` / `reservation_id` / `allocation_token`, pull reservations, and delete the capacity document. Do not require a live user lease.
2. Node storage: after unused-node `save()`, delete capacity docs for `removed_ids`. Align in-use checks on non-terminal states only.
3. Admin terminate route: honor `local_only=true`; skip adapter; log; return `forget_local`. Default inventory excludes terminal rows unless `state` is an explicit terminal filter.
4. Settings route: `POST /opensandbox-nodes/{node_id}/force-remove` with confirm + expected_revision; last remaining node writes `mode=legacy`; publish settings fan-out.
5. Log inventory probe failures and provider action failures with `get_logger(__name__)`; stop swallowing `Exception` silently.
6. Frontend API wrappers + `OpenSandboxNodesPanel` confirms for local forget and force-remove; refresh occupancy after success.
7. Tests as listed below. Do not talk to a real OpenSandbox.

## Validation

```bash
python -m pytest tests/api/test_opensandbox_admin_routes.py tests/infra/sandbox/test_opensandbox_node_storage.py tests/infra/sandbox/test_opensandbox_capacity_storage.py -q
python -m ruff check src/api/routes/opensandbox_admin.py src/api/routes/settings.py src/infra/sandbox/node_storage.py src/infra/sandbox/capacity_storage.py
python -m mypy src/api/routes/opensandbox_admin.py src/api/routes/settings.py src/infra/sandbox/node_storage.py src/infra/sandbox/capacity_storage.py
```

Frontend (package script used by this repo for panel tests):

```bash
node --import tsx --test frontend/src/components/panels/__tests__/openSandboxNodesPanel.test.ts
```

If `test_opensandbox_capacity_storage.py` does not exist, add focused tests next to `test_opensandbox_node_storage.py` instead of creating an unused module.

## Tests to add

- Terminate without `local_only` still 502s when adapter raises connection/timeout; Mongo occupancy unchanged.
- Terminate with `local_only=true` on unknown row succeeds; binding terminal; reservation gone; used count 0.
- `local_only=true` on healthy running node with healthy health_state returns 409.
- Inventory default list omits terminated/released rows.
- Probe exception is logged (caplog) and row state is unknown.
- Force-remove unknown node with occupancy: node gone, capacity doc gone, bindings terminal, revision bumped.
- Force-remove last node: stored mode is `legacy`.
- Force-remove healthy in-use node: 409 `opensandbox_node_in_use`.
- Ordinary save that removes an unused node also deletes its capacity document.
- Occupancy cleared then `save(mode=legacy)` succeeds.
- Frontend source test matches `local_only` / `force-remove` and the orphan-TTL confirm wording.

## Risky files

- `src/api/routes/opensandbox_admin.py` — terminate currently assumes provider success before bookkeeping.
- `src/infra/sandbox/node_storage.py` — save/legacy switch occupancy gates; last-node legacy write must not leave empty `multi_node`.
- `src/infra/sandbox/capacity_storage.py` — token fencing; admin purge must not clobber a newer allocation_token.
- `frontend/src/components/panels/OpenSandboxNodesPanel.tsx` — current trash is draft-only; mixing it with immediate force-remove is the main UX footgun.

## Rollback points

- Land storage helpers + tests first; routes second; UI last.
- If UI ships without `local_only`, operators remain stuck; do not ship the button until the route exists.
- Force-remove of the last node is irreversible in config (mode becomes legacy); keep revision checks.

## Follow-up before `task.py start`

- Planning summary reviewed and explicitly approved in a later user message.
- `implement.jsonl` / `check.jsonl` contain real spec + research entries.
