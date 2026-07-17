import test from "node:test";
import assert from "node:assert/strict";
import {
  resolveAvailableAgentId,
  resolvePersonaAgentId,
} from "../agentSelection";

const agents = [
  { id: "search", name: "Search", description: "", version: "1.0.0" },
  { id: "fast", name: "Fast", description: "", version: "1.0.0" },
];

test("falls back to the first available agent when the default agent is unavailable", () => {
  assert.equal(resolveAvailableAgentId("", "default", agents), "search");
});

test("keeps the current agent when it is still available", () => {
  assert.equal(resolveAvailableAgentId("fast", "search", agents), "fast");
});

test("replaces an unavailable current agent with the first available agent", () => {
  assert.equal(resolveAvailableAgentId("default", "default", agents), "search");
});

test("persona preferred agent wins when valid", () => {
  assert.equal(resolvePersonaAgentId("search", "fast"), "search");
  assert.equal(resolvePersonaAgentId("team", "fast"), "team");
});

test("persona missing preferred falls back to requested then fast", () => {
  assert.equal(resolvePersonaAgentId(undefined, "search"), "search");
  assert.equal(resolvePersonaAgentId(null, null), "fast");
  assert.equal(resolvePersonaAgentId("invalid", "team"), "team");
  assert.equal(resolvePersonaAgentId("invalid", "nope"), "fast");
});
