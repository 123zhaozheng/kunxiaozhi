# TeamAgent Plan Confirmation UI

## 1. Scope / Trigger

Use this contract when changing TeamAgent plan/step/run events, chat history
rehydration, approval routing, reconnect state, or the TeamPlan panel.

## 2. Signatures

```typescript
type TeamPlanEventType =
  | "team:plan"
  | "team:step"
  | "team:run"
  | "approval_required";

function normalizeTeamPlanEvent(
  eventType: TeamPlanEventType,
  payload: unknown,
): TeamPlanEvent | null;

function reduceTeamPlan(
  current: TeamPlanState | null,
  event: TeamPlanEvent,
): TeamPlanState | null;
```

Approvals use the existing human response API and carry
`approval_type="team_plan"`, `approval_id` (or legacy event `id`), `plan_id`,
`team_run_id`, and nested `plan`.

## 3. Contracts

- Normalize additive `team:plan`, `team:step`, and `team:run` payloads plus
  `approval_required(type=team_plan)` into one `TeamPlanState`.
- Accept backend field aliases deliberately: `attachment_manifest.attachments`,
  `ordinal`, `required_artifacts`, `error.reason`, and approval event `id`.
- Unknown plan/step statuses normalize to safe nonterminal defaults; never cast
  arbitrary strings into union types.
- A team-plan approval is rendered only by `TeamPlanPanel`; suppress the generic
  `ApprovalPanel` in both live handling and history rehydration.
- Preserve reject feedback and terminal `rejected`, `partial_failure`,
  `cancelled`, `failed`, and `completed` states.
- Reconnect/history reconstruction must keep `awaiting_confirmation` actionable
  and must not insert plan events as assistant message content.
- The panel shows ordered steps, roles, dependencies, expected outputs,
  materialized/failed attachments, status, and approve/reject-with-feedback.

## 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| `approval_required` is not `team_plan` | Keep existing generic approval flow |
| Team-plan approval lacks usable id | Render state but disable/avoid invalid response request |
| Backend sends unknown status | Normalize to safe default; do not crash |
| Backend sends `error.reason` | Display it as the attachment/step error |
| History contains team-plan approval | Rehydrate TeamPlan only; no duplicate generic panel |
| Reject includes `feedback` | Preserve as `rejection_feedback` |
| Reconnect while awaiting confirmation | Restore actionable TeamPlan panel |

## 5. Good / Base / Bad Cases

- Good: live or historical plan events reduce to the same panel state; the user
  sees attachments/dependencies and responds once with the correct approval id.
- Base: ordinary form/confirm approvals continue through `ApprovalPanel`.
- Bad: render both panels for the same approval or append `team:plan` JSON as a
  chat message.

## 6. Tests Required

- Normalizer/reducer tests for nested and flat events, backend aliases, unknown
  statuses, approval id fallback, rejection feedback, and partial failure.
- Event-handler/history tests proving team-plan approvals do not enter the
  generic approval/message paths.
- UI tests for ordered steps, dependency/attachment errors, approve, reject
  feedback, disabled missing-id behavior, and terminal states.
- Run frontend ESLint, TypeScript build, targeted event/history tests, and the
  production build.

## 7. Wrong vs Correct

```typescript
// Wrong: duplicate team-plan and generic approval controls.
setPendingApproval(payload);
setTeamPlan(normalizeTeamPlanEvent("approval_required", payload));

// Correct: route team plans exclusively to typed plan state.
if (payload.approval_type === "team_plan") {
  updateTeamPlan(normalizeTeamPlanEvent("approval_required", payload));
} else {
  setPendingApproval(payload);
}
```
