import test from "node:test";
import assert from "node:assert/strict";

import type { PersonaPreset } from "../../../../types/personaPreset.ts";
import type { ScheduleSpec } from "../../../../types/scheduledTask.ts";
import {
  buildTaskSaveBody,
  personaChoiceFromPreset,
  personaChoiceFromTask,
  presetToSnapshot,
} from "../taskEditorPayload.ts";

const schedule: ScheduleSpec = { kind: "weekdays", time: "08:30" };
const analyst = { id: "p1", name: "分析师", avatar: "📊" };

const base = {
  name: "  晨报  ",
  prompt: "  整理今天的重点  ",
  schedule,
  persona: null,
  templateId: "tpl-1",
  isEdit: false,
};

test("create body trims text, cleans the schedule and carries the template", () => {
  const body = buildTaskSaveBody(base);
  assert.deepEqual(body, {
    name: "晨报",
    prompt: "整理今天的重点",
    schedule: { kind: "weekdays", time: "08:30" },
    timezone: "Asia/Shanghai",
    persona_preset_id: null,
    template_id: "tpl-1",
  });
});

test("create body sends the chosen expert id", () => {
  const body = buildTaskSaveBody({ ...base, persona: analyst });
  assert.equal(body.persona_preset_id, "p1");
});

test("update body keeps the expert and omits template_id", () => {
  const body = buildTaskSaveBody({ ...base, isEdit: true, persona: analyst });
  assert.equal(body.persona_preset_id, "p1");
  assert.equal("template_id" in body, false);
});

test("clearing the expert sends an explicit null on update and create", () => {
  for (const isEdit of [true, false]) {
    const body = buildTaskSaveBody({ ...base, isEdit, persona: null });
    assert.ok("persona_preset_id" in body);
    assert.equal(body.persona_preset_id, null);
    assert.equal(JSON.parse(JSON.stringify(body)).persona_preset_id, null);
  }
});

test("update body normalises one-shot run_at like create", () => {
  const body = buildTaskSaveBody({
    ...base,
    isEdit: true,
    schedule: { kind: "once", run_at: "2026-10-10T09:00" },
  });
  assert.deepEqual(body.schedule, { kind: "once", run_at: "2026-10-10T09:00:00" });
});

test("personaChoiceFromTask restores the stored expert and ignores plain tasks", () => {
  assert.equal(personaChoiceFromTask(null), null);
  assert.equal(personaChoiceFromTask({ persona_preset_id: null }), null);
  assert.deepEqual(
    personaChoiceFromTask({
      persona_preset_id: "p1",
      persona_name: "分析师",
      persona_avatar: "📊",
    }),
    analyst,
  );
  // An unavailable expert has no name/avatar but must still be kept so saving does not clear it.
  assert.deepEqual(personaChoiceFromTask({ persona_preset_id: "gone" }), {
    id: "gone",
    name: "",
    avatar: null,
  });
});

const preset = {
  id: "p1",
  scope: "global",
  name: "分析师",
  description: "",
  avatar: "📊",
  tags: [],
  system_prompt: "你是分析师",
  starter_prompts: [],
  skill_names: ["web-research"],
  dify_kb_dataset_ids: ["kb-1"],
  preferred_agent_id: "search",
  visibility: "public",
  status: "published",
  version: 3,
  usage_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} as PersonaPreset;

test("selecting a preset records only id, name and avatar", () => {
  assert.deepEqual(personaChoiceFromPreset(preset), analyst);
  assert.deepEqual(personaChoiceFromPreset({ ...preset, avatar: undefined }), {
    id: "p1",
    name: "分析师",
    avatar: null,
  });
});

test("presetToSnapshot satisfies the selector contract from the preset alone", () => {
  const snapshot = presetToSnapshot(preset);
  assert.equal(snapshot.preset_id, "p1");
  assert.equal(snapshot.name, "分析师");
  assert.deepEqual(snapshot.skill_names, ["web-research"]);
  assert.deepEqual(snapshot.dify_kb_dataset_ids, ["kb-1"]);
  assert.equal(snapshot.preferred_agent_id, "search");
  assert.equal(snapshot.version, 3);
  assert.deepEqual(snapshot.skill_hints, []);
});
