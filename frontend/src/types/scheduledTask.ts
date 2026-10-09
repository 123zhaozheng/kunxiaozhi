export type ScheduleKind =
  | "daily"
  | "weekdays"
  | "weekly"
  | "monthly"
  | "once"
  | "interval";

export type ScheduledTaskStatus = "active" | "paused" | "completed";

export type PausedBy = "user" | "admin" | "system";

export type CreatedVia = "web" | "chat";

export type ScheduledTaskAgentId = "fast" | "search";

export type RunStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "timeout"
  | "skipped";

export interface ScheduleSpec {
  kind: ScheduleKind;
  time?: string | null;
  weekdays?: number[] | null;
  month_day?: number | null;
  run_at?: string | null;
  every_hours?: number | null;
}

export interface ScheduledTask {
  id: string;
  name: string;
  prompt: string;
  schedule: ScheduleSpec;
  schedule_text: string;
  timezone: string;
  agent_id: string;
  persona_preset_id?: string | null;
  persona_name?: string | null;
  persona_avatar?: string | null;
  persona_available?: boolean | null;
  model_id?: string | null;
  enabled: boolean;
  status: ScheduledTaskStatus;
  pause_reason?: string | null;
  paused_by?: PausedBy | null;
  created_via?: CreatedVia;
  max_retries: number;
  timeout_seconds: number;
  template_id?: string | null;
  consecutive_failures: number;
  total_runs: number;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_run_status?: RunStatus | null;
  last_run_id?: string | null;
  last_session_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ScheduledTaskInput {
  name: string;
  prompt: string;
  schedule: ScheduleSpec;
  timezone?: string;
  agent_id?: ScheduledTaskAgentId;
  persona_preset_id?: string | null;
  model_id?: string | null;
  enabled?: boolean;
  max_retries?: number;
  timeout_seconds?: number;
  template_id?: string | null;
}

export interface ScheduledTaskListResponse {
  items: ScheduledTask[];
  total: number;
  limit: number;
  min_interval_hours: number;
}

export interface TaskRun {
  id: string;
  task_id: string;
  task_name: string;
  trigger: "schedule" | "manual";
  status: RunStatus;
  session_id?: string | null;
  attempt: number;
  result_preview?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
}

export interface TaskRunListResponse {
  items: TaskRun[];
  total: number;
}

export interface SchedulePreview {
  valid: boolean;
  schedule_text: string;
  next_runs: string[];
  error?: string | null;
}

export interface ScheduledTaskTemplate {
  id: string;
  title: string;
  description: string;
  icon: string;
  name: string;
  prompt: string;
  schedule: ScheduleSpec;
}

export interface ScheduledTaskRunFinishedNotification {
  type: "scheduled_task:run_finished";
  data: {
    task_id: string;
    task_name: string;
    run_id: string;
    status: RunStatus;
    session_id: string;
    schedule_text: string;
    result_preview?: string | null;
    error_message?: string | null;
    paused_reason?: string | null;
  };
}
