import type { PersonaPreset, PersonaPresetSnapshot } from "../../../types";
import type {
  ScheduleSpec,
  ScheduledTaskInput,
} from "../../../types/scheduledTask";
import { DEFAULT_TIMEZONE, cleanSpec } from "./scheduleUtils";

/** The expert picked in the task editor (display data only; never calls `/use`). */
export interface PersonaChoice {
  id: string;
  name: string;
  avatar?: string | null;
}

export interface TaskSaveFields {
  name: string;
  prompt: string;
  schedule: ScheduleSpec;
  persona: PersonaChoice | null;
  templateId: string | null;
  isEdit: boolean;
}

/**
 * Request body for create / update.
 * `persona_preset_id` is always sent: the id when an expert is chosen, `null` to clear it
 * (an omitted field would leave the stored expert untouched on update).
 */
export function buildTaskSaveBody({
  name,
  prompt,
  schedule,
  persona,
  templateId,
  isEdit,
}: TaskSaveFields): ScheduledTaskInput {
  const body: ScheduledTaskInput = {
    name: name.trim(),
    prompt: prompt.trim(),
    schedule: cleanSpec(schedule),
    timezone: DEFAULT_TIMEZONE,
    persona_preset_id: persona?.id ?? null,
  };
  if (!isEdit) body.template_id = templateId;
  return body;
}

export function personaChoiceFromPreset(preset: PersonaPreset): PersonaChoice {
  return { id: preset.id, name: preset.name, avatar: preset.avatar ?? null };
}

/** Initial choice when editing; null when the task has no expert (or its name is unknown). */
export function personaChoiceFromTask(task: {
  persona_preset_id?: string | null;
  persona_name?: string | null;
  persona_avatar?: string | null;
} | null): PersonaChoice | null {
  if (!task?.persona_preset_id) return null;
  return {
    id: task.persona_preset_id,
    name: task.persona_name ?? "",
    avatar: task.persona_avatar ?? null,
  };
}

/** Satisfies the selector's `onUsePreset` contract without hitting the `/use` endpoint. */
export function presetToSnapshot(preset: PersonaPreset): PersonaPresetSnapshot {
  return {
    preset_id: preset.id,
    name: preset.name,
    system_prompt: preset.system_prompt,
    starter_prompts: preset.starter_prompts,
    skill_names: preset.skill_names,
    skill_hints: [],
    dify_kb_dataset_ids: preset.dify_kb_dataset_ids,
    preferred_agent_id: preset.preferred_agent_id,
    version: preset.version,
    avatar: preset.avatar ?? null,
  };
}
