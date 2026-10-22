import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_LAYOUT_NODES,
  computeForceLayout,
  layoutSize,
  type LayoutEdge,
  type LayoutNode,
} from "../forceLayout.ts";

function ring(count: number): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const nodes = Array.from({ length: count }, (_, i) => ({ id: `n${i}` }));
  const edges = nodes.map((n, i) => ({ source: n.id, target: nodes[(i + 1) % count].id }));
  return { nodes, edges };
}

test("layout is deterministic and independent of input order", () => {
  const { nodes, edges } = ring(12);
  const a = computeForceLayout(nodes, edges);
  const b = computeForceLayout(nodes, edges);
  const c = computeForceLayout([...nodes].reverse(), [...edges].reverse());
  assert.deepEqual(a, b);
  assert.deepEqual(a, c);
});

test("a different seed gives a different but still deterministic layout", () => {
  const { nodes, edges } = ring(8);
  const base = computeForceLayout(nodes, edges, { seed: 0 });
  const other = computeForceLayout(nodes, edges, { seed: 7 });
  assert.notDeepEqual(base, other);
  assert.deepEqual(other, computeForceLayout(nodes, edges, { seed: 7 }));
});

test("positions are finite, inside the canvas and pairwise distinct", () => {
  const { nodes, edges } = ring(30);
  const layout = computeForceLayout(nodes, edges);
  const size = layoutSize(30);
  const seen = new Set<string>();
  for (const node of nodes) {
    const { x, y } = layout[node.id];
    assert.ok(Number.isFinite(x) && Number.isFinite(y), node.id);
    assert.ok(x >= 0 && x <= size && y >= 0 && y <= size, node.id);
    seen.add(`${x}:${y}`);
  }
  assert.equal(seen.size, nodes.length);
});

test("handles empty graphs, single nodes, duplicates and dangling edges without NaN", () => {
  assert.deepEqual(computeForceLayout([], []), {});
  const single = computeForceLayout([{ id: "a" }], []);
  assert.ok(Number.isFinite(single.a.x) && Number.isFinite(single.a.y));

  const messy = computeForceLayout(
    [{ id: "a" }, { id: "a" }, { id: "b" }, { id: "c" }],
    [
      { source: "a", target: "b" },
      { source: "b", target: "a" },
      { source: "a", target: "a" },
      { source: "a", target: "ghost" },
    ],
  );
  assert.deepEqual(Object.keys(messy).sort(), ["a", "b", "c"]);
  for (const point of Object.values(messy)) {
    assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
  }
});

test("connected nodes end up closer than the average pair", () => {
  const nodes = ["a", "b", "c", "d", "e", "f"].map((id) => ({ id }));
  const edges = [
    { source: "a", target: "b" },
    { source: "b", target: "c" },
    { source: "d", target: "e" },
    { source: "e", target: "f" },
  ];
  const layout = computeForceLayout(nodes, edges);
  const dist = (p: string, q: string) =>
    Math.hypot(layout[p].x - layout[q].x, layout[p].y - layout[q].y);
  assert.ok(dist("a", "b") < dist("a", "f"));
  assert.ok(dist("d", "e") < dist("c", "d"));
});

test("up to 500 nodes are simulated; extra nodes are parked on a ring", () => {
  const count = MAX_LAYOUT_NODES + 20;
  const { nodes, edges } = ring(count);
  const layout = computeForceLayout(nodes, edges, { iterations: 5 });
  assert.equal(Object.keys(layout).length, count);
  for (const point of Object.values(layout)) {
    assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y));
  }
});
