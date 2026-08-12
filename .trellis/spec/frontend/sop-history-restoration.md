# SOP History Restoration

## 1. Scope / Trigger

This contract applies when TeamAgent SOP state is rebuilt from persisted session events. SOP history contains two full-snapshot event shapes with different authority:

- `sop:updated` records the plan's evolving execution state.
- `approval_required` records an approval envelope and embeds the plan snapshot that existed when approval was requested.

The embedded approval snapshot is immutable historical context. It is not a later state update merely because approval metadata must be restored.

## 2. Signatures

Frontend restoration entry point:

```ts
restoreSopPlanFromHistory(events: AgentEvent[]): SopPlan | null
```

Relevant persisted event signatures:

```ts
type SopUpdatedEvent = {
  event_type: "sop:updated";
  data: SopPlanPayload;
};

type SopApprovalEvent = {
  event_type: "approval_required";
  data: {
    id: string;
    type?: "sop_plan";
    approval_type?: "sop_plan";
    plan_id?: string;
    plan: SopPlanPayload;
    expires_at?: string;
  };
};
```

No backend API, Mongo schema, or event payload migration is part of this frontend contract.

## 3. Contracts

- Sort replay candidates with the shared canonical history comparator, including `history_order`; use original input order as the stable final tie-breaker.
- Normalize the latest eligible SOP replay event as the authoritative plan snapshot.
- The authoritative snapshot owns plan status and every step field, including status, output, error, dependencies, and assignee.
- A same-plan approval envelope may add `approval_id` and supported expiry metadata only.
- Match approval metadata using consistent explicit and nested `plan_id` values. Conflicting identifiers fail closed and attach nothing.
- An approval-only history may use its embedded plan as the authoritative snapshot because no later snapshot is available.
- Live SSE handling continues to use `reduceSop` as a chronological full-snapshot reducer. Do not change that reducer to implement history-only metadata merging.

## 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| No valid SOP replay event | Return `null` |
| Latest event has an invalid plan payload | Return the last plan produced by the selected normalization behavior; do not synthesize fields |
| Approval `plan_id` differs from restored plan | Ignore approval metadata |
| Approval explicit `plan_id` conflicts with nested `plan.plan_id` | Ignore approval metadata |
| Matching approval has no valid identifier | Preserve the authoritative snapshot unchanged |
| History is incomplete or terminal event was not returned | Restore only from received events; the helper must not claim reconciliation with `sop_runs` |

## 5. Good / Base / Bad Cases

- Good: pending approval followed by completed `sop:updated` restores `completed` with succeeded steps and retains the matching `approval_id`.
- Base: approval-only history restores the embedded awaiting-confirmation plan and its approval metadata.
- Bad: an approval for another plan, or an envelope with conflicting plan identifiers, must not contribute metadata.
- Partial: if pagination omits the completed event, the helper can only show the latest received snapshot; incomplete-history UI remains responsible for signaling partial data.

## 6. Tests Required

- Earlier pending approval plus later completed update, with reversed input array order: assert completed plan, succeeded steps, and matching approval ID.
- Canonical `history_order` versus conflicting timestamps: assert the comparator-selected snapshot wins.
- Approval-only history: assert the embedded plan remains renderable.
- Different-plan and conflicting-plan approval envelopes: assert no metadata leaks to the selected plan.
- Existing live SOP reducer/event tests: assert chronological SSE behavior remains unchanged.
- Backend producer contract: assert completion emits `sop:updated` with completed plan status and succeeded step statuses.

## 7. Wrong vs Correct

### Wrong

```ts
let restored = reduceSop(null, latestSnapshot);
restored = reduceSop(restored, oldApprovalEvent);
```

The second full reduction replaces current status and steps with the old snapshot embedded in the approval envelope.

### Correct

```ts
const restored = reduceSop(null, latestSnapshot);
return approvalMatches(restored, approval)
  ? { ...restored, approval_id: approval.data.id }
  : restored;
```

History restoration preserves the latest state snapshot and merges only approval-envelope metadata.
