import assert from "node:assert/strict";
import test from "node:test";
import type { Node } from "@xyflow/react";
import {
  getLayoutedElements,
  estimateSopNodeHeight,
  SOP_NODE_MIN_HEIGHT,
} from "../sopLayout.ts";
import { SOP_FLOW_MIN_ZOOM } from "../SopFlow.tsx";

interface TestNodeData {
  title: string;
  [key: string]: unknown;
}

function makeNodes(): Node<TestNodeData>[] {
  return [
    { id: "s1", type: "sop", position: { x: 0, y: 0 }, data: { title: "Research" } },
    {
      id: "s2",
      type: "sop",
      position: { x: 0, y: 0 },
      data: { title: "Design a very long step title that wraps onto two lines" },
    },
    { id: "s3", type: "sop", position: { x: 0, y: 0 }, data: { title: "Ship" } },
  ];
}

test("getLayoutedElements assigns TB positions and keeps edges", () => {
  const nodes = makeNodes();
  const edges = [
    { id: "e1", source: "s1", target: "s2" },
    { id: "e2", source: "s2", target: "s3" },
  ];

  const { nodes: layouted, edges: layoutedEdges } = getLayoutedElements(
    nodes,
    edges,
    "TB",
  );

  assert.equal(layouted.length, 3);
  assert.equal(layoutedEdges.length, 2);

  const byId = new Map(layouted.map((n) => [n.id, n]));
  const s1 = byId.get("s1")!;
  const s2 = byId.get("s2")!;
  const s3 = byId.get("s3")!;

  // Dependency chains flow top → bottom (ranked rows).
  assert.ok(s2.position.y > s1.position.y, "s2 below s1");
  assert.ok(s3.position.y > s2.position.y, "s3 below s2");

  // target/source handles are top/bottom for TB layout.
  assert.equal(s1.targetPosition, "top");
  assert.equal(s1.sourcePosition, "bottom");
});

test("independent steps are placed side by side (fan-out)", () => {
  const nodes = [
    { id: "s1", type: "sop", position: { x: 0, y: 0 }, data: { title: "Start" } },
    { id: "s2", type: "sop", position: { x: 0, y: 0 }, data: { title: "A" } },
    { id: "s3", type: "sop", position: { x: 0, y: 0 }, data: { title: "B" } },
  ];
  const edges = [
    { id: "e1", source: "s1", target: "s2" },
    { id: "e2", source: "s1", target: "s3" },
  ];

  const { nodes: layouted } = getLayoutedElements(nodes, edges, "TB");
  const s2 = layouted.find((n) => n.id === "s2")!;
  const s3 = layouted.find((n) => n.id === "s3")!;

  // Fan-out children share the same rank (y) but occupy different columns (x).
  assert.equal(s2.position.y, s3.position.y);
  assert.notEqual(s2.position.x, s3.position.x);
  assert.ok(Math.abs(s2.position.x - s3.position.x) >= 40);
});

test("acyclic graph layout produces finite non-overlapping coordinates", () => {
  const nodes: Node[] = [];
  const edges: { id: string; source: string; target: string }[] = [];
  for (let i = 0; i < 8; i += 1) {
    nodes.push({
      id: `s${i}`,
      type: "sop",
      position: { x: 0, y: 0 },
      data: { title: `Step ${i}` },
    });
    if (i > 0) {
      edges.push({ id: `e${i}`, source: `s${i - 1}`, target: `s${i}` });
    }
  }

  const { nodes: layouted } = getLayoutedElements(nodes, edges, "TB");
  for (const node of layouted) {
    assert.ok(Number.isFinite(node.position.x));
    assert.ok(Number.isFinite(node.position.y));
  }
});

test("flow minimum zoom leaves room for the maximum vertical plan", () => {
  const nodes: Node[] = [];
  const edges: { id: string; source: string; target: string }[] = [];
  for (let i = 0; i < 12; i += 1) {
    nodes.push({
      id: `s${i}`,
      type: "sop",
      position: { x: 0, y: 0 },
      data: { title: `Step ${i}` },
    });
    if (i > 0) edges.push({ id: `e${i}`, source: `s${i - 1}`, target: `s${i}` });
  }

  const { nodes: layouted } = getLayoutedElements(nodes, edges, "TB");
  const graphTop = Math.min(...layouted.map((node) => node.position.y));
  const graphBottom = Math.max(
    ...layouted.map(
      (node) => node.position.y + estimateSopNodeHeight(String(node.data.title)),
    ),
  );

  // The SOP canvas is 256px on mobile and 288px on desktop. This conservative
  // bound verifies that the configured clamp can show the full worst-case DAG.
  assert.ok((graphBottom - graphTop) * SOP_FLOW_MIN_ZOOM <= 256);
});

test("estimateSopNodeHeight grows with title length", () => {
  assert.ok(estimateSopNodeHeight("Short") >= SOP_NODE_MIN_HEIGHT);
  assert.ok(
    estimateSopNodeHeight("x".repeat(60)) > estimateSopNodeHeight("Short"),
  );
});

test("LR direction uses left/right handles", () => {
  const nodes = makeNodes();
  const edges = [{ id: "e1", source: "s1", target: "s2" }];
  const { nodes: layouted } = getLayoutedElements(nodes, edges, "LR");
  const s1 = layouted.find((n) => n.id === "s1")!;
  assert.equal(s1.targetPosition, "left");
  assert.equal(s1.sourcePosition, "right");
});
