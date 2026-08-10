# Research: update_sop End-to-End Contract Audit

- Query: Trace exact fields, statuses, IDs, persistence, live SSE, history replay, DAG rendering, approval/replan, and later status updates for TeamAgent `update_sop`.
- Scope: mixed (internal backend/frontend/tests/specs)
- Date: 2026-08-10

## Findings

### Data-flow map

```text
TeamAgent nodes
  -> update_sop StructuredTool (SOPPlan snapshot)
  -> SopRunStore.sop_runs, key=(session_id, team_id)
  -> Presenter.emit_team_event
  -> Redis stream (event_id + stream id) + trace event storage
  -> SSE event `{event_type, data, id/event_id, run_id}`
  -> useAgent eventHandlers
       sop:updated: reduceSop(bare plan snapshot)
       approval_required(type=sop_plan): reduceSop(nested plan + id)
  -> ChatView -> SopBlock -> SopFlow (step_id nodes, dependency edges)

History: GET /sessions/{session}/events -> all paginated trace events by default
  -> latest `sop:updated` or SOP approval event -> reduceSop -> DAG card

Approval: approval_required.data.id -> SopBlock -> POST /human/{id}/respond
  -> Mongo approval response + notify/wakeup wait_for_response
  -> store status running/rejected -> sop:updated snapshot
```

The live event contract is internally consistent: backend emits `sop:updated` with
the plan as `data` and `approval_required` with `{id, type: "sop_plan",
plan_id, plan}` (`src/agents/team_agent/sop/tool.py:39-70`); the presenter
whitelists both and saves them (`src/infra/writer/presenter_events.py:420-432`,
`src/infra/writer/present.py:224-228`). `SopRunStore` serializes all backend
fields unchanged (`src/agents/team_agent/sop/schemas.py:29-70`; `store.py:71-85`),
and the frontend mirrors the field names and status unions
(`frontend/src/types/sop.ts:13-61`). The frontend normalizer accepts `step_id`
and the compatibility aliases `id`, `expectedOutput`, and `planId`
(`frontend/src/types/sop.ts:144-160,182-210`).

### Severity-ranked mismatches

#### Critical: plan identity is not part of confirmation gating

`sop_runs` is uniquely located by `(session_id, team_id)` and overwrites the
existing document (`src/agents/team_agent/sop/store.py:71-85,87-93`). The tool
then decides that a submission is a post-confirmation status update solely from
the existing plan status (`src/agents/team_agent/sop/tool.py:202-205`), without
requiring `existing.plan_id == plan.plan_id`. Consequently a new plan ID in an
already `running`/`completed` session bypasses the approval gate. The confirmed
path only updates step IDs found in the old snapshot and then emits that old
snapshot (`tool.py:82-107`), so a changed DAG can report success while silently
discarding its new goal/steps. This breaks R4's "confirmation before any
dispatch" and R2's full-replacement semantics.

Minimal contract: `plan_id` is immutable identity. Store lookup/update must
either key by `(session_id, team_id, plan_id)` or compare the incoming ID before
entering the confirmed path. A different ID is always a new draft requiring
`awaiting_confirmation`; a same-ID update may change only statuses, attempts,
outputs, and errors (plus an explicitly permitted terminal plan status).

#### High: history replay can display a different run's SOP

`loadHistory(targetSessionId, targetRunId)` calls `sessionApi.getAllEvents` with
no `run_id` filter (`frontend/src/hooks/useAgent.ts:386-388`). The API supports
the filter (`src/api/routes/session.py:275-330`), but the frontend then selects
the latest SOP event from the complete session event list
(`useAgent.ts:475-483`). A session with multiple runs or teams can therefore
show an older run's DAG/status/approval in the current chat. This is especially
misleading because the durable SOP store is also session/team scoped while the
event stream is run scoped.

Minimal contract: when a target run is selected, pass `run_id=targetRunId` to
history and select only that run's SOP events. If session-wide history is
intentional, SOP events must carry and be filtered by a canonical plan/run
identity and the UI must label the selected plan; do not silently use the last
event across runs.

#### High: event persistence failures are swallowed after state mutation

`_emit_sop_updated` catches every presenter exception and only logs it
(`src/agents/team_agent/sop/tool.py:44-52,54-70`). `Presenter.save_event` likewise
logs general write failures and returns (`src/infra/writer/presenter_storage.py:169-209`).
The SOP Mongo snapshot may already be updated while neither Redis live delivery
nor trace history contains the event. The tool can then return success and the
frontend has no replayable update. This violates the intended present+save
contract and leaves approval/status transitions unrecoverable from history.

Minimal contract: distinguish non-fatal live publish failure from durable trace
failure. Persist the snapshot/event before acknowledging the tool; propagate or
return a structured persistence error when the durable write fails, and retry or
surface a reconnect/state-snapshot path for Redis delivery. Never claim a
successful status transition solely because `sop_runs` wrote.

#### Medium: approval ID is frontend-only and is lost on a fresh snapshot

The backend plan model has no `approval_id`; only `approval_required.data.id`
contains it (`src/agents/team_agent/sop/tool.py:59-67`). The frontend stores it
as an extension and preserves it only while the current React state exists
(`frontend/src/types/sop.ts:197-210,226-237`). A page reload during
`awaiting_confirmation` can replay the plan snapshot and approval event if both
are retained, but a missing/truncated approval event leaves the DAG with no
confirm action even though the server approval remains pending. Generic
approval polling is intentionally skipped for SOP events
(`frontend/src/hooks/useAgent/eventHandlers.ts:252-266`; `historyLoader.ts:120-161`).

Minimal contract: include `approval_id` (or a durable approval reference) in the
persisted SOP snapshot while awaiting confirmation, or add a session/run SOP
status endpoint that returns the pending approval ID. The frontend should not
depend on event ordering or the presence of one transient event to recover the
confirm/replan controls.

#### Medium: approval timeout has no SOP state/event transition

`wait_for_response` returns `None` on timeout, but `_request_confirmation`
returns only `{"timed_out": true, "plan_id": ...}` and deliberately leaves the
stored plan in `awaiting_confirmation` without emitting a timeout snapshot
(`src/agents/team_agent/sop/tool.py:134-138`). Approval records expire in the
approval store (`src/infra/storage/mongodb.py:219-240`), so the frontend can be
left with an `awaiting_confirmation` card whose approval ID no longer resolves
and whose buttons cannot succeed. This is a stale-state/recovery break even
though the normal approve/reject path is correct.

Minimal contract: transition the plan to an explicit `timed_out`/`cancelled`
state (or retain `awaiting_confirmation` with a new `approval_expired` flag),
persist and emit that snapshot, and make the card offer replan/cancel without
posting to an expired approval ID.

#### Medium: dispatch guard cannot identify which same-assignee step is running

`task` calls carry `subagent_type`, while the guard finds the first step with a
matching `assignee` whose status is not in `{running,succeeded,failed}`
(`src/agents/team_agent/sop/guard.py:19-60`). The tool/task contract does not
carry `step_id`; two sequential steps assigned to the same persona can cause
the guard to remind about the first pending step even after the main agent
updated the second, or allow dispatch without a precise node identity. This is
an ID mismatch between DAG nodes (`step_id`) and execution calls
(`subagent_type`).

Minimal contract: include `step_id` in the dispatch request/context and validate
that exact node's status and assignee. If agent-driven dispatch deliberately
omits it, enforce a unique pending step per assignee during plan validation.

#### Low: index readiness is advisory for `sop_runs`

`_ensure_indexes_if_needed` logs index creation errors and continues; `upsert_plan`
still performs the write (`src/agents/team_agent/sop/store.py:42-69,71-84`). A
transient index failure can permit duplicate `(session_id, team_id)` documents,
after which `find_one`/upsert behavior is ambiguous. This is a persistence
readiness gap, not a frontend field mismatch.

Minimal contract: fail closed on first SOP write until the unique index is
confirmed, then retry; surface a structured tool error rather than accepting a
non-unique snapshot.

### Status and field compatibility matrix

| Boundary | Canonical current contract | Observation |
|---|---|---|
| Plan identity | `plan_id` | Backend store key omits it; frontend node/layout key uses it only as a layout cache key (`frontend/src/hooks/useSopStatus.ts:24-33`). |
| Step identity | `step_id`; dependencies reference `step_id` | Backend validator checks references; frontend node IDs and edges use the same values (`schemas.py:103-129`, `useSopStatus.ts:40-66`). |
| Assignee | stable `subagent_type` string | Validated against roster; guard matches it but has no step ID (`tool.py:193-200`, `guard.py:47-60`). |
| Step statuses | `pending/running/succeeded/failed/cancelled` | Backend and frontend agree. `blocked`, `skipped`, and `ready` appear in design/research recommendations but are not accepted by the runtime enum. |
| Plan statuses | `draft/awaiting_confirmation/running/completed/failed/cancelled/rejected` | Backend/frontend agree; timeout leaves `awaiting_confirmation` and emits no timeout event (`tool.py:134-138`). |
| Snapshot event | `sop:updated.data = SOPPlan` | Full replacement; excluded from message parts and replayed into standalone SOP state (`eventHandlers.ts:270-277`, `historyLoader.ts:108-117`). |
| Approval event | `approval_required.data.id` + `type="sop_plan"` + nested `plan` | Frontend sends `approved` and JSON `response` as query parameters to the matching route (`useApprovals.ts:48-67`; `human.py:245-296`). |
| Stream/history identity | Redis stream is `(session_id, run_id)`; history event has `run_id`, `event_id`, `seq` | Live SSE is run-isolated (`src/api/routes/chat.py:616-658`); default history is session-wide unless `run_id` is passed. |

## External References

- `@xyflow/react` and `@dagrejs/dagre` are the frontend DAG/rendering dependencies; layout is derived once from step IDs/dependencies and status-only updates reuse coordinates (`frontend/src/hooks/useSopStatus.ts:123-156`).
- Installed DeepAgents/LangChain behavior is not needed to establish the contract breaks above; the relevant SOP code is application-owned.

## Related Specs

- `.trellis/spec/guides/cross-layer-thinking-guide.md` (explicit boundary/data-flow checklist).
- `.trellis/spec/backend/session-history-pagination.md` (run filters, stable event identity, replay completeness).
- `.trellis/spec/backend/trace-event-storage.md` (durable event identity and failure visibility).
- `.trellis/spec/backend/agent-harness.md` (Team `update_sop` replacement for `write_todos`).
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/prd.md` and `design.md` (R1-R8 and approved SOP decisions).

## Caveats / Not Found

- No SOP-specific HTTP read endpoint exists; `sop_runs` is consumed by the tool and dispatch guard only. Frontend recovery is therefore trace-event based.
- Existing tests cover schema/normalizer/event-handler/approval idempotency paths, but do not prove plan-ID replacement isolation, multi-run replay filtering, durable-event failure propagation, or same-assignee dispatch behavior.
- `blocked/skipped/ready` are mentioned by broader design research but are outside the current PRD's explicit runtime status list; this audit treats the current seven plan and five step statuses as canonical until the product contract expands.
