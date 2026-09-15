import assert from "node:assert/strict";
import test from "node:test";
import { sortAgentModes } from "../agentModePresentation";

test("keeps the fixed product order for known agent modes", () => {
  const agents = [
    { id: "team", sort_order: 0 },
    { id: "fast", sort_order: 2 },
    { id: "search", sort_order: 100 },
  ];

  assert.deepEqual(
    sortAgentModes(agents).map((agent) => agent.id),
    ["search", "fast", "team"],
  );
});

test("orders unknown agent modes by catalog sort_order before id", () => {
  const agents = [
    { id: "custom-z", sort_order: 20 },
    { id: "custom-b", sort_order: 10 },
    { id: "custom-a", sort_order: 10 },
    { id: "custom-no-order" },
  ];

  assert.deepEqual(
    sortAgentModes(agents).map((agent) => agent.id),
    ["custom-a", "custom-b", "custom-z", "custom-no-order"],
  );
});
