# Smooth TeamAgent Harness Design

## Architecture

Keep the existing two-layer topology, but add deterministic preflight and
execution contracts around the inner DeepAgents router:

```text
chat request
  -> TeamAgent outer lifecycle
  -> acquire shared sandbox
  -> materialize attachments and persist AttachmentManifest
  -> compile validated TeamRoster
  -> planner produces typed TeamPlan
  -> emit plan + create durable approval
  -> wait for confirm / reject
  -> confirmed plan becomes immutable ApprovedTeamPlan
  -> inner DeepAgents router executes only guarded TeamHandoffs
  -> validate role results/artifacts
  -> synthesize or report recoverable partial failure
```

The outer application layer continues to own request/session/task/SSE and
cancellation. The inner `create_deep_agent` graph remains the router and
synthesizer. Role members remain DeepAgents subagents, but their invocation is
guarded by application-owned plan and handoff schemas.

## Contracts

### AttachmentManifest

Each attachment record contains `attachment_id`, storage `key`, original name,
MIME type, size, collision-safe absolute `sandbox_path`, status, and structured
error fields. The materializer validates ownership/existence, downloads bytes
from configured storage by key, uploads them through the concrete shared
sandbox backend, verifies all upload responses, and persists the manifest.

Materialization runs after `get_or_create()` yields the real provider work
directory and before any planner/model invocation. Stable destinations live
under `<work_dir>/attachments/<attachment-id>/<safe-name>`. Retries verify and
reuse an existing matching target.

`upload_url_to_sandbox` is registered for TeamAgent as an on-demand external
URL capability, but it is not the authoritative user-attachment path.

### TeamRoster

Compile one immutable roster snapshot per plan. Sort enabled members by
`position` then stable member id, reject duplicate generated subagent names,
record the selected default member and persona snapshot/version, and explicitly
control whether `general-purpose` exists. Explicit teams must not receive an
implicit DeepAgents default role.

### TeamPlan

The planner returns a typed schema containing `plan_id`, `team_run_id`, summary,
attachment manifest reference, ordered steps, dependency edges, selected
subagent type/member/persona snapshot, objective, required inputs, expected
artifacts, and completion criteria. Validation rejects unknown roles, cyclic or
missing dependencies, attachment references that are not materialized, and
plans with no executable or direct-answer outcome.

Plans requiring role delegation emit a typed plan event and create a durable
approval using the existing human approval service. Approval responses are
idempotent. Rejection may carry feedback and returns to planning without
starting role work. Simple direct answers may skip approval.

### ApprovedTeamPlan and TeamHandoff

Confirmation freezes a plan version. Every role invocation uses a structured
handoff envelope with `handoff_id`, `plan_id`, `team_run_id`, `step_id`,
`subagent_type`, objective, context, verified attachment paths, predecessor
artifacts, expected artifacts, and attempt number.

DeepAgents 0.6.7 exposes only `task(description, subagent_type)`. For initial
compatibility, serialize the validated envelope into `description` and enforce
it with an application middleware/guard before the stock task callable runs.
The guard rejects task calls when no approved plan is present, the step/role is
not in that plan, dependencies are incomplete, or attachment paths are not
verified. Role results use a typed response format when supported and are
validated before a step becomes complete.

## State And Persistence

Do not rely on the stateless outer LangGraph state or process memory. Persist
the materialized manifest, roster snapshot, plan/version, approval identity and
decision, step attempts/results, and terminal status in application-owned run
metadata/events. The inner DeepAgents checkpointer continues to own conversation
messages and must not share a checkpoint namespace with new orchestration state.

The existing approval wait path can pause a running task. Recovery must reload
the persisted plan/approval decision and materialization manifest so rerunning
preflight is idempotent and cannot create a second approval or duplicate files.

## Events And Frontend

Add or extend typed events for plan proposed, awaiting confirmation, approved,
rejected, step started/progress/succeeded/failed/retrying, synthesis, partial
failure, cancellation, and completion. Include `team_run_id`, `plan_id`,
`step_id`, `handoff_id`, team/member/subagent/persona identifiers, attempt, and
run id. Do not infer identity solely from `checkpoint_ns`.

Reuse the existing ApprovalPanel interaction for approve/reject and feedback,
but render a TeamPlan-specific view with steps, roles, dependencies, attachment
availability, and expected outputs. Update status/history/reconnect logic so an
awaiting-confirmation run is visible and resumable rather than treated as a
generic running or completed trace.

## Failure And Recovery

- Attachment download/upload/verification failure blocks planning and emits a
  per-file actionable error.
- Rejection starts no role work and preserves feedback for a replacement plan.
- A step failure is recorded with attempt and dependency impact; independent
  completed steps remain available for retry.
- Required failed or missing steps prevent a success terminal state and final
  synthesis must label partial results.
- Cancellation stops pending/running step work and never changes an approved
  plan into completed.
- Recovery reuses the immutable plan version and completed results, and retries
  only eligible incomplete steps unless the user requests replanning.

## Compatibility And Rollback

The feature is TeamAgent-scoped. FastAgent/SearchAgent request and attachment
behavior remain unchanged. Existing TeamAgent requests that need no delegation
retain a direct-answer path. Keep compatibility with locked DeepAgents 0.6.7;
assert the final compiled task roster so dependency behavior cannot silently
change.

Rollout behind a TeamAgent harness option/default that can restore the previous
router path without deleting persisted manifests/plans. Schema additions must
be additive and old events remain readable.

## Key Trade-offs

- Application-owned schemas and guards add code, but convert prompt guidance
  into executable contracts.
- Reusing the existing approval service minimizes frontend/API duplication;
  typed plan metadata and idempotent recovery are still required.
- Serializing structured input through stock `task.description` is a temporary
  compatibility bridge. A custom typed task tool can replace it later without
  changing the application handoff schema.
