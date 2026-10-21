import type {
  ScheduledTaskDraftAction,
  ScheduledTaskDraftChangeKey,
  ScheduledTaskDraftPersona,
  ScheduledTaskDraftSummary,
  ScheduledTaskDraftToolResult,
} from "../../../../types/scheduledTask";

const DRAFT_TOOL_NAME = "draft_scheduled_task";

const CHANGE_KEYS: readonly ScheduledTaskDraftChangeKey[] = [
  "name",
  "prompt",
  "schedule",
  "persona",
  "enabled",
];

/** 草稿改动字段 → 中文标签（兜底文案；组件内用 i18n 覆盖）。 */
export const DRAFT_CHANGE_LABELS: Record<ScheduledTaskDraftChangeKey, string> =
  {
    name: "名称",
    prompt: "要做的事",
    schedule: "执行时间",
    persona: "执行专家",
    enabled: "启用状态",
  };

type ToolResult = string | Record<string, unknown> | undefined;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isScheduledTaskDraftToolName(name: string | undefined): boolean {
  if (!name) return false;
  return name === DRAFT_TOOL_NAME || name.endsWith(`:${DRAFT_TOOL_NAME}`);
}

function normalizePersona(value: unknown): ScheduledTaskDraftPersona | null {
  if (!isRecord(value)) return null;
  const name = typeof value.name === "string" ? value.name : "";
  const id = typeof value.id === "string" ? value.id : "";
  if (!id && !name) return null;
  return {
    id,
    name,
    avatar: typeof value.avatar === "string" ? value.avatar : null,
  };
}

export function normalizeDraftSummary(
  value: unknown,
): ScheduledTaskDraftSummary | null {
  if (!isRecord(value)) return null;
  return {
    name: typeof value.name === "string" ? value.name : "",
    prompt: typeof value.prompt === "string" ? value.prompt : "",
    schedule_text:
      typeof value.schedule_text === "string" ? value.schedule_text : "",
    next_runs: Array.isArray(value.next_runs)
      ? value.next_runs.filter((x): x is string => typeof x === "string")
      : [],
    persona: normalizePersona(value.persona),
    enabled: value.enabled !== false,
  };
}

function normalizeChanges(value: unknown): ScheduledTaskDraftChangeKey[] {
  if (!Array.isArray(value)) return [];
  return value.filter((x): x is ScheduledTaskDraftChangeKey =>
    CHANGE_KEYS.includes(x as ScheduledTaskDraftChangeKey),
  );
}

function toRecord(result: ToolResult): Record<string, unknown> | null {
  if (typeof result === "string") {
    const text = result.trim();
    if (!text.startsWith("{")) return null;
    try {
      const parsed: unknown = JSON.parse(text);
      return isRecord(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }
  return isRecord(result) ? result : null;
}

export function parseScheduledTaskDraftResult(
  result: ToolResult,
): ScheduledTaskDraftToolResult | null {
  const payload = toRecord(result);
  if (!payload) return null;
  if (payload.entity_type !== "scheduled_task_draft") return null;
  if (typeof payload.draft_id !== "string" || !payload.draft_id) return null;

  const summary = normalizeDraftSummary(payload.summary);
  if (!summary) return null;

  const action: ScheduledTaskDraftAction =
    payload.action === "update" ? "update" : "create";

  return {
    entity_type: "scheduled_task_draft",
    draft_id: payload.draft_id,
    action,
    task_id: typeof payload.task_id === "string" ? payload.task_id : null,
    summary,
    before: normalizeDraftSummary(payload.before),
    changes: normalizeChanges(payload.changes),
    expires_at:
      typeof payload.expires_at === "string" ? payload.expires_at : null,
    now: typeof payload.now === "string" ? payload.now : null,
    message: typeof payload.message === "string" ? payload.message : null,
  };
}

export function describeDraftChanges(
  changes: readonly string[] | null | undefined,
): string[] {
  if (!changes) return [];
  const labels: string[] = [];
  for (const key of changes) {
    const label = DRAFT_CHANGE_LABELS[key as ScheduledTaskDraftChangeKey];
    if (label && !labels.includes(label)) labels.push(label);
  }
  return labels;
}
