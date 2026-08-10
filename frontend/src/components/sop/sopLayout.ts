import dagre from "@dagrejs/dagre";
import { Position, type Edge, type Node } from "@xyflow/react";

// dagre layout helper for the SOP DAG card.
// Follows the official React Flow dagre example pattern:
// https://reactflow.dev/examples/layout/dagre

export const SOP_NODE_WIDTH = 220;
export const SOP_NODE_MIN_HEIGHT = 72;

/** Approximate characters per line for a 220px node at text-[12px]. */
const TITLE_CHARS_PER_LINE = 24;
const LINE_HEIGHT = 18;

/**
 * Estimate the rendered height of a SOP node from its title length.
 * Height must be stable across status changes (status-only updates must not
 * move coordinates), so it only depends on content that never changes.
 */
export function estimateSopNodeHeight(title: string): number {
  const lines = Math.max(1, Math.ceil(title.length / TITLE_CHARS_PER_LINE));
  return SOP_NODE_MIN_HEIGHT + lines * LINE_HEIGHT;
}

/**
 * Run dagre layout and convert its center-anchored positions into React Flow
 * top-left anchored node positions. Returns a new `{ nodes, edges }` pair;
 * edge geometry is left untouched (React Flow renders edges).
 */
export function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
  direction: "TB" | "LR" = "TB",
): { nodes: Node[]; edges: Edge[] } {
  const isHorizontal = direction === "LR";
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: direction, nodesep: 50, ranksep: 80 });

  const heights = new Map<string, number>();
  for (const node of nodes) {
    const data = (node.data ?? {}) as { title?: unknown };
    const title = typeof data.title === "string" ? data.title : "";
    const height = estimateSopNodeHeight(title);
    heights.set(node.id, height);
    graph.setNode(node.id, { width: SOP_NODE_WIDTH, height });
  }
  for (const edge of edges) {
    graph.setEdge(edge.source, edge.target);
  }

  dagre.layout(graph);

  return {
    nodes: nodes.map((node) => {
      const position = graph.node(node.id);
      const height = heights.get(node.id) ?? SOP_NODE_MIN_HEIGHT;
      return {
        ...node,
        targetPosition: isHorizontal ? Position.Left : Position.Top,
        sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
        position: {
          x: position.x - SOP_NODE_WIDTH / 2,
          y: position.y - height / 2,
        },
      };
    }),
    edges,
  };
}
