import assert from "node:assert/strict";
import test from "node:test";
import type { KBGraphData, KBGraphStatus } from "../../../../services/api/knowledgeBase.ts";
import {
  ENTITY_NODE_TYPE,
  LABEL_PALETTE,
  NODE_MAX_SIZE,
  NODE_MIN_SIZE,
  labelColor,
  nodeSize,
  toFlowElements,
} from "../graphMapping.ts";
import {
  graphCanBuild,
  graphNeedsPolling,
  graphPhase,
  graphProgressPercent,
  graphSearchAvailable,
} from "../graphUtils.ts";

const graph: KBGraphData = {
  truncated: false,
  nodes: [
    { id: "1", name: "Alice", label: "Person", mention_count: 3 },
    { id: "2", name: "Acme", label: "Org", mention_count: 12 },
    { id: "2", name: "Acme again", label: "Org", mention_count: 1 },
  ],
  edges: [
    { id: "e1", source: "1", target: "2", label: "works at", text: "Alice → works at → Acme" },
    { id: "e1", source: "1", target: "2", label: "dup", text: "" },
    { id: "e2", source: "1", target: "ghost", label: "knows", text: "" },
  ],
};

test("nodes map to React Flow entity nodes centred on their layout point", () => {
  const { nodes } = toFlowElements(
    graph,
    { "1": { x: 100, y: 200 }, "2": { x: 300, y: 50 } },
    "1",
  );
  assert.equal(nodes.length, 2, "duplicate ids are dropped");
  const alice = nodes[0];
  assert.equal(alice.type, ENTITY_NODE_TYPE);
  assert.equal(alice.data.name, "Alice");
  assert.equal(alice.data.selected, true);
  assert.equal(alice.data.color, labelColor("Person"));
  assert.deepEqual(alice.position, {
    x: 100 - alice.data.size / 2,
    y: 200 - alice.data.size / 2,
  });
  assert.equal(nodes[1].data.selected, false);
  assert.ok(nodes[1].data.size > alice.data.size, "more mentions -> bigger node");
});

test("edges keep labels, drop duplicates and edges with unknown endpoints", () => {
  const { edges } = toFlowElements(graph, {}, null);
  assert.deepEqual(
    edges.map((e) => [e.id, e.source, e.target, e.label]),
    [["e1", "1", "2", "works at"]],
  );
  assert.equal(edges[0].markerEnd !== undefined, true);
});

test("selection dims edges that are not attached to the selected node", () => {
  const data: KBGraphData = {
    truncated: false,
    nodes: [1, 2, 3].map((i) => ({ id: String(i), name: `n${i}`, label: "T", mention_count: 1 })),
    edges: [
      { id: "a", source: "1", target: "2", label: "x", text: "" },
      { id: "b", source: "2", target: "3", label: "y", text: "" },
    ],
  };
  const { edges } = toFlowElements(data, {}, "1");
  assert.equal(edges.find((e) => e.id === "a")?.style?.opacity, 1);
  assert.ok(Number(edges.find((e) => e.id === "b")?.style?.opacity) < 1);
});

test("colour and size helpers are stable and clamped", () => {
  assert.equal(labelColor("Person"), labelColor("Person"));
  assert.ok((LABEL_PALETTE as readonly string[]).includes(labelColor("whatever")));
  assert.equal(nodeSize(0), NODE_MIN_SIZE);
  assert.equal(nodeSize(Number.NaN), NODE_MIN_SIZE);
  assert.equal(nodeSize(1_000_000), NODE_MAX_SIZE);
  assert.ok(nodeSize(10) > nodeSize(2));
});

function status(overrides: Partial<KBGraphStatus> = {}): KBGraphStatus {
  return {
    enabled: true,
    schema: "",
    total_chunks: 10,
    pending: 0,
    done: 10,
    failed: 0,
    excluded: 0,
    entity_count: 4,
    relation_count: 3,
    active_task: null,
    last_error: null,
    allow_graph: true,
    ...overrides,
  };
}

test("graph phase, progress and action availability", () => {
  assert.equal(graphPhase(status({ enabled: false })), "disabled");
  assert.equal(graphPhase(status()), "ready");
  assert.equal(graphPhase(status({ pending: 2 })), "pending");
  assert.equal(graphPhase(status({ failed: 1 })), "failed");
  assert.equal(graphPhase(status({ last_error: "x", done: 0 })), "failed");
  const task = { id: "t" } as unknown as KBGraphStatus["active_task"];
  assert.equal(graphPhase(status({ pending: 2, active_task: task })), "building");

  assert.equal(graphProgressPercent(status({ done: 5, pending: 5 })), 50);
  assert.equal(graphProgressPercent(status({ done: 0, pending: 0 })), 100);
  assert.equal(graphProgressPercent(status({ done: 2, failed: 2, pending: 4 })), 50);

  assert.equal(graphCanBuild(status()), false);
  assert.equal(graphCanBuild(status({ failed: 1 })), true);
  assert.equal(graphCanBuild(status({ pending: 1, active_task: task })), false);
  assert.equal(graphNeedsPolling(status({ active_task: task })), true);
  assert.equal(graphNeedsPolling(null), false);
  assert.equal(graphSearchAvailable(status()), true);
  assert.equal(graphSearchAvailable(status({ done: 0 })), false);
  assert.equal(graphSearchAvailable(status({ enabled: false })), false);
});
