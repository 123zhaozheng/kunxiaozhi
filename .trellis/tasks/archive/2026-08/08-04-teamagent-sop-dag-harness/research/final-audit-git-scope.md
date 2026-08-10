# Research: Dirty Worktree Scope Audit

- Query: Classify every modified or untracked worktree path against the active parent task `08-04-teamagent-sop-dag-harness`.
- Scope: internal
- Date: 2026-08-10

## Findings

### A. Clearly part of the parent task

The parent PRD defines R1-R9 at `prd.md:20-70`; the design maps directly to the backend SOP package (`design.md:29-108`), frontend DAG package (`design.md:110-133`), and harness follow-up (`design.md:150-178`). The implementation plan has matching backend, frontend, event, approval, and test work (`implement.md:9-103`). The following paths are therefore in the proposed parent-task commit boundary:

- `.trellis/tasks/08-04-teamagent-sop-dag-harness/check.jsonl`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/design.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/implement.jsonl`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/implement.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/prd.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/task.json`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/codebase-current-state.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/dag-visualization-frontend.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/final-audit-git-scope.md` (this audit report)
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/multi-agent-sop-orchestration.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/review-deepagents-prompt-pipeline.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/review-main-strategy.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/review-three-agent-mode-injection.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/review-write-todos-removal.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/sop-design-recommendations.md`
- `frontend/package.json`
- `frontend/pnpm-lock.yaml`
- `frontend/src/components/chat/ChatMessage/MessagePartRenderer.tsx`
- `frontend/src/components/layout/AppContent/ChatAppContent.tsx`
- `frontend/src/components/layout/AppContent/ChatView.tsx`
- `frontend/src/components/layout/AppContent/ChatViewProps.tsx`
- `frontend/src/components/sop/SopBlock.tsx`
- `frontend/src/components/sop/SopFlow.tsx`
- `frontend/src/components/sop/SopNode.tsx`
- `frontend/src/components/sop/__tests__/SopBlock.test.tsx`
- `frontend/src/components/sop/__tests__/sopLayout.test.ts`
- `frontend/src/components/sop/sopBlockUtils.ts`
- `frontend/src/components/sop/sopLayout.ts`
- `frontend/src/components/sop/sopNodeStyles.ts`
- `frontend/src/components/sop/sopPlanFlow.css`
- `frontend/src/hooks/__tests__/useSopStatus.test.tsx`
- `frontend/src/hooks/useAgent.ts`
- `frontend/src/hooks/useAgent/eventHandlers.ts`
- `frontend/src/hooks/useAgent/historyLoader.ts`
- `frontend/src/hooks/useAgent/messageParts.ts`
- `frontend/src/hooks/useAgent/types.ts`
- `frontend/src/hooks/useSopStatus.ts`
- `frontend/src/main.tsx`
- `frontend/src/types/__tests__/sop.test.ts`
- `frontend/src/types/index.ts`
- `frontend/src/types/message.ts`
- `frontend/src/types/sop.ts`
- `src/agents/team_agent/context.py`
- `src/agents/team_agent/nodes.py`
- `src/agents/team_agent/prompt.py`
- `src/agents/team_agent/sop/__init__.py`
- `src/agents/team_agent/sop/guard.py`
- `src/agents/team_agent/sop/schemas.py`
- `src/agents/team_agent/sop/store.py`
- `src/agents/team_agent/sop/tool.py`
- `src/api/routes/human.py`
- `src/infra/writer/present.py`
- `src/infra/writer/presenter_events.py`
- `tests/agents/test_sop_dispatch_guard.py`
- `tests/agents/test_sop_prompt_section.py`
- `tests/agents/test_sop_schemas.py`
- `tests/agents/test_sop_store.py`
- `tests/agents/test_sop_tool_gate.py`
- `tests/agents/test_team_agent_sop_tool_hook.py`
- `tests/agents/test_team_context_sandbox_tools.py`
- `tests/agents/test_team_router_prompt.py`
- `tests/api/test_sop_approval_idempotent.py`
- `tests/infra/test_sop_presenter_events.py`

Evidence: `@dagrejs/dagre` is added in `frontend/package.json:41`; frontend paths consume `sop:updated` and `approval_required(sop_plan)` and render `SopBlock`; backend paths implement `SOPPlan`, Mongo `SopRunStore`, `update_sop`, dispatch guard, TeamAgent wiring, approval idempotence, and Team event presentation. The new tests exercise those exact contracts.

### B. Already-owned/completed child task artifact

No dirty path belongs to a separate child task. The parent `task.json` declares child `08-07-unify-agent-harness-profile`, and that child was archived by `60ccbb37`; its implementation was committed in `44f4a6e1` (`src/agents/team_agent/harness_profile.py`, `tool_exclusion.py`, related specs/tests). Those files are clean and are only consumed by the dirty parent `src/agents/team_agent/nodes.py`. Do not re-add or revert the clean child commit.

### C. Unrelated user WIP

These paths are outside the SOP/DAG acceptance surface and should be excluded from the parent commit:

- `.claude/settings.local.json` — local permission allowlist adds MCP todo tools; no SOP implementation dependency.
- `AGENTS.md` — Trellis instruction text update, timestamped with the local settings change; project guidance/config rather than feature code.
- `src/infra/session/trace_storage.py` — only an extra blank line remains dirty; trace/history storage is not part of this parent design.
- `tests/infra/session/test_trace_storage_token_usage.py` — edits adapt dedup aggregation and the 10,000-event limit described by `research/history-loss-investigation.md`, not SOP events.
- `research/history-loss-investigation.md` — separate history-loss investigation; it names the trace storage/test changes and the already-landed history fixes (`1795fac0`, `d4985bb5`, `87d98309`).

These are safe to exclude from the parent feature commit. Preserve them for the owner of the history-loss/config work; do not revert them.

### D. Generated or misplaced output

- `AGENTS copy.md` is a duplicate backup of the project instructions (same content as `AGENTS.md` before the local update, untracked, and not referenced by code). It is safe to exclude and is the only path with strong evidence for deletion as generated junk, subject to owner confirmation.

### E. Ambiguous, exclude pending owner decision

- `research/dag-visualization-frontend.md`
- `research/multi-agent-sop-orchestration.md`

Both root-level files are clearly related in subject to the parent task, but they are outside the active task directory, were created before the task-scoped copies, and are not byte-identical to those copies (root sizes: 25,398 and 43,975 bytes; task-scoped sizes: 25,113 and 49,914 bytes). They may be intermediate research drafts rather than junk. They are safe to exclude from a feature commit, but should not be deleted without confirming whether the root `research/` directory is an intentional user workspace.

## Proposed cleanup boundary

For the parent task, stage only category A. Explicitly leave categories C-E out of the feature commit. Category B needs no work because the child artifact is already committed and clean. A future cleanup may remove `AGENTS copy.md` after owner confirmation; retain the three root research drafts and all category C paths until their owning work is separately committed or discarded.

## Caveats / Not Found

- Git history can establish committed ownership, but not the author of current uncommitted edits; timestamps are supporting evidence only.
- The active parent has no task-local child directory in the dirty tree, and `task.py list` reports only this parent as active.
- This audit did not run tests and did not modify or delete any product, test, config, or research path outside this report.
