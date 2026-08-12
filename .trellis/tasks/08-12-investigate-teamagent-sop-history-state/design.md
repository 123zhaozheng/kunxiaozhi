# Design: TeamAgent SOP History State Restoration

## Root Cause

History hydration currently performs two incompatible operations:

1. It selects and normalizes the latest full SOP replay snapshot.
2. It finds a same-plan `approval_required` event and passes that event through `reduceSop` again.

`approval_required.data.plan` is a full immutable snapshot captured when the plan awaited confirmation. Because `reduceSop` is a full-replacement reducer, step 2 overwrites a later completed plan and its succeeded steps with the earlier pending snapshot. Live streaming does not perform this second replay pass and therefore remains correct.

## State Contract

- For a complete history result, the latest normalized SOP replay event is the source of truth for the plan fields and every step field.
- A matching approval envelope is auxiliary metadata. It may attach `approval_id` and any already-supported expiry metadata, but it must not replace plan status, steps, outputs, errors, dependencies, or ownership.
- Approval metadata is attached only when its explicit/nested plan identifier matches the selected plan.
- `reduceSop` remains the live full-snapshot reducer. Its behavior is not weakened or special-cased for history.
- The backend `approval_required` and `sop:updated` payload schemas remain unchanged.

## Frontend Design

Extract the SOP-specific history block from `useAgent.loadHistory` into a small pure restoration helper colocated with the existing history loading helpers.

The helper will:

1. Order/filter SOP replay events using the existing behavior unless adopting the shared canonical comparator is proven to be a no-risk local reuse.
2. Normalize the latest eligible full snapshot with `reduceSop(null, event)`.
3. Locate the latest approval event for the selected `plan_id`.
4. Return a copy of the selected snapshot with approval envelope metadata attached directly.
5. Never call `reduceSop(restoredPlan, approvalEvent)`.

`useAgent.loadHistory` calls the helper and stores its result. Live event handlers and the DAG renderer are unchanged.

## Compatibility

- Existing historical approval events retain their nested plan payload; no migration is needed.
- Histories containing only an approval event still render that event's normalized plan because it is itself an eligible full replay snapshot.
- Histories with a later completed `sop:updated` retain completion while still carrying the matching approval identifier.
- Different-plan approvals remain isolated.
- Incomplete histories can only restore the latest event they received. This task does not claim to reconstruct an omitted terminal snapshot.

## Verification

- Add pure frontend regression tests for completed-after-approval, reversed input ordering, approval metadata retention, and plan isolation.
- Add or extend the backend SOP tool test to assert that completion emits a full `sop:updated` payload with completed/succeeded state. This protects the producer contract without changing production backend code.
- Run existing SOP reducer/live tests to ensure live semantics are unchanged.

## Risks And Follow-Ups

- `sop_runs` and trace events can diverge when event persistence fails after the mutable snapshot commits.
- Legacy trace arrays may truncate terminal events, and incomplete pagination cannot reconstruct omitted state.
- SOP-specific replay ordering does not fully reuse the canonical `history_order` tuple.

These risks are real but independently reproducible and require broader protocol/API decisions. They are out of scope for the deterministic frontend overwrite fix.

## Rollback

The change is isolated to history restoration and focused tests. Rollback restores the previous helper/call site; there is no schema, migration, or persisted-data change.
