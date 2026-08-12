# Investigate TeamAgent SOP History State Persistence

## Goal

Ensure that a TeamAgent SOP DAG restored from conversation history shows the latest persisted step states. In particular, an SOP completed through `update_sop` must not return to a pending-confirmation state when the historical conversation is opened.

## Background

- TeamAgent exposes an SOP tool rendered as a DAG in the frontend.
- During a live conversation, `update_sop` can advance every SOP node to completed.
- Reopening or querying that conversation history can render the same SOP as pending confirmation.
- FastAgent and Search have todo-related tools whose state appears to remain stable across history restoration; their data flow is the reference implementation to inspect.
- Research confirmed that `useAgent.loadHistory` first selects the latest SOP snapshot, then reapplies an older same-plan `approval_required` event through the full-snapshot reducer. The nested approval plan replaces a completed plan with its original `awaiting_confirmation`/pending snapshot.
- The live path remains correct because it folds each event once in arrival order, so a later `sop:updated` completion snapshot wins while retaining only the approval identifier.

## Requirements

- Trace the complete SOP state lifecycle: tool creation, `update_sop`, event/message emission, session persistence, history API serialization, frontend parsing, and DAG reconstruction.
- Determine whether the stale state originates in runtime mutation, persistence, history selection/aggregation, protocol conversion, or frontend state derivation.
- Compare the SOP lifecycle with the FastAgent and Search todo lifecycle, including how later updates are associated with and applied to the original tool call.
- Identify the authoritative persisted representation and any duplicated or snapshot state that can diverge.
- Produce an evidence-backed root cause with file and symbol references.
- Design the smallest backward-compatible fix and focused regression tests across every affected layer.
- Preserve existing live-stream behavior and historical compatibility unless repository evidence demonstrates that a contract must change.
- Treat the latest complete SOP replay snapshot as authoritative for plan and step state. A matching approval event may contribute envelope metadata but may not replace the selected snapshot.
- Keep backend event schemas, `sop_runs`, and the global live reducer unchanged in this task.

## Acceptance Criteria

- [x] Research artifacts document the SOP write/read/render data flow with concrete code references.
- [x] Research artifacts document the FastAgent/Search todo persistence strategy and the meaningful differences from SOP.
- [x] The exact condition that causes a completed SOP to reopen as pending confirmation is identified with a deterministic two-event fixture.
- [x] The proposed design identifies the source of truth, update/merge semantics, backward compatibility behavior, and failure handling.
- [x] The implementation plan names the affected modules and includes focused frontend and backend contract coverage.
- [x] Historical restoration of an earlier `approval_required(awaiting_confirmation)` followed by a later `sop:updated(completed)` preserves `completed` and all succeeded steps.
- [x] A matching historical approval still supplies its approval identifier without replacing plan/step fields.
- [x] An approval for a different plan is not attached to the restored plan.
- [x] Live SOP handling and the backend payload/schema remain behaviorally unchanged.
- [x] Focused frontend and backend regression tests pass.
- [x] No product code was changed before the final planning summary was explicitly approved.

## Out Of Scope

- Redesigning the TeamAgent execution model or DAG visual language.
- Unrelated changes to general conversation history pagination or tracing.
- Migrating historical records unless the root cause requires a narrowly scoped compatibility path.
- Reconciling history against the mutable `sop_runs` collection.
- Changing history pagination, legacy trace retention, event durability, or canonical `history_order` handling; these are documented follow-up risks rather than causes required to reproduce this bug.
