# Research: SOP history state regression test reproduction

- Query: Inventory SOP/update_sop, event persistence, history serialization, and DAG hydration coverage; derive the smallest deterministic case where a completed live SOP reopens as pending confirmation.
- Scope: mixed (tests, fixtures, and directly exercised interfaces)
- Date: 2026-08-12

## Findings

### Existing coverage by layer

- Backend tool lifecycle: `tests/agents/test_sop_tool_gate.py` exercises `update_sop` creation, approval gate, approval/rejection/timeout, confirmed step updates, and completion. In particular, `test_dispatch_plan_blocks_then_approved_to_running` (lines 184-212) asserts the initial `sop:updated` is `awaiting_confirmation`, the next is `running`, and `approval_required` contains a nested full `plan`. `test_confirmed_plan_completion_sets_plan_status` (lines 295-316) verifies the in-memory/store snapshot becomes `completed` with all steps succeeded, but does not assert the emitted completed event payload.
- Backend persistence/serialization: `tests/agents/test_sop_store.py` (`test_upsert_and_get_roundtrip`, `test_upsert_writes_full_doc_via_set`, `test_set_step_status_updates_and_returns_snapshot`) verifies status, step status/output, and datetime JSON round trips through the fake Mongo collection. It does not replay multiple persisted event snapshots.
- Event protocol: `tests/infra/test_sop_presenter_events.py` (`test_present_team_event_whitelist_accepts_sop_events`, `test_emit_team_event_presents_and_saves`) verifies `sop:updated` and `approval_required` are allowed and saved, but only with minimal payloads and no ordering/state assertions.
- Frontend live reduction: `frontend/src/hooks/__tests__/useSopStatus.test.tsx` lines 254-335 cover `sop:updated`, SOP approval interception, approval-id preservation across a later running snapshot, and non-SOP approvals. This proves live arrival order works.
- Frontend normalization/reducer: `frontend/src/types/__tests__/sop.test.ts` lines 143-191 verify full snapshot replacement, approval-id preservation, invalid payload handling, and plan-id replacement. `reduceSop` intentionally treats every event payload as a full snapshot (`frontend/src/types/sop.ts:224-251`).
- Frontend history/message replay: `frontend/src/hooks/__tests__/useSopStatus.test.tsx` lines 354 onward verifies SOP events do not become message parts or generic approvals. It does not inspect the `setSopPlan` value produced by `useAgent.loadHistory`.
- History ordering/pagination: `frontend/src/hooks/useAgent/__tests__/historyLoader.test.ts` has `history_order` ordering coverage (around lines 475-537); `frontend/src/services/api/__tests__/historyPagination.test.ts` covers page accumulation, dedupe, incomplete history, and preservation of distinct `history_order` rows. Neither test combines SOP snapshots with approval replay. `useAgent.loadHistory` uses a separate timestamp/`seq` sort (`frontend/src/hooks/useAgent.ts:480-492`), not the `history_order` comparator used by `historyLoader.ts`.

### Smallest deterministic reproduction

Use one plan (`plan-1`) with two steps (`s1`, `s2`) and this exact event set. Array order can be intentionally reversed; the loader's timestamp/sequence sort must put them in causal order:

```ts
const pendingPlan = {
  plan_id: "plan-1", goal: "build a report", session_id: "session-1", team_id: "team-1",
  status: "awaiting_confirmation",
  steps: [
    { step_id: "s1", title: "Research", dependencies: [], assignee: "team-m1-role1", expected_output: "out1", status: "pending" },
    { step_id: "s2", title: "Write", dependencies: ["s1"], assignee: "team-m2-role2", expected_output: "out2", status: "pending" },
  ],
};
const completedPlan = {
  ...pendingPlan,
  status: "completed",
  steps: pendingPlan.steps.map((step) => ({ ...step, status: "succeeded", output: "done" })),
};
const events = [
  { event_type: "sop:updated", timestamp: "2026-08-12T00:00:02.000Z", seq: 2, data: completedPlan },
  { event_type: "approval_required", timestamp: "2026-08-12T00:00:01.000Z", seq: 1,
    data: { id: "approval-1", type: "sop_plan", plan_id: "plan-1", plan: pendingPlan } },
];
```

Executable reproduction of the current `loadHistory` logic (the block at `frontend/src/hooks/useAgent.ts:483-513`):

1. Sort `events` by timestamp/`seq` and filter with `isSopReplayEvent`.
2. `restoredPlan = reduceSop(null, latestSopEvent)`; this is `completed` with both steps `succeeded`.
3. Find the reverse latest matching-plan `approval_required` event.
4. Current code calls `reduceSop(restoredPlan, latestApproval)`. Because the approval envelope carries a nested full pending snapshot, `reduceSop` replaces the completed plan and returns `status === "awaiting_confirmation"`, both steps `pending`, and `approval_id === "approval-1"`.

The regression assertion is therefore:

```ts
assert.equal(restoredPlan?.status, "completed");
assert.deepEqual(restoredPlan?.steps.map((s) => s.status), ["succeeded", "succeeded"]);
assert.equal(restoredPlan?.approval_id, "approval-1");
```

The Current implementation fails the first two assertions after the approval merge. To demonstrate the failure directly, evaluate `const buggy = reduceSop(restoredPlan, latestApproval)` and assert `buggy.status === "completed"`; this assertion fails because `buggy.status` is `"awaiting_confirmation"`. The intended fix should preserve the latest full snapshot and merge only approval metadata, so the final state remains completed/succeeded while retaining `approval_id`.

### Recommended regression matrix

1. **Pure history SOP reducer (required, frontend):** add a testable helper around the `loadHistory` SOP restoration block, or export an equivalent pure function. Feed the two-event fixture above (including reversed input order). Assert final `plan_id`, `status: "completed"`, every step `succeeded`, and `approval_id: "approval-1"`.
2. **Live/history parity (required, frontend):** apply the same snapshots through `handleStreamEvent` updaters (`approval_required` then completed `sop:updated`) and through the history helper; assert both produce identical plan status/step states and approval ID. Existing `useSopStatus.test.tsx` covers the running variant but not completed state or history parity.
3. **Plan scoping (required, frontend):** include an older approval for `plan-old` and a completed `plan-1`; assert no approval ID from the old plan is attached. This protects the existing same-plan guard at `useAgent.ts:497-508`.
4. **Backend emitted completed snapshot (focused, Python):** extend `test_confirmed_plan_completion_sets_plan_status` to assert the single emitted `sop:updated` payload has `status == "completed"` and all serialized step statuses are `"succeeded"`. This locks the producer contract used by history replay.
5. **Persistence round trip (already mostly covered):** retain `test_set_step_status_updates_and_returns_snapshot` and add/confirm a `set_status(..., "completed")` readback assertion if backend changes touch status serialization. No new Mongo integration fixture is needed.
6. **Ordering/pagination (secondary):** do not make cursor pagination part of the minimal regression; the confirmed failure occurs with a complete in-memory event list. Add a `history_order` variant only if the fix changes SOP restoration to use the canonical comparator, and add a multi-page fixture only if the API can return the approval and final snapshot on different pages.

### Missing coverage and focused commands

- Missing contract: no test calls the `useAgent.loadHistory` SOP reconstruction path or asserts `setSopPlan` after history hydration; no test protects completed snapshots from an older nested approval replacement.
- Missing producer assertion: backend completion test checks the store but not the emitted event that becomes the history source.
- Focused frontend commands (from `frontend/`): `pnpm exec tsx --test src/types/__tests__/sop.test.ts src/hooks/__tests__/useSopStatus.test.tsx src/hooks/useAgent/__tests__/historyLoader.test.ts`; include the new history restoration test file/helper in the same command.
- Focused backend commands (repository root): `pytest -q tests/agents/test_sop_tool_gate.py tests/agents/test_sop_store.py tests/infra/test_sop_presenter_events.py`.

## Caveats / Not Found

- `useAgent.loadHistory` is a large hook with internal state setters; current tests use source-presence checks for race/scope behavior (`useAgentLoadHistoryRace.test.ts`, `useAgentHistoryScope.test.ts`) rather than mounting the hook. A small pure restoration helper is the least brittle way to make the regression executable.
- The backend `approval_required` event intentionally stores a complete nested plan (`src/agents/team_agent/sop/tool.py:60-76, 126-146`), so treating it as metadata requires an explicit frontend merge contract; changing `reduceSop` globally would risk live semantics.
- `history_order` and cursor pagination are relevant only to event ordering/completeness. They are not needed to reproduce the confirmed completed-to-pending regression with a complete two-event history.
- Keep the regression fixture deliberately complete; pagination and missing-history behavior should remain separate tests.
