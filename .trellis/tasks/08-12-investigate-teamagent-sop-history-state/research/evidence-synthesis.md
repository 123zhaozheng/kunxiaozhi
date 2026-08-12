# Research: Evidence synthesis for TeamAgent SOP history state

- Query: Cross-check six research artifacts and source code to separate the deterministic stale-SOP root cause from persistence, ordering, and pagination risks; define the smallest backward-compatible fix and regression boundary.
- Scope: mixed internal source, history, tests, and Git evolution
- Date: 2026-08-12

## Findings

### Confidence-ranked root cause

1. **High confidence, deterministic root cause: frontend history hydration re-applies an old full approval snapshot.** `useAgent.loadHistory` selects the last replayable SOP event (`frontend/src/hooks/useAgent.ts:480-496`), then finds a same-plan `approval_required` and calls `reduceSop(restoredPlan, latestApproval)` (`useAgent.ts:497-514`). `reduceSop` is explicitly full-snapshot replacement (`frontend/src/types/sop.ts:224-251`), while the approval envelope contains a nested `awaiting_confirmation` plan (`src/agents/team_agent/sop/tool.py:54-67`). Therefore a history containing approval/pending followed by `sop:updated`/completed deterministically ends as pending, even though the selected latest event was completed. This is introduced by the SOP history pass in `04612bf2`; live SSE reduces each event once and does not exhibit the overwrite.

2. **Medium confidence, independent ordering risk:** the SOP-specific sort uses timestamp, then `seq`/array index (`useAgent.ts:484-491`) and ignores server `history_order`, while generic replay uses the canonical comparator (`frontend/src/hooks/useAgent/historyLoader.ts:81-113`). In merged/legacy histories this can select the wrong latest SOP event under ties or missing sequence values. It is not required for the deterministic reproduction above.

3. **Medium-high confidence, projection/durability risk:** `SopRunStore` keeps one mutable current snapshot per `(session_id, team_id)` (`src/agents/team_agent/sop/store.py:80-104`), but history reads only persisted trace events. SOP store writes can succeed while event emission/storage logs and continues (`src/agents/team_agent/sop/tool.py:44-51`; `src/infra/writer/presenter_storage.py:206-210`), and buffered/legacy storage can lose or truncate events. This can produce a missing terminal event or genuinely stale history, but it is a different symptom from the approval overwrite when a complete event set is available.

4. **Medium risk:** cursor pagination and bounded legacy arrays may return `history_complete=false` and omit the terminal snapshot. The current UI exposes diagnostics but does not reconcile from `sop_runs`; a partial history can legitimately hydrate an older approval/pending snapshot. This belongs in follow-up behavior/tests, not the minimal fix.

5. **Low-medium backend race/status risks:** per-process locks do not provide cross-worker optimistic versioning; confirmed-plan updates only change overall status when the caller explicitly supplies a non-`draft` status (`src/agents/team_agent/sop/tool.py:82-107`). These can cause stale/running state, but not the reported pending-confirmation regression.

### Evidence table

| Claim | Evidence | Confidence / interpretation |
| --- | --- | --- |
| Approval carries a complete old plan | `_emit_approval_required` includes `plan: _plan_json(plan)`; request path sets `awaiting_confirmation` first (`tool.py:54-67, 118-134`) | High; approval is an immutable snapshot envelope, not metadata-only |
| SOP reducer replaces snapshots | `reduceSop` normalizes payload and returns `next`; only same-plan `approval_id` is carried forward (`sop.ts:224-251`) | High; feeding approval back is destructive to status/steps |
| Hydration performs the destructive second reduction | `loadHistory` picks latest replay event, then invokes `reduceSop(restoredPlan, latestApproval)` (`useAgent.ts:480-514`) | High; exact causal line of failure |
| Live path is not affected | SSE handlers call one reducer for approval or `sop:updated` and keep SOP outside message parts (`eventHandlers.ts:258-283`) | High; explains live/history divergence |
| Backend current source differs from history projection | `SopRunStore` full replacement/current document; no history API read of it (`store.py:80-104`; session history route/storage artifacts) | High; explains why backend persistence cannot directly correct frontend hydration |
| Event history may be incomplete/stale | save/emission exceptions are logged; legacy arrays use bounded `$slice`; pagination returns completeness diagnostics (`presenter_storage.py:206-210`, `dual_writer.py`, `trace_storage.py`) | Medium-high; adjacent risk requiring separate policy |
| Ordering can diverge | Generic `historyLoader` honors `history_order`; SOP local sort does not (`historyLoader.ts:81-113`; `useAgent.ts:484-491`) | Medium; only manifests for tie/merge cases |

### Contradiction audit

- **“Backend persistence is the root cause” vs “frontend overwrite is the root cause”:** both can produce stale UI in different inputs. The complete two-event fixture proves the frontend overwrite without any storage failure, so backend persistence is not the deterministic reported cause. Treat `sop_runs` as live authoritative state and trace events as a historical projection.
- **“Latest event wins” vs approval metadata preservation:** this is compatible only if approval metadata is copied without replacing the latest snapshot. `reduceSop` already preserves `approval_id` for ordinary event order, but the history callback reverses the intended direction by applying the nested approval plan afterward.
- **SOP and todo parity:** todos use one chronological generic fold; SOP uses a dedicated state plus a second history pass. The old TeamPlan reducer merged incrementally, but current SOP snapshots are replacement semantics. Reusing the old approval-restoration shape is therefore not valid parity.
- **Ordering/pagination concerns vs minimal reproduction:** reversed input and pagination are unnecessary for the failure; they should not expand the first fix beyond the hydration contract.

## Recommended fix contract

1. Keep the latest normalized full SOP snapshot as the sole source for `status`, `steps`, plan fields, and terminal state.
2. When a matching `approval_required` is found, extract only frontend metadata (`approval_id`, and any explicitly agreed expiry/approval fields). Do not call `reduceSop` with the nested approval event and do not copy its status/steps over the selected snapshot.
3. Preserve existing plan-id scoping: approvals from older replans must not attach to the current plan.
4. Keep live reducer behavior unchanged; do not alter global `reduceSop` replacement semantics or backend event schemas. This is backward-compatible with existing persisted approval envelopes.
5. Prefer a small pure history-reconstruction helper around the current `useAgent` block so it can be unit-tested without mounting the full hook. If ordering is fixed in the same change, reuse/export the canonical `compareHistoryEvents`; otherwise record that as a separate follow-up.

### Source-of-truth contract

- **Live/current state:** `SopRunStore` document keyed by `(session_id, team_id)` is authoritative for backend guards and recovery.
- **Historical UI replay:** ordered `sop:updated` snapshots are authoritative for the latest renderable historical plan; `approval_required` contributes only approval envelope metadata.
- **Completeness:** `history_complete=false` means the latest historical state is not guaranteed; do not infer that an approval snapshot is the true current state. Surface diagnostics or add a later reconciliation/read API rather than silently treating partial history as complete.

## Regression boundary

### Required in the minimal fix

- Frontend pure helper/test: approval (`awaiting_confirmation`, pending steps) followed by same-plan completed `sop:updated`, including reversed input order; assert final `completed`, all steps `succeeded`, and approval ID retained.
- Inverse ordering: completed snapshot followed by approval envelope; assert completed status/steps remain unchanged and metadata is attached only if policy allows.
- Plan scoping: approval for `plan-old` must not attach to completed `plan-1`.
- Live/history parity: same snapshots through live handlers and history helper yield equivalent plan status/steps/approval metadata.

### Focused producer coverage (recommended, not required to fix frontend bug)

- Extend `tests/agents/test_sop_tool_gate.py:test_confirmed_plan_completion_sets_plan_status` to assert the emitted `sop:updated` payload is completed with succeeded steps. Existing store tests remain the persistence contract.

### Explicit non-goals / follow-ups

- Do not redesign `SopRunStore` into a revision/history collection or change session history API in this fix.
- Do not change approval event payloads or globally change `reduceSop` to merge fields; both risk existing live behavior and old histories.
- Do not fold pagination/incomplete-history handling into the deterministic regression. Add a separate test for approval and terminal events split across pages and assert diagnostics when the terminal page is unavailable.
- Follow up on SOP ordering parity by using `history_order`/canonical comparator in `useAgent` and add a merged-history tie fixture.
- Follow up on event durability/reconciliation policy for store-success/event-failure and legacy truncation; current architecture has no API that can repair a missing terminal event from `sop_runs`.

## Exact affected modules and test surfaces

- Primary implementation: `frontend/src/hooks/useAgent.ts` history SOP reconstruction block (lines 480-514); optionally a new colocated pure helper.
- Existing reducer contract: `frontend/src/types/sop.ts` (`reduceSop`, `normalizeSopEvent`).
- Existing canonical ordering: `frontend/src/hooks/useAgent/historyLoader.ts` (`compareHistoryEvents`).
- Producer contract: `src/agents/team_agent/sop/tool.py` and `src/agents/team_agent/sop/store.py`.
- Frontend tests: `frontend/src/types/__tests__/sop.test.ts`, `frontend/src/hooks/__tests__/useSopStatus.test.tsx`, plus a new focused history reconstruction test/helper; retain pagination/order suites as separate coverage.
- Backend tests: `tests/agents/test_sop_tool_gate.py`, `tests/agents/test_sop_store.py`, `tests/infra/test_sop_presenter_events.py`.

## Caveats / Not Found

- No existing test invokes `useAgent.loadHistory` and asserts the final `setSopPlan` value; the deterministic bug is therefore currently unguarded.
- No session-history path reads `sop_runs`; backend state cannot repair a truncated/incomplete event stream without a new API or client reconciliation step.
- Save/emission exceptions and legacy retention are real durability risks, but no evidence shows they are necessary for the reported completed-to-pending transition.
