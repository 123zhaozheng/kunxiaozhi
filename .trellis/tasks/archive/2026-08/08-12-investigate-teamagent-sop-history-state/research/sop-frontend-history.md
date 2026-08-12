# Research: Frontend SOP history/state reconstruction

- Query: Trace live `sop:updated` / `approval_required(sop_plan)` events and historical events through the frontend, including status keys, snapshot merging, defaults, ordering, pagination, and the pending-confirmation regression.
- Scope: internal frontend
- Date: 2026-08-12

## Findings

### Live stream data flow

- `useAgent` owns the only SOP snapshot state: `const { plan: sopPlan, setSopPlan } = useSopStatus(null)` ([frontend/src/hooks/useAgent.ts:87]). It exposes that plan to `ChatAppContent`/`ChatView`, which renders one `SopBlock` ([frontend/src/components/layout/AppContent/ChatAppContent.tsx:178,898], [frontend/src/components/layout/AppContent/ChatView.tsx:497-504]).
- SSE events are parsed and deduplicated by `eventId`; events older than `lastHistoryTimestampRef` are discarded ([frontend/src/hooks/useAgent/eventHandlers.ts:63-111]). `approval_required` with `type`/`approval_type === "sop_plan"` is intercepted and updates `sopPlan`; generic approval state is bypassed ([eventHandlers.ts:259-273]). `sop:updated` likewise bypasses message processing and updates the plan ([eventHandlers.ts:275-281]).
- Both paths call `reduceSop(prev, { event_type, data })`. The reducer treats payloads as complete snapshots, not patches ([frontend/src/types/sop.ts:224-251]). A new snapshot replaces plan/step statuses, but carries forward `approval_id` only when the plan id is unchanged and the incoming snapshot has no approval id ([sop.ts:231-250]). This preserves the approval identifier when event order is `approval_required -> sop:updated`; it does not preserve old status fields.
- Live status normalization is strict. Unknown plan statuses fall back to `draft`, unknown step statuses to `pending`; missing arrays/ids invalidate the payload ([sop.ts:91-145,147-166,179-221]). Thus a malformed/missing `status` is visibly pending/draft, but a valid `completed` snapshot remains completed.

### DAG derivation and rendering

- `useSopStatus` follows the external `plan` reference in an effect and derives React Flow nodes/edges from the current normalized snapshot ([frontend/src/hooks/useSopStatus.ts:105-125,138-178]). `buildSopFlowElements` keys nodes by `step_id`; status, assignee, output/error, title, and dependencies come directly from each snapshot ([useSopStatus.ts:36-91]).
- Layout is cached using `plan_id + step_id + sorted dependencies`; status-only updates replace node data while retaining coordinates ([useSopStatus.ts:25-34,145-170]). Therefore a stale `SopPlan` snapshot produces a stale DAG without an additional UI cache or status derivation.
- `SopBlock` uses `plan.status` for the header and confirmation controls. Confirm/replan controls are shown only for `awaiting_confirmation` plus a truthy `approval_id` ([frontend/src/components/sop/SopBlock.tsx:77-93]). Node success/progress is counted from `step.status === "succeeded"` ([SopBlock.tsx:67-75]). A completed plan with old approval metadata would still hide controls; an awaiting-confirmation snapshot shows the regression directly.

### History loading and event selection

- `loadHistory` clears the old SOP state before fetching (`setSopPlan(null)`) and requests all session events through `sessionApi.getAllEvents` ([frontend/src/hooks/useAgent.ts:323-338,387-414]). `getAllSessionEvents` follows `next_cursor`, deduplicates by `event_id`/id or a composite fallback, and marks results incomplete after abort, repeated cursors, or later-page failures ([frontend/src/services/api/session.ts:49-125]). An incomplete page can therefore omit the terminal SOP event; the UI only exposes `historyIncomplete`/`historyError` as diagnostics.
- Message reconstruction intentionally excludes `sop:updated` and SOP approval events from assistant message parts; SOP is a side-channel card ([frontend/src/hooks/useAgent/historyLoader.ts:115-126,165-195]). Existing history tests assert no SOP message part and no generic approval panel ([frontend/src/hooks/__tests__/useSopStatus.test.tsx:372-450]).
- After messages/goals are reconstructed, `useAgent.loadHistory` rebuilds SOP separately. It sorts all events by timestamp, then uses `seq` only as a same-timestamp tie-breaker; it ignores the backend `history_order` tuple used elsewhere by the history comparator ([frontend/src/hooks/useAgent.ts:480-492], compare logic in [frontend/src/hooks/useAgent/historyLoader.ts:81-113]). It filters `sop:updated` and `approval_required(sop_plan)` events, chooses `replayEvents.at(-1)` as `latestSopEvent`, and normalizes that single snapshot ([useAgent.ts:492-496]).
- It then searches backward for the latest matching-plan `approval_required` event and applies it to the already selected plan via `reduceSop(restoredPlan, latestApproval)` ([useAgent.ts:497-514]). This is the key divergence from live behavior: `approval_required` contains a nested full plan snapshot (normally the original `awaiting_confirmation` snapshot), not just metadata. Calling `reduceSop` replaces the completed plan's status and every step with that nested snapshot while adding `approval_id`.

### Exact stale-state mechanism

For a history containing (same `plan_id`):

1. `approval_required` with nested plan status `awaiting_confirmation` and pending steps;
2. later `sop:updated` with status `completed` and succeeded steps;

`latestSopEvent` is correctly the completed update, but `latestApproval` still points to event 1. `reduceSop(completedPlan, approvalEvent)` normalizes event 1's nested plan and returns it as a full replacement. The final `setSopPlan` therefore stores `awaiting_confirmation`/pending steps, reproducing the report. The live stream does not have this regression because each event is reduced once in arrival order; the later completed `sop:updated` replaces status and steps and only preserves approval_id ([eventHandlers.ts:266-280], [sop.ts:231-250]).

The same bug occurs if `approval_required` sorts after the completion due to timestamp/sequence skew: the history pass intentionally reapplies the approval regardless of its relative position. Conversely, if pagination is incomplete and the terminal `sop:updated` is absent, selecting the approval snapshot is expected from the available data but should be reported as incomplete.

### Keys, defaults, and duplicate state

- Authoritative frontend status is the normalized `SopPlan` snapshot (`plan.status`, `steps[].status`), keyed by `plan_id` and each `step_id`; React Flow node ids are exactly step ids ([sop.ts:49-63], [useSopStatus.ts:41-61]).
- `approval_id` is frontend-only metadata extracted from the approval envelope (`payload.id`); bare snapshots never infer it ([sop.ts:211-216]). The nested approval plan duplicates all plan/step fields, creating the divergent snapshot that causes the regression.
- `sessionApi.getAllEvents` preserves page append order but the SOP-specific history sort does not use `history_order`; `historyLoader`'s general comparator does use the full tuple when present ([historyLoader.ts:81-113]). This is a second ordering risk for same-time/multi-trace records, although the approval reapplication bug is sufficient to explain the stale state.
- No frontend history API or component stores a separate SOP record. Clearing history/session also clears the singleton `sopPlan` state ([useAgent.ts:893-940]).

### FastAgent/Search todo comparison

- Todo events are handled by the shared `processMessageEvent` path as `todo:updated`, creating/replacing one `TodoPart`; at depth 0 `upsertTodoPart` replaces the existing part, while nested events are attached by subagent depth ([frontend/src/hooks/useAgent/eventProcessor.ts:368-387,499-507]).
- History replay feeds todo events through the same processor, in sorted event order, so later full todo snapshots replace earlier ones. There is no separate history pass that re-applies an earlier tool envelope. Tool results are associated by `tool_call_id` where available, with a name/pending fallback only for legacy top-level results ([eventProcessor.ts:231-311]).
- This explains why todo history generally remains stable: one reducer, one chronological fold, and no duplicated approval snapshot. SOP currently has two reducers in history (latest snapshot selection, then old approval replacement).

### Tests and coverage gaps

- Covered: normalizer aliases/defaults, full snapshot replacement, approval-id carry-forward, plan-id reset, replay-event detection ([frontend/src/types/__tests__/sop.test.ts:52-220]); live event handlers and message-side history exclusion ([frontend/src/hooks/__tests__/useSopStatus.test.tsx:254-450]); pagination/dedup/incomplete-page behavior ([frontend/src/services/api/__tests__/historyPagination.test.ts:1-190]).
- Missing: a `useAgent.loadHistory` regression test with `approval_required(awaiting_confirmation)` followed by `sop:updated(completed)` for the same plan, asserting the final `sopPlan` remains completed; the inverse event ordering should also be tested. There is no test that `history_order` affects SOP replay ordering, nor one that an incomplete final page is surfaced alongside the resulting stale/partial SOP.

## Recommended smallest fix

Keep the latest full snapshot as the source of truth for status/steps. When attaching an approval from history, extract only its envelope id (and expiry metadata), or copy `approval_id` onto `restoredPlan` without passing the approval event through `reduceSop`. Do not replace plan fields with the nested approval snapshot. A compatible implementation can add a small helper that normalizes the matching approval, copies `approval_id` only when the plan ids match, and leaves the selected snapshot unchanged. Add focused tests for completed-after-approval, approval-after-completed, and missing-terminal-event/incomplete-history cases. Consider using the shared `history_order` comparator for the SOP sort to remove cross-trace timestamp ties.

## Caveats / Not Found

- This note does not inspect backend SOP persistence/event emission; it assumes history payloads contain the full nested approval snapshot as represented by the frontend contract. Backend artifacts should confirm whether approval events are intentionally immutable snapshots.
- Existing tests exercise reducers and message reconstruction but do not invoke the `loadHistory` callback, so the stale overwrite is not currently caught.
