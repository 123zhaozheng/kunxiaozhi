# Smooth TeamAgent Harness Execution Plan

## Ordered Checklist

1. Add TeamAgent orchestration schemas and persistence contracts:
   `AttachmentManifest`, deterministic `TeamRoster`, `TeamPlan`, approval state,
   `TeamHandoff`, typed role result, step/run status, and correlation metadata.
2. Implement the pre-planning attachment materializer using storage keys and
   the concrete shared sandbox backend; validate paths, ownership, provider
   responses, idempotency, and failure behavior. Register the existing external
   URL upload capability for TeamAgent without using it as the primary path.
3. Extract and test deterministic roster compilation: position/id ordering,
   unique subagent names, persona snapshot fields, default member, and explicit
   control of DeepAgents' `general-purpose` injection.
4. Add the typed planner/preflight phase. Validate role/dependency/attachment
   references and allow direct-answer plans to bypass approval.
5. Integrate the durable approval service and typed plan events. Enforce that
   no role task is called before approval; support reject-with-feedback,
   idempotent confirmation, timeout/cancellation, and recovery.
6. Add the task guard and handoff/result validation around the pinned
   DeepAgents task tool. Propagate verified attachment paths, predecessor
   artifacts, correlation identifiers, and attempts; prevent false completion.
7. Extend presenter/event persistence and task status/history semantics for
   awaiting confirmation, step progress, retry, partial failure, and resume.
8. Add the frontend plan confirmation view using the existing approval UI
   pattern; render roles/dependencies/attachments and update SSE dedupe,
   reconnect, history reconstruction, reject feedback, and terminal states.
9. Add focused backend and frontend tests plus a deterministic multi-role
   attachment scenario covering plan -> confirm -> parallel/dependent steps ->
   synthesis, partial failure, cancellation, and recovery.
10. Run full Trellis check, update executable backend/frontend specs, and split
    any deferred custom-task-tool migration into a follow-up task.

## Validation Plan

Backend checks will include focused TeamAgent, sandbox, task/event, approval,
and recovery tests, then the repository's configured lint/type checks for
changed Python modules. Frontend checks will include targeted chat/event/
approval tests, ESLint, TypeScript, and the production build.

Minimum deterministic scenario:

1. Submit two attachments and a team with at least two enabled roles.
2. Verify both storage objects are materialized exactly once under the real
   sandbox work directory and appear in the proposed plan.
3. Verify zero task calls before approval and zero after rejection.
4. Approve the plan and verify independent steps may run concurrently while a
   dependent step waits for predecessor artifacts.
5. Verify every event/result carries the plan/step/handoff/member correlation.
6. Force one role failure, confirm the run is not reported successful, then
   retry/resume without reuploading files or repeating completed steps.

## Review Gates

- Planning artifacts and JSONL context manifests must be approved before
  `task.py start`.
- No prompt-only attachment, confirmation, roster, or success guarantee is
  accepted.
- Do not write production code until schemas and ownership boundaries are
  reviewed against current approval/event persistence behavior.
- Use one writing agent per overlapping file group; backend and frontend work
  may proceed in parallel only after the shared event/schema contract lands.
- A fresh `trellis-check` agent performs the final review and may fix issues.

## Rollback Points

- Attachment materialization can be independently disabled before planner
  activation while retaining its isolated tests.
- Planner/approval/guard activation remains behind the TeamAgent harness
  option until backend and frontend event consumers are compatible.
- Event/schema changes are additive; rollback must leave old trace readers
  functional and preserve stored plans/manifests for audit.
