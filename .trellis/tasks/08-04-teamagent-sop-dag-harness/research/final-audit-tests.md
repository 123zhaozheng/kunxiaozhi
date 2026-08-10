# Research: Final SOP DAG Acceptance Audit

- Query: Trace the SOP DAG PRD acceptance criteria and implementation checklist to current code/tests, then run feasible backend/frontend validation.
- Scope: mixed (internal code, tests, task artifacts, and installed DeepAgents behavior)
- Date: 2026-08-10

## Findings

### Requirement-to-evidence matrix

| Requirement | Evidence | Status | Audit result |
|---|---|---|---|
| R1 team-mode `update_sop` entry, simple-answer bypass, non-team compatibility | Tool is appended in `src/agents/team_agent/nodes.py:346-363`; SOP prompt is appended in `nodes.py:549-552`; mode wiring is covered by `tests/agents/test_team_agent_sop_tool_hook.py` and `test_team_router_prompt.py`. | Partial | No end-to-end model run proves complex-task planning -> approval -> dispatch -> delivery, and simple-task bypass remains prompt/model behavior rather than a deterministic classifier. |
| R2 SOP model and deterministic validation | `src/agents/team_agent/sop/schemas.py:16-133` defines statuses, plan/step fields, dependency/DFS/assignee/output checks; `tool.py:192-200` returns errors before persistence/events. Targeted schema/tool tests pass. | Partial | Duplicate `step_id` values are not rejected: `steps_by_id = {step.step_id: step ...}` silently overwrites at `schemas.py:109`. No malformed payload/property-based validation coverage. |
| R3 max/min step granularity | `TEAM_SOP_MAX_STEPS` and `TEAM_SOP_MIN_STEPS` are configured at `src/kernel/config/base.py:161-164`; max is enforced at `schemas.py:115-116`; prompt displays the range in `prompt_section.py:28-32`. | Partial | `min_steps` is intentionally ignored by `validate_sop_plan` (`schemas.py:106-107`) and `_run_update_sop` only treats zero steps as direct answer (`tool.py:186-190`). There is no deterministic “below min with no dispatch value” behavior or test. |
| R4 confirmation gate, rejection/timeout/idempotency | `_request_confirmation` persists `awaiting_confirmation`, creates `sop_plan` approval, emits events, and waits at `tool.py:118-152`; route idempotency is at `src/api/routes/human.py:260-296`; tests cover approval/rejection/timeout/processed-response. | Partial / blocker | The runtime guard explicitly passes through when plan is missing or not `running` (`src/agents/team_agent/sop/guard.py:47-49`), and tests codify pass-through for `awaiting_confirmation` (`tests/agents/test_sop_dispatch_guard.py:84-97`). Thus there is no hard zero-dispatch guarantee; only the blocking tool call/prompt normally prevents the next model turn. Approval idempotency tests do not exercise two concurrent pending responses; route check-then-update is not atomic. |
| R5 persistent state and event contract | `SopRunStore` uses MongoDB `sop_runs`, compound unique index, snapshot operations (`src/agents/team_agent/sop/store.py:20-136`); presenter allowlist/double-write is at `src/infra/writer/presenter_events.py:420-432` and `src/infra/writer/present.py:224-228`; targeted store/event tests pass. | Partial | Tests use an in-memory fake collection, not MongoDB or cross-process recovery. `set_step_status` is read-modify-write (`store.py:105-126`), so concurrent updates can lose one another; no concurrency/version test exists. |
| R6 frontend DAG card and history | Types/normalization/reducer: `frontend/src/types/sop.ts:13-251`; dagre layout: `frontend/src/components/sop/sopLayout.ts:30-68`; React Flow/card/status/approval UI: `SopFlow.tsx`, `SopNode.tsx`, `SopBlock.tsx`; event/history wiring: `frontend/src/hooks/useAgent/eventHandlers.ts:252-277`, `historyLoader.ts:108-138`, and `useAgent.ts:475-483`. Frontend SOP tests pass. | Partial | No browser/manual end-to-end test covers SSE -> card -> approval response -> live state -> completion, mobile behavior, click details, or real React Flow interaction. The primary card is rendered from `ChatView.tsx:497-505`; the `MessagePartRenderer` SOP branch is not integration-tested. |
| R7 soft task-description guidance | Prompt says to include step requirements/precedent outputs (`src/agents/team_agent/sop/prompt_section.py:21-23`); router prompt requires delegation (`src/agents/team_agent/prompt.py:14-21`). | Partial | There is no code that constructs or verifies task descriptions from the current SOP step and predecessor output; behavior is solely LLM prompt compliance. |
| R8 sandbox upload tool | `TeamAgentContext.setup()` appends `get_upload_url_tool()` only when sandbox is enabled (`src/agents/team_agent/context.py:27-47`); tests cover enabled/disabled and retained tools. | Complete (unit scope) | No live sandbox integration was run. |
| R9 default-off compatibility and rollback | Mode branches in `nodes.py:349-363`, `549-591`; disabled-mode node tests pass. | Partial / blocker | PRD requires `TEAM_SOP_MODE` default false, but current config is true at `src/kernel/config/base.py:162`. No explicit default-value regression test exists. The task is therefore enabled by default and does not meet the stated compatibility/default contract. |

### Known architecture gaps

- Child tool visibility is not isolated. Team role specs omit `tools` in `src/agents/team_agent/nodes.py:467-480`; DeepAgents documents omitted subagent tools as inherited (`.venv/Lib/site-packages/deepagents/middleware/subagents.py:47-51`), and graph assembly supplies the main `_tools` to the general-purpose subagent (`.venv/Lib/site-packages/deepagents/graph.py:688-693`). Since `filtered_tools` contains `update_sop`, child agents can receive it. `TeamToolExclusionMiddleware` only excludes `write_todos` (`src/agents/team_agent/tool_exclusion.py:68-85`). No test inspects each child model's tool list.
- The intended “hard” dispatch gate is a reminder middleware, not a hard block. It returns `handler(request)` for `draft`, `awaiting_confirmation`, `rejected`, and no-plan states (`guard.py:47-49`), directly conflicting with the acceptance wording “confirmation前零子代理 dispatch.”
- Store writes are full-document snapshots. Concurrent `set_step_status` calls can overwrite another step's update; no optimistic version/atomic positional update or concurrency test exists.
- Approval idempotency handles an already processed approval, but two simultaneous requests can both observe `pending` before `update_status`; no conditional update/unique decision claim or race test exists.
- Real MongoDB, Redis/distributed approval notification, SSE transport, and a browser/manual team workflow were not exercised. The available tests are mocked/unit-level.

### Implementation checklist audit

- Wave 0.1: `team_harness_profile.py` exists, but it is a context manager around `build_default_harness_profile(todo_enabled=False)` rather than the planned `_TeamHarnessProfile`/registered team profile with the specified `ShortTodoListMiddleware` assertions. `tests/agents/test_team_harness_profile.py` is absent. Status: missing/partial.
- Wave 0.2: Team prompt slimming and router constraints are present in `nodes.py:520-538` and `prompt.py:14-21`, with tests. However `TEAM_ROUTER_SYSTEM_PROMPT` still names `reveal_file` at `prompt.py:17`; this is a delegation constraint, while the test expects the name, so the checklist's “no reveal_file guidance” wording is not fully unambiguous. Status: partial.
- Wave 0.3: SOP prompt and `SopDispatchGuardMiddleware` are present, mounted under the feature flag, and tested for pending/running behavior. The pre-confirmation hard-gate claim is not met as described above. Status: partial.
- Wave 1.0-1.2: Sandbox registration, schema validation, and Mongo snapshot store are implemented with passing focused tests. Status: complete for unit scope.
- Wave 1.3: `update_sop` gate branches, rejection, timeout, and post-confirmation updates are implemented and focused tests pass. Status: partial because zero-dispatch and race semantics are not proven.
- Wave 1.4: event allowlist/double-write and processed `sop_plan` idempotency are implemented and tested. Status: partial because concurrent pending-response idempotency and real persistence are absent.
- Wave 1.5: mode wiring and prompt section exist and tests pass. Status: partial because child tool inheritance is not checked and default mode is wrong.
- Wave 2.1-2.3: dagre dependency, types/reducer, React Flow components, ChatView/event/history wiring, and focused tests exist. Status: partial because no browser/e2e workflow or visual/manual verification was run.

## Validation results

Commands run from `D:\code\python\LambChat` unless noted:

| Command | Result |
|---|---|
| `pytest -q tests/agents/test_sop_schemas.py tests/agents/test_sop_store.py tests/agents/test_sop_tool_gate.py tests/agents/test_sop_dispatch_guard.py tests/agents/test_team_agent_sop_tool_hook.py tests/agents/test_team_context_sandbox_tools.py tests/agents/test_team_router_prompt.py tests/agents/test_sop_prompt_section.py tests/api/test_sop_approval_idempotent.py tests/infra/test_sop_presenter_events.py` | PASS: 59 passed, 2 deprecation warnings, 7.75s. |
| `pytest -q tests/agents tests/api` | FAIL: 425 passed, 3 unrelated failures. Failures: `tests/api/test_persona_preset_routes.py::test_list_persona_presets_returns_real_total` (Mongo/event-loop cleanup), and two `tests/api/test_shared_page_route.py` newline expectation failures (`CRLF` vs `LF`). No SOP test failed. |
| `ruff check src/agents/team_agent src/api/routes/human.py src/infra/writer/present.py src/infra/writer/presenter_events.py tests/agents tests/api/test_sop_approval_idempotent.py tests/infra/test_sop_presenter_events.py` | PASS: All checks passed. |
| `mypy src/agents/team_agent src/api/routes/human.py src/infra/writer/present.py src/infra/writer/presenter_events.py` | PASS: no issues in 17 source files. |
| `pnpm exec tsx --test src/types/__tests__/sop.test.ts src/components/sop/__tests__/sopLayout.test.ts src/components/sop/__tests__/SopBlock.test.tsx src/hooks/__tests__/useSopStatus.test.tsx` (from `frontend`) | PASS: 32 tests passed. One test logs an expected Node-only `localStorage` warning while exercising a non-SOP approval branch; it does not fail. |
| `pnpm lint` (from `frontend`) | PASS. |
| `pnpm exec tsc -b` (from `frontend`) | PASS. |
| `pnpm build` (from `frontend`) | PASS in 107.6s; Vite emitted existing large-chunk warnings. |
| `node --experimental-strip-types --test ...tsx ...` (initial frontend attempt) | Runner failure: Node 22 does not load `.tsx` directly; the project has no Vitest script/binary. Re-run with `pnpm exec tsx --test` above passed. |

## Caveats / Not Found

- PRD acceptance checkboxes and implementation checklist entries remain unchecked, and `task.json` remains `in_progress`; this audit does not mark task artifacts complete.
- No real-model or browser workflow was available in this audit, so “complex task automatically creates a DAG,” “confirmation precedes every dispatch,” and “completed delivery” remain untested end-to-end.
- Existing unrelated worktree changes were preserved. Generated frontend `dist` output may have changed during `pnpm build`; no product/test files were edited by this audit.
