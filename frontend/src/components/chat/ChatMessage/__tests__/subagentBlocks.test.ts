import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSubagentPanelState,
  getSubagentAvatarImageUrl,
  getSubagentRoleIconMeta,
} from "../SubagentBlocks.tsx";

test("subagent panel subtitle shows only the start time", () => {
  const startedAt = Date.UTC(2026, 4, 10, 1, 45, 54);
  const completedAt = startedAt + 26_076 * 60_000 + 2_000;

  const state = buildSubagentPanelState({
    agentId: "agent-a",
    agentName: "worker_agent",
    input: "Do work",
    status: "complete",
    startedAt,
    completedAt,
  });

  assert.equal(
    state.subtitle,
    new Date(startedAt).toLocaleString(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
  );
  assert.ok(!state.subtitle?.includes(" · "));
  assert.ok(!state.subtitle?.includes("26076m 2s"));
});

test("subagent role icon meta matches recognizable role names", () => {
  assert.equal(getSubagentRoleIconMeta("设计理念分析").kind, "design");
  assert.equal(getSubagentRoleIconMeta("frontend_code_reviewer").kind, "code");
  assert.equal(getSubagentRoleIconMeta("qa test analyst").kind, "test");
  assert.equal(getSubagentRoleIconMeta("general-purpose").kind, "general");
});

test("subagent avatar image url accepts role url and emoji avatars", () => {
  assert.equal(
    getSubagentAvatarImageUrl("/api/files/avatar.png"),
    "/api/files/avatar.png",
  );
  assert.match(getSubagentAvatarImageUrl("🎨") || "", /\/emoji-assets\//);
  assert.equal(getSubagentAvatarImageUrl("icon:writing"), null);
});
