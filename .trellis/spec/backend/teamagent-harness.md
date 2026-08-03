# TeamAgent Complex-Task Harness

## 1. Scope / Trigger

Use this contract when changing TeamAgent attachments, team-member compilation,
DeepAgents `task` delegation, plan approval, role-result validation, TeamAgent
events, or recovery state. TeamAgent is an application `BaseGraphAgent` wrapper
around an inner `create_deep_agent` graph; the application layer owns the hard
contracts described here.

## 2. Signatures

```python
async def materialize_attachments(
    attachments: Sequence[Any],
    *,
    backend: Any,
    work_dir: str,
    user_id: str,
) -> AttachmentManifest: ...

def compile_team_roster(
    team: Any,
    *,
    persona_snapshots: Mapping[str, Any] | None = None,
    include_general_purpose: bool = False,
) -> TeamRoster: ...

def build_team_plan(...) -> TeamPlan: ...
def validate_handoff_for_plan(handoff: TeamHandoff, plan: TeamPlan) -> TeamPlanStep: ...
async def request_team_approval(...) -> tuple[TeamPlan, str | None]: ...
```

The stock DeepAgents 0.6.7 task signature remains
`task(description: str, subagent_type: str)`. TeamAgent serializes a validated
`{"team_handoff": ...}` JSON envelope into `description` and installs
`TeamTaskGuardMiddleware` to validate it before dispatch.

## 3. Contracts

### Attachment manifest

- Acquire the real sandbox `(CompositeBackend, work_dir)` first.
- Validate the authenticated user's file record, download by storage `key`, and
  upload through the concrete backend to
  `<work_dir>/attachments/<attachment-id>/<safe-name>`.
- `AttachmentManifestItem` carries `attachment_id`, `key`, `name`, `mime_type`,
  `size`, `sandbox_path`, `status`, and structured `error`.
- Any requested attachment that is not `materialized` blocks planning and role
  work. URL-only fallback is forbidden.
- Retrying verifies/reuses the stable path; every upload response and returned
  path must match the request.
- `upload_url_to_sandbox` is registered on the TeamAgent main and inherited
  role tool roster for external URLs only; it does not replace preflight.

### Roster, plan, and handoff

- Compile enabled members once in `(position, member_id)` order. Reject missing
  ids, duplicate generated subagent types, invalid defaults, and the reserved
  `general-purpose` name.
- Explicit teams use `include_general_purpose=False`; tests must assert the
  effective DeepAgents roster because 0.6.7 can otherwise inject the generic
  role.
- Freeze the roster/persona snapshot in `TeamPlan`. A plan validates role names,
  attachment ids, dependency ids, self-dependencies, cycles, and materialized
  attachments.
- A plan with any delegated step requires a durable `team_plan` approval.
  Reject, timeout, or cancellation dispatches zero task calls. Repeated
  responses return the stored decision idempotently.
- A `TeamHandoff` carries `handoff_id`, `plan_id`, `team_run_id`, `step_id`,
  `subagent_type`, objective/context, exact verified attachment paths,
  predecessor/expected artifacts, and attempt.
- The guard rejects unapproved plans, unknown role/step, incomplete
  predecessors, missing or extra attachment paths, malformed envelopes, and
  mismatched role results. A `ToolMessage(status="error")` is failure even if
  it has content.

### Persistence and events

- `TeamRunStore` persists the immutable plan, approval id/state, step results,
  attempts, resume origin, and run status independently from the inner message
  checkpointer.
- Run states include `planning`, `awaiting_confirmation`, `approved`,
  `rejected`, `running`, `partial_failure`, `cancelled`, `completed`, `failed`.
  Completion is valid only after every required step result succeeds.
- Presenter event types are `approval_required` (`approval_type="team_plan"`),
  `team:plan`, `team:step`, and `team:run`. Payloads carry `plan_id`,
  `team_run_id`, and step/handoff/role identifiers where applicable.

## 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| No concrete sandbox with attachments | Fail preflight; never send URL-only input to roles |
| File not owned/missing/download fails | Manifest item `failed` with stage/reason; block plan |
| Upload error or returned-path mismatch | Fail verification; block plan |
| Duplicate/reserved role type | Reject roster before `create_deep_agent` |
| Unknown/cyclic plan dependency | Pydantic plan validation fails |
| Delegated plan unapproved/rejected/timed out | Zero task dispatch; persist terminal decision |
| Handoff predecessor incomplete | Guard rejects task and emits no step success |
| Handoff path set differs from approved manifest | Guard rejects task |
| ToolMessage has `status="error"` | Persist failed step; dependants remain blocked |
| Required step fails | Run is `partial_failure`/`failed`, never `completed` |
| Duplicate team-plan approval response | Return durable prior decision idempotently |

## 5. Good / Base / Bad Cases

- Good: two files materialize once, a typed plan is approved, independent role
  steps run, dependent steps wait, and correlated results unlock synthesis.
- Base: a simple direct-answer plan has no delegated step and skips approval.
- Bad: tell the router in a prompt to upload files/ask for approval, then expose
  the stock `task` tool without an application guard.
- Bad: trust `checkpoint_ns` alone for concurrent handoff correlation or treat
  non-empty error ToolMessages as success.

## 6. Tests Required

- Materializer tests: ownership, safe provider path, idempotency, response/path
  verification, missing sandbox, per-stage structured errors.
- Roster tests: stable ordering, persona snapshots, invalid default, duplicate
  and implicit-general-purpose rejection/effective roster.
- Plan/guard tests: reference/cycle validation, zero calls before approval,
  rejection/timeout/idempotency, exact path set, dependency blocking, error
  ToolMessage behavior, step/run persistence.
- Integration test: attachment -> plan -> approve -> independent/dependent role
  calls -> role result validation -> synthesis/partial failure/recovery.
- Run the existing `tests/agents` suite plus Ruff and mypy on changed modules.

## 7. Wrong vs Correct

```python
# Wrong: prompt-only policy and URL metadata.
inner_graph = create_deep_agent(subagents=roles)

# Correct: deterministic preflight and guarded task transport.
manifest = await materialize_attachments(
    attachments, backend=backend, work_dir=work_dir, user_id=user_id
)
roster = compile_team_roster(team, include_general_purpose=False)
plan = build_team_plan(attachment_manifest=manifest, roster=roster, ...)
approved_plan, approval_id = await request_team_approval(plan=plan, ...)
middleware.append(TeamTaskGuardMiddleware(plan=approved_plan, ...))
```
