import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCheckpointForkUrl,
  buildMessageCheckpointUrl,
  buildMessageForkUrl,
  buildSessionRunsUrl,
  buildSubmitChatBody,
} from "../session.ts";

test("builds a session list url with favorites_only", () => {
  const searchParams = new URLSearchParams();
  searchParams.set("favorites_only", "true");
  assert.equal(
    `/api/sessions?${searchParams.toString()}`,
    "/api/sessions?favorites_only=true",
  );
});

test("builds the default session runs url", () => {
  assert.equal(
    buildSessionRunsUrl("session-1"),
    "/api/sessions/session-1/runs",
  );
});

test("includes trace_id when looking up a specific run by trace", () => {
  assert.equal(
    buildSessionRunsUrl("session-1", { trace_id: "trace-123" }),
    "/api/sessions/session-1/runs?trace_id=trace-123",
  );
});

test("includes user_timezone in the submit chat body when available", () => {
  assert.deepEqual(
    buildSubmitChatBody({
      message: "hello",
      sessionId: "session-1",
      userTimezone: "Asia/Shanghai",
    }),
    {
      message: "hello",
      session_id: "session-1",
      agent_options: undefined,
      attachments: undefined,
      disabled_skills: undefined,
      enabled_skills: undefined,
      persona_preset_id: undefined,
      disabled_mcp_tools: undefined,
      user_timezone: "Asia/Shanghai",
    },
  );
});

test("includes persona preset fields in the submit chat body", () => {
  assert.deepEqual(
    buildSubmitChatBody({
      message: "hello",
      personaPresetId: "preset-1",
      enabledSkills: ["planning"],
    }),
    {
      message: "hello",
      session_id: undefined,
      agent_options: undefined,
      attachments: undefined,
      disabled_skills: undefined,
      enabled_skills: ["planning"],
      persona_preset_id: "preset-1",
      disabled_mcp_tools: undefined,
    },
  );
});

test("includes team_id in the submit chat body when a team is selected", () => {
  assert.deepEqual(
    buildSubmitChatBody({
      message: "hello",
      teamId: "team-1",
    }),
    {
      message: "hello",
      session_id: undefined,
      agent_options: undefined,
      attachments: undefined,
      disabled_skills: undefined,
      enabled_skills: undefined,
      persona_preset_id: undefined,
      disabled_mcp_tools: undefined,
      team_id: "team-1",
    },
  );
});

test("includes a run-scoped goal in the submit chat body", () => {
  assert.deepEqual(
    buildSubmitChatBody({
      message: "continue",
      goal: {
        objective: "finish docs",
        rubric: "- docs updated",
        max_iterations: 3,
      },
    }),
    {
      message: "continue",
      session_id: undefined,
      agent_options: undefined,
      attachments: undefined,
      disabled_skills: undefined,
      enabled_skills: undefined,
      persona_preset_id: undefined,
      disabled_mcp_tools: undefined,
      goal: {
        objective: "finish docs",
        rubric: "- docs updated",
        max_iterations: 3,
      },
    },
  );
});

test("builds the message fork url", () => {
  assert.equal(
    buildMessageForkUrl("session-1", "message-1"),
    "/api/sessions/session-1/messages/message-1/fork",
  );
});

test("builds the message checkpoint url", () => {
  assert.equal(
    buildMessageCheckpointUrl("session-1", "message-1"),
    "/api/sessions/session-1/messages/message-1/checkpoints",
  );
});

test("builds the checkpoint fork url", () => {
  assert.equal(
    buildCheckpointForkUrl("session-1", "checkpoint-1"),
    "/api/sessions/session-1/checkpoints/checkpoint-1/fork",
  );
});

test("includes kb_ids in the submit chat body only when non-empty", () => {
  const withKbs = buildSubmitChatBody({
    message: "hi",
    kbIds: ["kb-1", "kb-2"],
  });
  assert.deepEqual(withKbs.kb_ids, ["kb-1", "kb-2"]);
  assert.equal("kb_ids" in buildSubmitChatBody({ message: "hi" }), false);
  assert.equal(
    "kb_ids" in buildSubmitChatBody({ message: "hi", kbIds: [] }),
    false,
  );
});
