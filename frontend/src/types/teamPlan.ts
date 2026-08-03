/** Typed TeamAgent planning and execution events. */

export type TeamPlanStatus =
  | "proposed"
  | "awaiting_confirmation"
  | "approved"
  | "rejected"
  | "planning"
  | "running"
  | "synthesizing"
  | "partial_failure"
  | "completed"
  | "cancelled"
  | "failed";

export type TeamStepStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "retrying"
  | "cancelled"
  | "skipped";

export type AttachmentMaterializationStatus =
  | "pending"
  | "materializing"
  | "materialized"
  | "failed";

export interface TeamPlanAttachment {
  attachment_id: string;
  key?: string;
  name: string;
  mime_type?: string;
  size?: number;
  sandbox_path?: string;
  status: AttachmentMaterializationStatus;
  error?: { code?: string; message: string; retryable?: boolean } | null;
}

export interface TeamPlanStep {
  step_id: string;
  order: number;
  objective: string;
  subagent_type?: string;
  member_id?: string;
  member_name?: string;
  persona_id?: string;
  persona_version?: string;
  dependencies: string[];
  required_inputs: string[];
  expected_artifacts: string[];
  completion_criteria?: string[];
  status: TeamStepStatus;
  attempt?: number;
  error?: string | null;
  output?: string | null;
}

export interface TeamPlanState {
  plan_id: string;
  team_run_id?: string;
  approval_id?: string;
  version?: number;
  summary: string;
  status: TeamPlanStatus;
  rejection_feedback?: string | null;
  attachments: TeamPlanAttachment[];
  steps: TeamPlanStep[];
  updated_at?: string;
}

export interface TeamPlanEvent {
  event_type: "team:plan" | "team:step" | "team:run" | "approval_required";
  plan?: Partial<TeamPlanState> & { plan_id?: string };
  step?: Partial<TeamPlanStep> & { step_id?: string };
  status?: string;
  plan_id?: string;
  team_run_id?: string;
  approval_id?: string;
  rejection_feedback?: string | null;
  timestamp?: string;
}

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};

const asString = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value : undefined;

const asStringArray = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];

const PLAN_STATUSES = new Set<TeamPlanStatus>([
  "proposed", "awaiting_confirmation", "approved", "rejected", "planning",
  "running", "synthesizing", "partial_failure", "completed", "cancelled", "failed",
]);
const STEP_STATUSES = new Set<TeamStepStatus>([
  "queued", "running", "succeeded", "failed", "retrying", "cancelled", "skipped",
]);

function normalizeAttachment(value: unknown): TeamPlanAttachment | null {
  const item = asRecord(value);
  const attachmentId = asString(item.attachment_id) ?? asString(item.id);
  const name = asString(item.name) ?? attachmentId;
  if (!attachmentId || !name) return null;
  const status = asString(item.status);
  return {
    attachment_id: attachmentId,
    key: asString(item.key),
    name,
    mime_type: asString(item.mime_type) ?? asString(item.mimeType),
    size: typeof item.size === "number" ? item.size : undefined,
    sandbox_path: asString(item.sandbox_path) ?? asString(item.path),
    status:
      status === "materialized" || status === "materializing" || status === "failed"
        ? status
        : "pending",
    error:
      item.error && typeof item.error === "object"
        ? {
            code: asString(asRecord(item.error).code),
            message:
              asString(asRecord(item.error).message) ??
              asString(asRecord(item.error).reason) ??
              "Materialization failed",
            retryable: asRecord(item.error).retryable === true,
          }
        : asString(item.error)
          ? { message: asString(item.error)! }
          : null,
  };
}

function normalizeStep(value: unknown, index = 0): TeamPlanStep | null {
  const item = asRecord(value);
  const stepId = asString(item.step_id) ?? asString(item.id);
  if (!stepId) return null;
  const status = asString(item.status);
  return {
    step_id: stepId,
    order:
      typeof item.order === "number"
        ? item.order
        : typeof item.ordinal === "number"
          ? item.ordinal + 1
          : index + 1,
    objective: asString(item.objective) ?? "",
    subagent_type: asString(item.subagent_type),
    member_id: asString(item.member_id),
    member_name: asString(item.member_name),
    persona_id: asString(item.persona_id),
    persona_version: asString(item.persona_version),
    dependencies: asStringArray(item.dependencies),
    required_inputs: asStringArray(item.required_inputs),
    expected_artifacts: asStringArray(item.expected_artifacts).length
      ? asStringArray(item.expected_artifacts)
      : asStringArray(item.required_artifacts),
    completion_criteria:
      asStringArray(item.completion_criteria).length > 0
        ? asStringArray(item.completion_criteria)
        : asString(item.expected_completion)
          ? [asString(item.expected_completion)!]
          : [],
    status: status && STEP_STATUSES.has(status as TeamStepStatus)
      ? (status as TeamStepStatus)
      : "queued",
    attempt: typeof item.attempt === "number" ? item.attempt : undefined,
    error: asString(item.error) ?? null,
    output: asString(item.output) ?? null,
  };
}

/** Convert a backend event's nested or flat payload into a stable event shape. */
export function normalizeTeamPlanEvent(
  eventType: TeamPlanEvent["event_type"],
  payload: unknown,
): TeamPlanEvent {
  const data = asRecord(payload);
  const nestedPlan = asRecord(data.plan);
  const nestedStep = asRecord(data.step);
  const planId =
    asString(data.plan_id) ?? asString(nestedPlan.plan_id) ?? asString(nestedPlan.id);
  const rawStatus = asString(data.status) ?? asString(nestedPlan.status);
  const status = rawStatus && PLAN_STATUSES.has(rawStatus as TeamPlanStatus)
    ? (rawStatus as TeamPlanStatus)
    : undefined;
  const attachmentManifest = asRecord(nestedPlan.attachment_manifest);
  const attachmentValues = Array.isArray(nestedPlan.attachments)
    ? nestedPlan.attachments
    : Array.isArray(attachmentManifest.attachments)
      ? attachmentManifest.attachments
    : Array.isArray(data.attachments)
      ? data.attachments
      : null;
  const stepValues = Array.isArray(nestedPlan.steps)
    ? nestedPlan.steps
    : Array.isArray(data.steps)
      ? data.steps
      : null;
  const plan: Partial<TeamPlanState> = {
    plan_id: planId,
    team_run_id: asString(data.team_run_id) ?? asString(nestedPlan.team_run_id),
    approval_id:
      asString(data.approval_id) ??
      (eventType === "approval_required" ? asString(data.id) : undefined) ??
      asString(nestedPlan.approval_id),
    version: typeof nestedPlan.version === "number" ? nestedPlan.version : undefined,
    summary: asString(nestedPlan.summary) ?? asString(data.summary),
    status,
    rejection_feedback:
      asString(data.rejection_feedback) ?? asString(data.feedback) ??
      asString(nestedPlan.rejection_feedback) ?? asString(nestedPlan.feedback) ?? null,
    attachments: attachmentValues
      ? attachmentValues
          .map(normalizeAttachment)
          .filter((x): x is TeamPlanAttachment => Boolean(x))
      : undefined,
    steps: stepValues
      ? stepValues
          .map(normalizeStep)
          .filter((x): x is TeamPlanStep => Boolean(x))
      : undefined,
  };
  const step = normalizeStep(
    Object.keys(nestedStep).length > 0 ? { ...nestedStep, status: status ?? nestedStep.status } : data,
  );
  return {
    event_type: eventType,
    plan: planId || Object.keys(nestedPlan).length > 0 ? plan : undefined,
    step: step ?? undefined,
    status,
    plan_id: planId,
    team_run_id: asString(data.team_run_id),
    approval_id:
      asString(data.approval_id) ??
      (eventType === "approval_required" ? asString(data.id) : undefined),
    rejection_feedback: asString(data.rejection_feedback) ?? asString(data.feedback) ?? null,
    timestamp: asString(data.timestamp),
  };
}

/** Apply a plan/step event without losing fields from an earlier event. */
export function reduceTeamPlan(
  current: TeamPlanState | null,
  event: TeamPlanEvent,
): TeamPlanState | null {
  const incoming = event.plan;
  const planId = event.plan_id ?? incoming?.plan_id ?? current?.plan_id;
  if (!planId && !current) return null;
  if (!planId) return current;
  const base: TeamPlanState = current ?? {
    plan_id: planId,
    summary: "",
    status: "proposed",
    attachments: [],
    steps: [],
  };
  const status =
    (event.status as TeamPlanStatus | undefined) ?? incoming?.status ?? base.status;
  const next: TeamPlanState = {
    ...base,
    plan_id: planId,
    team_run_id: event.team_run_id ?? incoming?.team_run_id ?? base.team_run_id,
    approval_id: event.approval_id ?? incoming?.approval_id ?? base.approval_id,
    status,
    rejection_feedback:
      event.rejection_feedback ?? incoming?.rejection_feedback ?? base.rejection_feedback,
    updated_at: event.timestamp ?? base.updated_at,
  };
  if (incoming) {
    if (incoming.summary !== undefined) next.summary = incoming.summary;
    if (incoming.version !== undefined) next.version = incoming.version;
    if (incoming.attachments !== undefined) next.attachments = incoming.attachments;
    if (incoming.steps !== undefined) next.steps = incoming.steps;
  }
  if (event.step?.step_id) {
    const step = event.step as TeamPlanStep;
    const index = next.steps.findIndex((item) => item.step_id === step.step_id);
    next.steps =
      index < 0
        ? [...next.steps, { ...step, objective: step.objective ?? "", order: step.order ?? next.steps.length + 1, dependencies: step.dependencies ?? [], required_inputs: step.required_inputs ?? [], expected_artifacts: step.expected_artifacts ?? [], status: step.status ?? "queued" }]
        : next.steps.map((item, itemIndex) => (itemIndex === index ? { ...item, ...step } : item));
    next.steps.sort((a, b) => a.order - b.order);
  }
  return next;
}
