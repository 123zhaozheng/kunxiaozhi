# Research: Backend SOP DAG audit

- Query: Audit the TeamAgent SOP DAG backend against `prd.md`, `design.md`, and `implement.md`, with emphasis on approval hard-gating, zero dispatch before confirmation, schema validation, store concurrency, event contracts, feature flags, timeout/reject/idempotency, and recovery/history.
- Scope: internal (plus the installed DeepAgents implementation for tool inheritance)
- Date: 2026-08-10

## Files found

- `src/agents/team_agent/sop/schemas.py` - SOP Pydantic models and deterministic validation.
- `src/agents/team_agent/sop/tool.py` - `update_sop`, approval flow, and event emission.
- `src/agents/team_agent/sop/store.py` - MongoDB snapshot store.
- `src/agents/team_agent/sop/guard.py` - dispatch middleware.
- `src/agents/team_agent/nodes.py` - Team tool and middleware/subagent assembly.
- `src/agents/team_agent/context.py` - Team tool loading and sandbox upload tool.
- `src/api/routes/human.py` - approval response/wait/extension routes.
- `src/infra/storage/mongodb.py` - `ApprovalStorage` implementation.
- `src/infra/writer/presenter_events.py`, `src/infra/writer/present.py` - event whitelist and dual-write wrapper.
- `src/kernel/config/base.py`, `src/kernel/config/definitions.py` - SOP settings and defaults.
- `tests/agents/test_sop_{schemas,store,tool_gate,dispatch_guard}.py`, `tests/agents/test_team_agent_sop_tool_hook.py`, `tests/api/test_sop_approval_idempotent.py`, `tests/infra/test_sop_presenter_events.py` - focused regression tests.
- `.venv/Lib/site-packages/deepagents/graph.py:634-645` - installed `deepagents==0.6.7` parent-tool inheritance.

## Findings

### P0 / High - approval is not a hard dispatch gate

`SopDispatchGuardMiddleware` explicitly calls the task handler whenever the plan is absent or has status `draft`, `awaiting_confirmation`, or `rejected` (`src/agents/team_agent/sop/guard.py:55-59`). This means a `task` call can dispatch while approval is pending, after rejection, or with no plan. The guard only returns a reminder for a `running` plan whose target step is not yet `running` (`guard.py:61-86`); it does not enforce the required zero-dispatch-before-confirmation invariant. The regression test documents the defect as the expected behavior (`tests/agents/test_sop_dispatch_guard.py:84-97`).

The initial `update_sop` invocation does await `wait_for_response` (`src/agents/team_agent/sop/tool.py:131-150`), but that is not a system-level gate: concurrent tool calls, later turns, and any dispatch path not covered by this one middleware can still reach `task`. This fails PRD R4 and the acceptance criterion requiring zero subagent dispatch before confirmation.

### P0 / High - `update_sop` is inherited by every role subagent

The main `filtered_tools` list receives `update_sop` (`src/agents/team_agent/nodes.py:346-363`), while each role spec omits a `tools` entry (`nodes.py:467-480`). DeepAgents 0.6.7 fills an omitted subagent `tools` value from the parent (`.venv/Lib/site-packages/deepagents/graph.py:634-645`). Consequently role subagents can invoke the main-agent-only SOP tool, mutate the plan, create approval requests, or race step updates. The dispatch guard is only mounted on the main middleware stack and explicitly says it is not mounted on child agents (`src/agents/team_agent/sop/guard.py:9-10`; `src/agents/team_agent/nodes.py:582-591`). This undermines the approval boundary and the “one SOP owner” contract in R2/R4.

### P0 / High - snapshot writes lose concurrent step updates and stale approvals can start the wrong plan

`SopRunStore.upsert_plan` performs a read, mutates the caller's plan, then writes the entire document with `$set` (`src/agents/team_agent/sop/store.py:75-89`). `set_step_status` repeats the same read-modify-full-write sequence (`store.py:109-130`). Two concurrent updates based on the same snapshot can therefore overwrite each other's step status/output. The focused store tests only cover sequential writes (`tests/agents/test_sop_store.py:151-174`) and have no CAS/revision or concurrency case.

Approval decisions are not bound to a `plan_id` or approval id in the stored run. After waiting, approval code unconditionally sets the current `(session_id, team_id)` run to `running` (`src/agents/team_agent/sop/tool.py:146-150`). If a retry/replan creates another approval for the same key, an old approval can transition the newer plan. This is a direct consequence of the same non-versioned store contract and violates pause/replan/recovery semantics.

### P0 / High - approval idempotency is not atomic and response ownership is not checked

The response route reads a pending approval and then updates it in separate operations (`src/api/routes/human.py:256-285`). `ApprovalStorage.update_status` updates by `_id` only, without `status: pending` or an atomic `find_one_and_update` (`src/infra/storage/mongodb.py:242-254`). Two concurrent responses can both pass the pending check and overwrite the stored decision; each request also publishes its own decision (`human.py:287-296`). The existing SOP idempotency tests cover only a request arriving after a completed write, not concurrent responses (`tests/api/test_sop_approval_idempotent.py:13-66`).

Additionally, `respond_to_approval` has only the permission dependency and no current-user parameter or owner/session check (`src/api/routes/human.py:245-250`). Any user with `chat:write` who obtains/guesses an approval id can approve or reject another user's SOP. That is incompatible with a user confirmation gate.

### P0 / High - the feature flag defaults on, contrary to the planned compatibility default

Both the runtime setting and the persisted setting definition default `TEAM_SOP_MODE` to `True` (`src/kernel/config/base.py:161-164`; `src/kernel/config/definitions.py:337-343`). The PRD/design/implement contract specifies `false` as the default so legacy Team routing remains unchanged until explicitly enabled (R3/R9). A fresh deployment therefore exposes the new tool and approval flow by default, making the documented rollback/compatibility behavior false.

### P1 / Medium - timeout extension cannot extend the waiting agent

`update_sop` always waits exactly 300 seconds (`src/agents/team_agent/sop/tool.py:25-26,131-144`). The human route can extend the approval's MongoDB expiry (`src/api/routes/human.py:299-317`), but it cannot alter the already-running `wait_for_response` timeout. The agent returns `timed_out` at 300 seconds even when the approval was successfully extended, leaving a pending approval with no waiter. This contradicts R4's “300s default, extendable” behavior.

### P1 / Medium - validation accepts duplicate/empty step identifiers and does not cap configured max steps

Validation builds `steps_by_id` with a dict comprehension (`src/agents/team_agent/sop/schemas.py:116-118`), silently collapsing duplicate `step_id` values; no duplicate check exists. Blank `step_id`, title, plan id, and goal are also accepted by the Pydantic fields. A duplicate ID produces ambiguous dependency edges and `set_step_status` updates only the first matching step (`store.py:119-130`). Separately, the requirement's hard upper bound of 12 is not enforced: `TEAM_SOP_MAX_STEPS` is an unconstrained integer (`src/kernel/config/base.py:163`; `src/kernel/config/definitions.py:345-352`) and is passed directly to validation (`src/agents/team_agent/sop/tool.py:207-212`).

### P1 / Medium - event delivery failures are swallowed, and recovery has no backend resume path

Both `sop:updated` and `approval_required` emission catches every exception, logs a warning, and lets the tool continue (`src/agents/team_agent/sop/tool.py:50-76`). If the dual writer is unavailable, the plan can be persisted while the UI receives no snapshot/approval event; the agent then waits until timeout with no visible confirmation. The presenter whitelist and `emit_team_event` do implement the intended event shape/double-write path (`src/infra/writer/presenter_events.py:420-432`; `src/infra/writer/present.py:224-228`), but the tool treats that required delivery as best effort.

The store exposes only current-snapshot reads and writes (`src/agents/team_agent/sop/store.py:75-140`). There is no SOP-specific history query, approval binding, or resume/reconciliation hook in `team_router_node`; a new Team run starts by constructing the inner graph and does not load an awaiting/running SOP (`src/agents/team_agent/nodes.py:129-137,369-377`). Persisted events can support frontend replay, but backend cross-turn/cross-worker continuation is not implemented by these paths, so PRD R5's recovery promise is incomplete.

## Requirement coverage

| Requirement | Evidence | Assessment |
|---|---|---|
| R1/R2 tool and schema | Tool is mounted only for active Team members when the flag is true (`nodes.py:346-363`); deterministic checks cover dependency existence/cycles, roster, expected output, and max setting (`schemas.py:103-140`). | Partial: role inheritance and duplicate/empty identifiers remain. |
| R3 granularity/config | Prompt renders min/max and validation uses max (`tool.py:207-212`). | Partial: no 12 hard cap; min is advisory only. |
| R4 approval gate | First call persists `awaiting_confirmation`, creates `sop_plan`, waits, and transitions approved/rejected (`tool.py:124-158`). | Fails hard-gating, concurrent idempotency, ownership, and extendable timeout. |
| R5 store/events/history | Whitelist and dual-write wrapper exist; snapshots are persisted (`presenter_events.py:420-432`, `store.py:75-140`). | Partial: lost updates, swallowed delivery failures, no approval binding/resume/history service. |
| R7 guided dispatch | Prompt requires `update_sop` before dispatch (`src/agents/team_agent/sop/prompt_section.py:18-25`) and running-step reminder exists. | Fails as an enforcement contract; guard allows pre-confirmation dispatch and roles inherit the tool. |
| R8 sandbox upload | Team context appends `upload_url_to_sandbox` under the same sandbox flag as Search (`src/agents/team_agent/context.py:27-46`). | Covered by implementation; no finding. |
| R9 compatibility flag | Runtime and setting-definition defaults are both `True` (`base.py:162`, `definitions.py:337-343`). | Fails planned default-off compatibility. |

## Verification

`python -m pytest -q tests/agents/test_sop_schemas.py tests/agents/test_sop_store.py tests/agents/test_sop_tool_gate.py tests/agents/test_sop_dispatch_guard.py tests/agents/test_team_agent_sop_tool_hook.py tests/api/test_sop_approval_idempotent.py tests/infra/test_sop_presenter_events.py` passed: **51 passed**, with two unrelated Pydantic deprecation warnings. The suite does not cover concurrent store writes, stale approvals, concurrent response races, approval ownership, timeout extension, child-agent tool visibility, or backend resume.

## External references

- `deepagents==0.6.7`, `.venv/Lib/site-packages/deepagents/graph.py:634-645`: subagents inherit parent `tools` unless they explicitly declare their own.
- Existing task research: `research/codebase-current-state.md`, `research/review-write-todos-removal.md`, `research/sop-design-recommendations.md`.

## Related specs

- `.trellis/spec/backend/agent-harness.md` - model-visible tool/profile boundaries.
- `.trellis/spec/backend/database-guidelines.md` - storage abstraction and async Mongo conventions.
- `.trellis/spec/backend/error-handling.md` - route-boundary error handling and cleanup.
- `.trellis/spec/backend/quality-guidelines.md` - type safety, route security, and async reliability.
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/prd.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/design.md`
- `.trellis/tasks/08-04-teamagent-sop-dag-harness/implement.md`

## Caveats / Not Found

- This is a read-only audit; no product or test files were changed.
- The event history transport itself is outside the requested backend SOP modules; the finding is specifically that these SOP paths do not bind/reconcile persisted snapshots on a resumed Team run.
- The existing tests intentionally assert permissive pre-confirmation dispatch (`test_sop_dispatch_guard.py:84-97`), so their green result is not evidence of the PRD's zero-dispatch acceptance criterion.
