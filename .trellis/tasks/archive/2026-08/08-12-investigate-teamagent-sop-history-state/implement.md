# Implementation Plan

## Implementation

- [x] Introduce a pure SOP history restoration helper in the existing frontend history-loading module or a narrowly scoped adjacent module.
- [x] Preserve the latest normalized SOP snapshot as the source of truth.
- [x] Attach only same-plan approval envelope metadata without re-running the full snapshot reducer.
- [x] Replace the inline `useAgent.loadHistory` restoration block with the helper.
- [x] Avoid changes to live event handling, `reduceSop`, backend schemas, persistence, pagination, and the DAG component.

## Tests

- [x] Add a deterministic history test with early pending approval and later completed SOP update; assert completed plan and succeeded steps remain.
- [x] Assert the matching approval identifier is retained.
- [x] Assert different-plan approvals are ignored.
- [x] Exercise reversed input array order so restoration does not depend on API array order.
- [x] Extend the backend completion test to assert the emitted `sop:updated` payload is completed with succeeded steps, if this can be done without product backend changes.
- [x] Run existing live SOP reducer/event tests for regression coverage.

## Validation Commands

- `cd frontend && pnpm exec tsx --test <new-history-test> src/types/__tests__/sop.test.ts src/hooks/__tests__/useSopStatus.test.tsx src/hooks/useAgent/__tests__/historyLoader.test.ts`
- `pytest -q tests/agents/test_sop_tool_gate.py tests/agents/test_sop_store.py tests/infra/test_sop_presenter_events.py`
- Run the repository/frontend lint and type-check commands discovered from package scripts during implementation.

## Review Gates

- [x] Verify the helper never applies an approval's nested plan over a later snapshot.
- [x] Verify plan-id scoping and approval metadata behavior against `design.md`.
- [x] Verify no unrelated history ordering/pagination or backend persistence changes entered the diff.
- [x] Run a fresh Trellis check agent across the complete task diff.

## Rollback Point

- The frontend helper and call-site edit form one reversible unit; tests can be reverted with it. No stored data or API contract requires rollback.
