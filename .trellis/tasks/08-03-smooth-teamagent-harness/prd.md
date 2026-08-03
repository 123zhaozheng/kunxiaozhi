# Smooth TeamAgent harness orchestration

## Goal

Make TeamAgent a dependable complex-task harness: uploaded user files are
materialized into the shared sandbox before delegation, the router produces a
reviewable SOP/plan and pauses for explicit user confirmation, and only then
does it orchestrate persona subagents through a structured, observable,
recoverable handoff contract.

## Background / Confirmed Facts

- The application `TeamAgent` wraps an inner `deepagents.create_deep_agent`
  router graph; role members are declarative DeepAgents subagents invoked by
  the `task` tool.
- TeamAgent currently lacks the explicit attachment-to-sandbox capability and
  its prompt-level mention of sandbox upload is not a sufficient runtime
  guarantee.
- The current task-tool handoff returns text/`ToolMessage` results and does not
  by itself guarantee a validated SOP, attachment path propagation, complete
  roster selection, or explicit user approval.
- The change spans backend runtime, API/event contracts, persistence/resume,
  frontend confirmation UX, and focused tests.

## Requirements

- Materialize every successfully uploaded attachment into the session's shared
  sandbox before the TeamAgent router plans delegated work; expose a stable
  sandbox path and actionable materialization errors.
- Give the router a deterministic attachment manifest and a registered runtime
  tool/capability for any supported on-demand upload or verification operation;
  prompt text alone is not an accepted contract.
- Add a structured planning/SOP phase that records ordered steps, role
  assignments, dependencies, required artifacts, and expected completion
  criteria.
- Require confirmation only when the router proposes delegated multi-step or
  multi-role work; a simple answer that uses no role task may complete without
  an approval round trip.
- Present the plan to the user and pause execution until an explicit confirm
  action is received. Rejection may include feedback and triggers replanning;
  inline free-form plan editing is not required for the first implementation.
- After confirmation, compile and invoke only the intended role roster through
  structured handoffs that carry the objective, context, attachment paths,
  predecessor artifacts, and correlation identifiers.
- Stream plan, confirmation, role start/progress/result/error, synthesis, and
  terminal state events with enough team/member/persona-version correlation for
  the UI and persistence layer to reconstruct a run.
- Define partial-failure, cancellation, timeout, retry, and resume behavior so
  the router cannot report successful completion when required work is missing.
- Preserve existing non-team agent behavior and compatibility with the pinned
  DeepAgents version unless a separate migration decision is required.

## Acceptance Criteria

- [ ] An uploaded file has a verified sandbox path before any confirmed role
      task starts; a failed materialization is visible and blocks or qualifies
      the affected plan.
- [ ] A representative request emits a structured SOP/plan, waits for explicit
      user confirmation, and performs no role task call before confirmation.
- [ ] A confirmed plan invokes the selected persona roles with a machine-readable
      handoff containing attachment paths and dependency/artifact metadata.
- [ ] The final compiled TeamAgent roster is deterministic and excludes any
      unintended default role, or the behavior is explicitly controlled and
      tested.
- [ ] The UI/API can distinguish plan awaiting confirmation, role progress,
      partial failure, cancellation, retry, synthesis, and completion.
- [ ] Focused backend/frontend tests cover attachment propagation, confirmation
      gating, handoff validation, roster construction, and recovery semantics.
- [ ] Existing targeted TeamAgent and non-team agent tests remain green, apart
      from documented pre-existing environment-only failures.

## Notes

- This is an implementation task; production code, API contracts, UI, and tests
  are in scope. The earlier architecture review remains read-only evidence.
- Broad persona-authoring redesign, model-quality benchmarking, and unrelated
  agent refactors are out of scope.
- Materialization failures block confirmation and execution for the first
  implementation; silent URL-only fallback is prohibited.
- Plan confirmation reuses the existing approval transport/UI where practical,
  but must add typed TeamAgent plan metadata and durable resume semantics.
