# Research: Frontend SOP DAG Audit

- Query: Audit the SOP DAG frontend and cross-layer event/history flow against `prd.md`, `design.md`, and `implement.md`, including state replacement, approvals/replanning, history reconstruction, mobile/read-only React Flow behavior, unknown states, duplicate parts, and regressions.
- Scope: mixed (frontend implementation/tests, with the backend SOP event producer read only for contract alignment)
- Date: 2026-08-10

## Findings

### P1 - Stale SSE events are not isolated from a session/stream switch

`handleStreamEvent` captures `ctx.streamVersionRef.current` at event handling time (`frontend/src/hooks/useAgent/eventHandlers.ts:106-110`), so it is not a generation belonging to the SSE connection that produced the event. `loadHistory` aborts the old connection and clears `sopPlan` (`frontend/src/hooks/useAgent.ts:293-335`) but does not advance a connection generation. An old event arriving after the abort can therefore update the newly loaded session. SOP events are especially exposed because their early-return cases at `eventHandlers.ts:252-277` run before the generic stale-event check at `eventHandlers.ts:296-299`. This can replace the current session's DAG with the previous session's snapshot or restore an old approval ID.

The tests cover event IDs and ordinary SOP state updates, but no event is delivered after a history/session switch. The connection context needs an epoch captured when `connectToSSE` starts (or a session/run guard on every event), and SOP side effects must reject events from an older epoch.

### P1 - Full-snapshot replacement is lost in the React Flow layout cache

`buildSopStructureKey` intentionally includes only plan ID, step IDs, and dependencies (`frontend/src/hooks/useSopStatus.ts:24-32`). When that key is unchanged, the cache branch updates only `data.status` (`useSopStatus.ts:136-150`). A valid `sop:updated` full snapshot that changes a step title, assignee, description, expected output, or error/output while retaining the same IDs therefore leaves stale node text and tooltips on screen. The parent `SopPlan` itself is replaced, so the header/progress can disagree with node content. This is particularly relevant to an agent-driven replan and to status updates that add `error`/`output`.

The current tests assert only coordinate stability and status refresh (`frontend/src/hooks/__tests__/useSopStatus.test.tsx:89-109`, `157-176`), not the required data refresh. Keep coordinates stable while replacing all non-layout node data; if content changes affect measured height, explicitly re-layout or use a stable node height contract.

### P1 - History reconstruction can lose the approval ID and is order-dependent

History loading finds the latest replay candidate by reversing the API array (`frontend/src/hooks/useAgent.ts:475-483`), without applying the sequence/timestamp ordering used by `reconstructMessagesFromEvents` (`frontend/src/hooks/useAgent/historyLoader.ts:251-277`). If the API array is not already in causal order, an older snapshot can win. If the selected event is a bare `sop:updated` snapshot, `reduceSop(null, ...)` has no `approval_id` to attach (`frontend/src/types/sop.ts:197-211`), so an awaiting plan restored from history displays no confirm/replan buttons. The reducer preserves an ID only when a prior in-memory state exists (`types/sop.ts:226-237`), which is not true after the explicit `setSopPlan(null)` at `useAgent.ts:333-335`.

History should select the last valid normalized snapshot after the same deterministic sort used for message history, and independently associate the latest matching `approval_required(sop_plan)` ID for the same `plan_id`. Add coverage for unsorted events, a snapshot after approval creation, and malformed replay candidates.

### P1 - SOP approval timeout has no frontend expiry state or recovery

The backend intentionally leaves a timed-out plan in `awaiting_confirmation` (`src/agents/team_agent/sop/tool.py:140-144`). `SopBlock` renders confirm/replan actions whenever that status and an approval ID are present (`frontend/src/components/sop/SopBlock.tsx:69-79`, `207-252`), but it has no expiry/deadline, timeout event handling, or pending-approval status check. After the 300-second wait expires, the card can continue to show actionable buttons and a stale "Awaiting confirmation" status; the next click can fail with an already-expired approval while the user receives no "expired" state. This misses the PRD timeout acceptance path even though reject/replan payload construction is covered.

The card needs an expiry signal (or a backend status event/poll) and a terminal/disabled UI state, with a clear path to replan/cancel after timeout.

### P1 - The registered message-part renderer cannot perform SOP approval actions

`MessagePartRenderer` has a `part.type === "sop"` branch (`frontend/src/components/chat/ChatMessage/MessagePartRenderer.tsx:283-291`), but it renders `<SopBlock plan={part.plan}>` without `onRespond` or `isLoading`. Any SOP part on the normal or nested message-part path is consequently read-only, even for `awaiting_confirmation`. The live implementation instead renders a separate global card in `ChatView` (`frontend/src/components/layout/AppContent/ChatView.tsx:497-506`), and history deliberately creates no SOP part (`frontend/src/hooks/useAgent/historyLoader.ts:108-117`). This leaves the declared message-part contract and its approval interaction path dead/inconsistent.

Either make the global card the sole supported contract and remove the unused `SopPart` path, or pass the approval responder/loading state through every message-part renderer and create/rehydrate the part deterministically. The current tests assert that history has no SOP part (`frontend/src/hooks/__tests__/useSopStatus.test.tsx:340-414`), so they lock in the inconsistency rather than the PRD's message-part requirement.

### P2 - SOP approval detection is broader than the event contract

Both live and history paths classify any `approval_required` payload containing a `plan` object as SOP (`frontend/src/hooks/useAgent/eventHandlers.ts:252-264`; `frontend/src/hooks/useAgent/historyLoader.ts:120-138`), in addition to checking `type`/`approval_type`. A non-SOP approval that happens to carry a plan-shaped form field is swallowed and never reaches the generic approval panel. The same broad rule appears in `isSopReplayEvent` (`frontend/src/types/sop.ts:240-250`). Use an explicit `type === "sop_plan"` or `approval_type === "sop_plan"` discriminator, with nested-plan validation only as a documented legacy alias.

### P2 - A replan can inherit an approval ID from a different plan

`reduceSop` preserves `current.approval_id` whenever an incoming snapshot lacks one, without checking `current.plan_id === next.plan_id` (`frontend/src/types/sop.ts:232-236`). If a rejected/replanned plan gets a new `plan_id` and its `sop:updated` event races ahead of its new approval event, the card can submit the old approval ID for the new plan. Preserve IDs only for the same plan, or clear them whenever the plan identity changes and wait for the matching approval event.

### P2 - Invalid dependency references become dangling React Flow edges

The normalizer accepts every string in `dependencies` (`frontend/src/types/sop.ts:151`), and `buildSopFlowElements` emits an edge for each dependency without checking that its source is a node (`frontend/src/hooks/useSopStatus.ts:56-75`). Backend validation normally prevents this, but malformed/legacy history or a future backend alias can produce an edge from a missing node and a warning/incorrect graph. Normalize dependencies against the known step IDs or omit invalid edges while retaining the node snapshot.

### P2 - Layout work is duplicated and one result is dead

`useAgent` uses `useSopStatus(null)` only for `plan` and `setSopPlan` (`frontend/src/hooks/useAgent.ts:87`), but that hook still computes and caches dagre nodes/edges (`frontend/src/hooks/useSopStatus.ts:129-156`). `SopBlock` creates a second `useSopStatus(plan)` and performs the actual layout (`frontend/src/components/sop/SopBlock.tsx:58`). Every SOP snapshot can therefore run layout twice, with the first result discarded. Split state ownership from element derivation or pass the derived elements down once.

## Acceptance-Criteria Coverage

| Area | Evidence | Coverage |
|---|---|---|
| Normalize/reduce and unknown status fallback | `frontend/src/types/sop.ts:130-237`; `frontend/src/types/__tests__/sop.test.ts:54-172` | Covered for valid snapshots and unknown status strings. Dependency aliases/malformed replay are not covered. |
| DAG nodes, dependency edges, dagre layout | `frontend/src/hooks/useSopStatus.ts:35-82`; `frontend/src/components/sop/sopLayout.ts:30-68`; layout tests | Covered for TB/LR, fan-out, finite coordinates, and duplicate edges. Missing dependency behavior is not covered. |
| Status colors and read-only React Flow | `frontend/src/components/sop/SopNode.tsx:23-77`; `SopFlow.tsx:50-84` | Covered by source inspection: drag/connect disabled, controls/background/minimap present, fixed mobile height. No browser/mobile visual smoke test was found. |
| Live `sop:updated` replacement | `eventHandlers.ts:270-276`; `reduceSop` tests | Status/header replacement covered. Node metadata replacement and stale-stream isolation are not. |
| SOP approval bypasses generic panel | `eventHandlers.ts:252-266`; focused test 253-274 | Covered for an explicit `sop_plan` live event. Pending-approval restoration and over-broad `data.plan` discrimination are not. |
| Confirm/replan interaction | `SopBlock.tsx:207-252`; `sopBlockUtils.ts:6-13`; focused component tests | Payload/button visibility covered. Network success/failure, duplicate response, timeout, and stale approval ID are not. |
| History reconstruction | `historyLoader.ts:58-161`; `useAgent.ts:475-483`; focused tests 340-414 | Message-body duplication is prevented. Card selection ordering, approval-ID recovery, and malformed/latest event handling are incomplete. |
| Message-part integration | `message.ts:35-46`; `MessagePartRenderer.tsx:283-291`; `ChatView.tsx:497-506` | Type/branch exists, but live/history use the global card and the part branch cannot respond. This is a design-contract gap. |

## Files Found

- `frontend/src/types/sop.ts` - SOP schema normalization, reducer, replay predicate.
- `frontend/src/hooks/useSopStatus.ts` - React Flow element construction, dagre cache, state holder.
- `frontend/src/hooks/useAgent.ts` - live state ownership and history SOP snapshot selection.
- `frontend/src/hooks/useAgent/eventHandlers.ts` - SSE SOP/approval side effects.
- `frontend/src/hooks/useAgent/historyLoader.ts` - message reconstruction and SOP event exclusion.
- `frontend/src/hooks/useAgent/sseConnection.ts` - SSE connection/event dispatch boundary.
- `frontend/src/hooks/useApprovals.ts` - generic approval response/pending state path.
- `frontend/src/components/sop/SopBlock.tsx` - card, progress, confirmation/replan controls.
- `frontend/src/components/sop/SopFlow.tsx` - React Flow read-only canvas and fitting.
- `frontend/src/components/sop/SopNode.tsx` - node rendering/status styles/tooltips.
- `frontend/src/components/sop/sopLayout.ts` - dagre layout conversion.
- `frontend/src/components/chat/ChatMessage/MessagePartRenderer.tsx` - declared SOP message-part branch.
- `frontend/src/components/layout/AppContent/ChatView.tsx` - actual global SOP card placement.
- `frontend/src/hooks/__tests__/useSopStatus.test.tsx`, `frontend/src/types/__tests__/sop.test.ts`, and `frontend/src/components/sop/__tests__/*` - current focused coverage.
- `src/agents/team_agent/sop/tool.py` - read-only backend event/timeout contract.

## External References

- `@xyflow/react` `12.10.2` and `@dagrejs/dagre` `3.1.0` from `frontend/package.json`.
- React Flow dagre example referenced in `frontend/src/components/sop/sopLayout.ts:6`: https://reactflow.dev/examples/layout/dagre

## Related Specs

- `.trellis/tasks/08-04-teamagent-sop-dag-harness/prd.md` - R4/R5/R6 and acceptance criteria for approval, snapshots, history, read-only DAG, and mobile use.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/design.md` - frontend sections 3.1-3.2 and full-snapshot/state-flow decisions.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/implement.md` - wave 2.1-2.3 checks and manual smoke criteria.
- `.trellis/spec/frontend/hook-guidelines.md`, `component-guidelines.md`, `quality-guidelines.md`, and `type-safety.md` - hook state/side-effect, responsive/UI, test, and strict typing conventions.

## Caveats / Not Found

- Targeted frontend checks passed: ESLint on the SOP/event files, `pnpm exec tsc --noEmit`, and 32 focused SOP/layout tests via `pnpm exec tsx --test ...`.
- No browser/Playwright screenshot or real SSE/session-switch smoke test was run; the stale-stream and mobile findings are source-trace findings.
- No product or test files were edited. This report is the only file written by this audit.
