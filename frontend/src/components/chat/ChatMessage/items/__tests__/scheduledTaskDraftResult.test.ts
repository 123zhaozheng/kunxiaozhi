import test from "node:test";
import assert from "node:assert/strict";
import {
  describeDraftChanges,
  isScheduledTaskDraftToolName,
  parseScheduledTaskDraftResult,
} from "../scheduledTaskDraftResult.ts";

const draftPayload = {
  entity_type: "scheduled_task_draft",
  draft_id: "a".repeat(32),
  action: "create",
  task_id: null,
  summary: {
    name: "晨报",
    prompt: "整理今天的重点",
    schedule_text: "每个工作日 08:30",
    next_runs: ["2026-10-10T00:30:00+00:00"],
    persona: { id: "p1", name: "检索专家", avatar: "🔍" },
    enabled: true,
  },
  before: null,
  changes: [],
  expires_at: "2026-10-09T08:35:00+00:00",
  now: "2026-10-09 16:05（北京时间，周五）",
  message: "草稿已生成",
};

test("recognises the draft tool name, including prefixed names", () => {
  assert.equal(isScheduledTaskDraftToolName("draft_scheduled_task"), true);
  assert.equal(
    isScheduledTaskDraftToolName("builtin:draft_scheduled_task"),
    true,
  );
  assert.equal(isScheduledTaskDraftToolName("list_scheduled_tasks"), false);
  assert.equal(isScheduledTaskDraftToolName("my_draft_scheduled_task"), false);
  assert.equal(isScheduledTaskDraftToolName(""), false);
  assert.equal(isScheduledTaskDraftToolName(undefined), false);
});

test("parses an object result", () => {
  const parsed = parseScheduledTaskDraftResult(draftPayload);
  assert.ok(parsed);
  assert.equal(parsed.draft_id, "a".repeat(32));
  assert.equal(parsed.action, "create");
  assert.equal(parsed.task_id, null);
  assert.equal(parsed.summary.name, "晨报");
  assert.equal(parsed.summary.persona?.name, "检索专家");
  assert.deepEqual(parsed.summary.next_runs, ["2026-10-10T00:30:00+00:00"]);
  assert.equal(parsed.before, null);
  assert.deepEqual(parsed.changes, []);
});

test("parses a JSON string result", () => {
  const parsed = parseScheduledTaskDraftResult(JSON.stringify(draftPayload));
  assert.ok(parsed);
  assert.equal(parsed.summary.schedule_text, "每个工作日 08:30");
});

test("parses update drafts with before and changes", () => {
  const parsed = parseScheduledTaskDraftResult({
    ...draftPayload,
    action: "update",
    task_id: "t1",
    summary: { ...draftPayload.summary, persona: null, enabled: false },
    before: draftPayload.summary,
    changes: ["persona", "enabled", "bogus"],
  });
  assert.ok(parsed);
  assert.equal(parsed.action, "update");
  assert.equal(parsed.task_id, "t1");
  assert.equal(parsed.summary.persona, null);
  assert.equal(parsed.summary.enabled, false);
  assert.equal(parsed.before?.name, "晨报");
  assert.deepEqual(parsed.changes, ["persona", "enabled"]);
});

test("returns null for non-draft, error and malformed results", () => {
  assert.equal(parseScheduledTaskDraftResult(undefined), null);
  assert.equal(parseScheduledTaskDraftResult("not json"), null);
  assert.equal(parseScheduledTaskDraftResult("[1,2]"), null);
  assert.equal(
    parseScheduledTaskDraftResult({ entity_type: "scheduled_task_list" }),
    null,
  );
  assert.equal(
    parseScheduledTaskDraftResult({
      error: "频率不合法",
      now: "2026-10-09 16:05（北京时间，周五）",
    }),
    null,
  );
  assert.equal(
    parseScheduledTaskDraftResult(
      JSON.stringify({ error: "找不到任务", candidates: [] }),
    ),
    null,
  );
  assert.equal(
    parseScheduledTaskDraftResult({ ...draftPayload, draft_id: 123 }),
    null,
  );
  assert.equal(
    parseScheduledTaskDraftResult({ ...draftPayload, summary: undefined }),
    null,
  );
});

test("maps change keys to Chinese labels", () => {
  assert.deepEqual(
    describeDraftChanges([
      "name",
      "prompt",
      "schedule",
      "persona",
      "enabled",
    ]),
    ["名称", "要做的事", "执行时间", "执行专家", "启用状态"],
  );
  assert.deepEqual(describeDraftChanges(["schedule", "unknown", "schedule"]), [
    "执行时间",
  ]);
  assert.deepEqual(describeDraftChanges([]), []);
  assert.deepEqual(describeDraftChanges(undefined), []);
});
