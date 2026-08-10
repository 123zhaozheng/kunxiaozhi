# Research: Redundancy and Capability-Boundary Audit

- Query: Audit the TeamAgent SOP/DAG change for dead or duplicate code, unnecessary wrappers/state copies/styles/types/tests, stale/generated files, unrelated edits, main-agent over-capability, role-subagent inheritance of `update_sop`, and overlap with existing approval/todo/event/harness abstractions.
- Scope: mixed (internal repository plus installed DeepAgents behavior)
- Date: 2026-08-10

## Findings

### P0 / High - role subagents inherit the main-agent-only `update_sop` tool

`team_router_node` appends `update_sop` to the parent `filtered_tools` list (`src/agents/team_agent/nodes.py:346-363`), but each declarative role spec omits a `tools` field (`nodes.py:467-480`). The installed `deepagents==0.6.7` implementation explicitly fills an omitted subagent tool list from the parent (`.venv/Lib/site-packages/deepagents/graph.py:633-645`). Therefore every role subagent receives `update_sop`, not just the router. `TeamToolExclusionMiddleware` removes only `write_todos` (`src/agents/team_agent/tool_exclusion.py:68-85`), and `SopDispatchGuardMiddleware` is mounted only on the main stack (`src/agents/team_agent/nodes.py:582-591`; `sop/guard.py:1-5`). A role can consequently rewrite the plan, create/retrigger approval, or race status updates, violating the documented main-agent-only ownership.

Keep `update_sop` on the parent, but give each role an explicit, intentionally filtered `tools` list (at minimum excluding `update_sop`, approval management, and router-only tools). Add a test that inspects the actual generated role specs/tool names; the current hook test only asserts the parent list (`tests/agents/test_team_agent_sop_tool_hook.py:174-190`).

### P1 / High - Team router still exposes delivery/file tools despite removing their prompt guidance

The Team context excludes management and `ask_human` tools (`src/agents/team_agent/context.py:9-20`), and the main prompt drops file/reveal guidance (`src/agents/team_agent/nodes.py:520-528`). However `FastAgentContext.setup()` always adds `reveal_file`, `reveal_project`, and transfer tools (`src/agents/fast_agent/context.py:161-180`), while the shared filter treats `reveal_file`, `reveal_project`, and `transfer_file` as protected built-ins that can never be filtered (`src/agents/core/tool_filter.py:14-22,70-73`). The resulting `filtered_tools` is passed to the Team main agent (`nodes.py:331-363,593-606`). Prompt slimming is therefore not a capability boundary: the router can still perform file delivery/reveal directly.

Define a router-specific allow/deny boundary after built-in loading rather than relying on user-disabled-tool filtering. Role subagents should receive the narrower role list as well. Do not delete the reveal/transfer tools globally; Fast/Search use them for delivery.

### P1 / High - `SopDispatchGuardMiddleware` is an advisory wrapper, not the advertised dispatch boundary

When the plan is missing or is `draft`, `awaiting_confirmation`, or `rejected`, the guard immediately calls the task handler (`src/agents/team_agent/sop/guard.py:47-60`). It only returns a reminder for a `running` plan with a matching non-running step (`guard.py:61-74`). This makes the wrapper redundant as a security control: pre-confirmation dispatch remains possible, and the prompt plus middleware give conflicting impressions about enforcement. Existing tests encode the permissive behavior (`tests/agents/test_sop_dispatch_guard.py:84-97`).

Choose one contract. If zero dispatch before approval is required, reject all task calls until an approved/running plan and authorized step exist. If advisory behavior is intentional, rename/document it as a reminder middleware and remove claims that it is a gate; otherwise it will be mistaken for approval enforcement.

### P1 / Medium - SOP state/layout is computed twice, with one result discarded

`useAgent` calls `useSopStatus(null)` to own `sopPlan` and `setSopPlan` (`frontend/src/hooks/useAgent.ts:87`), but `useSopStatus` also derives and caches dagre nodes/edges (`frontend/src/hooks/useSopStatus.ts:129-156`). `SopBlock` creates a second hook instance and performs the layout actually rendered (`frontend/src/components/sop/SopBlock.tsx:49-60`). The first hook's `nodes`/`edges` are discarded, yet every SOP update still recomputes them. This is an unnecessary state/layout copy, not independent state ownership.

Split the hook into a plain SOP snapshot state hook and a layout derivation hook, or pass one derived layout from the owner. Preserve one authoritative `SopPlan` and one layout cache.

### P1 / Medium - declared `SopPart` rendering path is dead and inconsistent with the live path

`MessagePart` includes `SopPart` and `MessagePartRenderer` renders it (`frontend/src/types/message.ts:34-46,90-96`; `frontend/src/components/chat/ChatMessage/MessagePartRenderer.tsx:283-291`), but no event processor creates a `type: "sop"` part. Live events update the separate `useAgent.sopPlan` state (`frontend/src/hooks/useAgent/eventHandlers.ts:252-277`), history intentionally skips SOP events (`frontend/src/hooks/useAgent/historyLoader.ts:108-137`), and the actual card is mounted globally in `ChatView` (`frontend/src/components/layout/AppContent/ChatView.tsx:497-506`). Even if a part is injected, that branch omits `onRespond` and `isLoading`, so approval is read-only.

Consolidate on the global card and remove the unused `SopPart` type/import/renderer and `clearAllLoadingStates` SOP branch (`frontend/src/hooks/useAgent/messageParts.ts:646-666`), or make message parts the sole path and wire event/history/approval state through it. Keeping both paths creates dead code and two incompatible rendering contracts. Current tests reinforce the dead path by asserting history does not rehydrate SOP parts (`frontend/src/hooks/__tests__/useSopStatus.test.tsx:340-414`).

### P1 / Medium - SOP approval duplicates the existing approval service at the wrong layer

`update_sop` imports `create_approval` and `wait_for_response` directly from the FastAPI route module (`src/agents/team_agent/sop/tool.py:12-15,110-158`). The existing `ask_human` tool imports and uses the same route helpers (`src/infra/tool/human_tool/tool.py:12,163-177`) and emits the generic `approval_required` event (`human_tool/tool.py:320-362`). SOP adds a second payload/parser/timeout path rather than sharing an application-level approval service; agent code now depends upward on `src.api.routes.human`.

Retain `ask_human` for ordinary interactive questions, but extract shared create/wait/respond primitives below the route layer. Have both `ask_human` and SOP use that service, with `sop_plan` as a typed approval payload. Do not merge the UI cards: SOP needs its DAG-specific controls, while generic approvals need form fields.

### P2 / Medium - harness-level Todo exclusion and request-level Todo stripping overlap

The Team profile excludes `TodoListMiddleware` during DeepAgents assembly (`src/agents/team_agent/harness_profile.py:19-43`), while every Team graph still appends `TeamToolExclusionMiddleware` (`src/agents/team_agent/nodes.py:579-581`), which strips the tool and prompt section at request time (`src/agents/team_agent/tool_exclusion.py:27-85`). This is defensible as an unresolved-model-key fallback, but it is two implementations of the same Todo removal contract. Keep the request middleware only if the profile-resolution-failure path is covered; otherwise make the profile the sole main-agent mechanism and scope the fallback to unresolved profiles. Do not remove the shared harness constants, since the exclusion middleware currently consumes them by the backend harness contract (`.trellis/spec/backend/agent-harness.md:17-31`).

### P2 / Low - SOP package re-exports are unused and import extra infrastructure

`src/agents/team_agent/sop/__init__.py:3-11` re-exports schemas/store symbols, but production code imports concrete submodules and no search result uses these package-level names. Importing the package also imports `SopRunStore`, which imports Mongo settings/client wiring (`sop/store.py:9-18`). Either make the re-export module the intentional public API and use it consistently, or reduce it to a side-effect-free package initializer. This is a cleanup candidate, not a deletion required for correctness.

### P2 / Low - generated and unrelated worktree files should stay outside the feature boundary

The SOP package has ignored `__pycache__/*.pyc` files; they are generated artifacts and are not tracked or suitable for a feature commit. The current dirty tree also contains unrelated changes: `.claude/settings.local.json`, `AGENTS.md`, `src/infra/session/trace_storage.py`, `tests/infra/session/test_trace_storage_token_usage.py`, root `research/*`, and untracked `AGENTS copy.md`. The task-local git-scope audit identifies those as separate configuration/history work or a duplicate instruction backup (`research/final-audit-git-scope.md:53-82`). Exclude them from the SOP commit; do not delete unrelated research or user WIP without owner confirmation. `AGENTS copy.md` is the only strong stale-file deletion candidate, but cleanup is outside this read-only audit.

## Files Found

- `src/agents/team_agent/nodes.py` - Team main/role tool and middleware assembly.
- `src/agents/team_agent/context.py` - Team router exclusions and sandbox tool addition.
- `src/agents/team_agent/tool_exclusion.py` - request-layer `write_todos` filtering.
- `src/agents/team_agent/harness_profile.py` - assembly-time Todo middleware exclusion.
- `src/agents/team_agent/sop/tool.py` - `update_sop`, approval flow, and event emission.
- `src/agents/team_agent/sop/guard.py` - task dispatch reminder middleware.
- `src/agents/team_agent/sop/__init__.py` - package-level re-exports.
- `frontend/src/hooks/useAgent.ts` - authoritative SOP plan state.
- `frontend/src/hooks/useSopStatus.ts` - SOP layout cache and second state holder.
- `frontend/src/components/sop/SopBlock.tsx` - rendered global SOP card.
- `frontend/src/components/chat/ChatMessage/MessagePartRenderer.tsx` - unused/read-only SOP part branch.
- `frontend/src/hooks/useAgent/eventHandlers.ts` - live SOP event reducer path.
- `frontend/src/hooks/useAgent/historyLoader.ts` - SOP event exclusion during message reconstruction.
- `frontend/src/types/message.ts` and `frontend/src/types/sop.ts` - SOP message/type and normalizer contracts.
- `src/infra/tool/human_tool/tool.py` - existing generic approval tool.
- `src/api/routes/human.py` - shared approval create/wait/response route helpers.
- `src/agents/fast_agent/context.py` and `src/agents/core/tool_filter.py` - protected built-ins and tool filtering.
- `.venv/Lib/site-packages/deepagents/graph.py` - installed DeepAgents 0.6.7 subagent inheritance behavior.
- `.trellis/spec/backend/agent-harness.md` - harness capability/profile contract.

## Code Patterns

- Parent tools are inherited by declarative subagents when their spec omits `tools` (`.venv/Lib/site-packages/deepagents/graph.py:633-645`).
- Team currently passes one shared mutable `filtered_tools` list to the parent and role specs (`src/agents/team_agent/nodes.py:331-363,467-480`).
- SOP state updates use a separate global card path rather than message parts (`frontend/src/hooks/useAgent/eventHandlers.ts:252-277`; `ChatView.tsx:497-506`).
- Approval creation/waiting is shared by route and `ask_human`, but SOP reaches route helpers directly (`src/api/routes/human.py:103-208`; `src/infra/tool/human_tool/tool.py:163-177`; `src/agents/team_agent/sop/tool.py:110-158`).

## External References

- Installed package: `deepagents==0.6.7`; local source inspected at `.venv/Lib/site-packages/deepagents/graph.py`.
- React Flow dagre pattern referenced by the implementation: `frontend/src/components/sop/sopLayout.ts:4-6`, https://reactflow.dev/examples/layout/dagre.

## Related Specs

- `.trellis/spec/backend/agent-harness.md` - one capability switch, Team Todo exclusion, runtime/model-view boundary, and no duplicate harness constants.
- `.trellis/spec/backend/quality-guidelines.md` - storage/route boundaries and typed interfaces.
- `.trellis/spec/frontend/state-management.md` - state locality and avoiding unnecessary global/duplicate state.
- `.trellis/spec/frontend/hook-guidelines.md` - hook ownership and side effects.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/prd.md`, `design.md`, and `implement.md` - SOP ownership, approval, and UI acceptance criteria.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/final-audit-backend.md` and `final-audit-frontend.md` - complementary correctness findings.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/research/final-audit-git-scope.md` - dirty-file ownership and commit boundary.

## Caveats / Not Found

- This audit is read-only and wrote only this research file; no product or test files were changed.
- Findings are source- and installed-package-based. No browser smoke test or live DeepAgents graph invocation was run.
- The role inheritance finding is specific to the locally installed `deepagents==0.6.7`; pin/upgrade behavior should be rechecked if the dependency changes.
- `TeamToolExclusionMiddleware` may remain necessary for unresolved model-profile keys; it is marked as a consolidation candidate rather than dead code.
- Cross-language schema/status constants are intentionally duplicated between Pydantic and TypeScript boundaries and are not called redundant by this audit.
