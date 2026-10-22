import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type {
  KBGraphData,
  KBGraphEdge,
  KBGraphNode,
} from "../../../services/api/knowledgeBase";
import type { LayoutResult } from "./forceLayout";

export const ENTITY_NODE_TYPE = "entity";

export const LABEL_PALETTE = [
  "#6366f1",
  "#0ea5e9",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#14b8a6",
  "#ec4899",
  "#84cc16",
  "#f97316",
] as const;

export const NODE_MIN_SIZE = 28;
export const NODE_MAX_SIZE = 72;

export interface EntityNodeData extends Record<string, unknown> {
  name: string;
  label: string;
  color: string;
  size: number;
  mentionCount: number;
  selected: boolean;
}

export type EntityFlowNode = Node<EntityNodeData, typeof ENTITY_NODE_TYPE>;

/** Stable colour per entity type. */
export function labelColor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i += 1) {
    h = (Math.imul(h, 31) + label.charCodeAt(i)) >>> 0;
  }
  return LABEL_PALETTE[h % LABEL_PALETTE.length];
}

/** Node diameter grows with the (log of) mention count, clamped to a readable range. */
export function nodeSize(mentionCount: number): number {
  const count = Number.isFinite(mentionCount) ? Math.max(0, mentionCount) : 0;
  return Math.min(NODE_MAX_SIZE, Math.round(NODE_MIN_SIZE + 9 * Math.log2(1 + count)));
}

export function toFlowNodes(
  nodes: KBGraphNode[],
  layout: LayoutResult,
  selectedId: string | null = null,
): EntityFlowNode[] {
  const seen = new Set<string>();
  const out: EntityFlowNode[] = [];
  for (const node of nodes) {
    if (seen.has(node.id)) continue;
    seen.add(node.id);
    const size = nodeSize(node.mention_count);
    const at = layout[node.id] ?? { x: 0, y: 0 };
    out.push({
      id: node.id,
      type: ENTITY_NODE_TYPE,
      position: { x: at.x - size / 2, y: at.y - size / 2 },
      data: {
        name: node.name,
        label: node.label,
        color: labelColor(node.label),
        size,
        mentionCount: node.mention_count,
        selected: node.id === selectedId,
      },
    });
  }
  return out;
}

export function toFlowEdges(
  edges: KBGraphEdge[],
  nodeIds: Set<string>,
  selectedId: string | null = null,
): Edge[] {
  const seen = new Set<string>();
  const out: Edge[] = [];
  for (const edge of edges) {
    if (seen.has(edge.id) || !nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue;
    seen.add(edge.id);
    const active = selectedId !== null && (edge.source === selectedId || edge.target === selectedId);
    out.push({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label,
      markerEnd: { type: MarkerType.ArrowClosed },
      animated: false,
      style: { strokeWidth: active ? 2 : 1, opacity: selectedId && !active ? 0.35 : 1 },
      data: { text: edge.text },
    });
  }
  return out;
}

export function toFlowElements(
  graph: KBGraphData,
  layout: LayoutResult,
  selectedId: string | null = null,
): { nodes: EntityFlowNode[]; edges: Edge[] } {
  const nodes = toFlowNodes(graph.nodes, layout, selectedId);
  return { nodes, edges: toFlowEdges(graph.edges, new Set(nodes.map((n) => n.id)), selectedId) };
}
