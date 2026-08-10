import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import type {
  SopFlowEdge,
  SopFlowNode,
  SopPlan,
} from "../types/sop";
import {
  getLayoutedElements,
  estimateSopNodeHeight,
  SOP_NODE_WIDTH,
} from "../components/sop/sopLayout";

// ============================================
// Flow element construction
// ============================================

/** Structural key for the dagre layout cache (steps + dependencies only). */
export function buildSopStructureKey(plan: SopPlan): string {
  const steps = plan.steps
    .map(
      (step) =>
        `${step.step_id}:${(step.dependencies ?? []).slice().sort().join("+")}`,
    )
    .join("|");
  return `${plan.plan_id}::${steps}`;
}

/** Build React Flow nodes/edges from a plan snapshot and run dagre layout. */
export function buildSopFlowElements(plan: SopPlan): {
  nodes: SopFlowNode[];
  edges: SopFlowEdge[];
} {
  const nodes: SopFlowNode[] = plan.steps.map((step, index) => {
    const height = estimateSopNodeHeight(step.title);
    return {
      id: step.step_id,
      type: "sop",
      position: { x: 0, y: 0 },
      // Keep React Flow's measured wrapper aligned with dagre and the custom
      // node. Without explicit dimensions, the wrapper expands to the canvas
      // width and poisons fitView bounds.
      style: { width: SOP_NODE_WIDTH, height },
      data: {
      title: step.title,
      status: step.status,
      assignee: step.assignee,
      description: step.description ?? "",
      expectedOutput: step.expected_output ?? "",
      error: step.error ?? null,
      stepIndex: index,
        height,
      },
    };
  });

  const edges: SopFlowEdge[] = [];
  const seen = new Set<string>();
  const stepIds = new Set(plan.steps.map((step) => step.step_id));
  for (const step of plan.steps) {
    for (const dep of step.dependencies ?? []) {
      if (dep === step.step_id || !stepIds.has(dep)) continue;
      const key = `${dep}->${step.step_id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({
        id: `sop-edge-${key}`,
        source: dep,
        target: step.step_id,
        type: "smoothstep",
        style: {
          stroke: "var(--theme-primary)",
          strokeWidth: 1.5,
          opacity: 0.35,
        },
      });
    }
  }

  const layouted = getLayoutedElements(nodes, edges, "TB");
  return {
    nodes: layouted.nodes as SopFlowNode[],
    edges: layouted.edges as SopFlowEdge[],
  };
}

// ============================================
// Hook
// ============================================

export interface UseSopStatusResult {
  plan: SopPlan | null;
  setSopPlan: Dispatch<SetStateAction<SopPlan | null>>;
  nodes: SopFlowNode[];
  edges: SopFlowEdge[];
}

/**
 * Manages a `SopPlan | null` snapshot (fed by eventHandlers via `setSopPlan`)
 * and derives the React Flow `nodes`/`edges` for the DAG card.
 *
 * dagre layout is cached by a structural key (plan_id + step ids +
 * dependencies). Status-only updates reuse the cached coordinates so nodes do
 * not jump while a step runs.
 *
 * Accepts an optional external `plan` prop: when the caller passes a new plan
 * reference (e.g. history replay through useAgent → ChatView → SopBlock) the
 * internal snapshot follows it. `setSopPlan` stays authoritative for callers
 * that own the state themselves (useAgent).
 */
export function useSopStatus(plan: SopPlan | null): UseSopStatusResult {
  const [sopPlan, setSopPlanState] = useState<SopPlan | null>(plan);

  // Follow external plan changes (the effect only re-runs when the reference
  // changes, so internal `setSopPlan` updates are never clobbered).
  useEffect(() => {
    setSopPlanState(plan);
  }, [plan]);

  const setSopPlan = useCallback<Dispatch<SetStateAction<SopPlan | null>>>(
    (next) => setSopPlanState(next),
    [],
  );

  const layoutCacheRef = useRef<{
    key: string;
    nodes: SopFlowNode[];
    edges: SopFlowEdge[];
  } | null>(null);

  const { nodes, edges } = useMemo(() => {
    const current = sopPlan;
    if (!current || current.steps.length === 0) {
      layoutCacheRef.current = null;
      return { nodes: [] as SopFlowNode[], edges: [] as SopFlowEdge[] };
    }

    const key = buildSopStructureKey(current);
    const cached = layoutCacheRef.current;
    if (cached && cached.key === key) {
      // Preserve coordinates, but replace the complete node data from the
      // full snapshot so replans/status outputs never leave stale tooltips.
      const stepById = new Map(current.steps.map((step, index) => [step.step_id, { step, index }]));
      const nextNodes = cached.nodes.map((node) => {
        const currentStep = stepById.get(node.id);
        if (!currentStep) return node;
        const { step, index } = currentStep;
        return {
          ...node,
          data: {
            ...node.data,
            title: step.title,
            status: step.status,
            assignee: step.assignee,
            description: step.description ?? "",
            expectedOutput: step.expected_output ?? "",
            error: step.error ?? null,
            stepIndex: index,
          },
        };
      });
      layoutCacheRef.current = { ...cached, nodes: nextNodes };
      return { nodes: nextNodes, edges: cached.edges };
    }

    const built = buildSopFlowElements(current);
    layoutCacheRef.current = { key, ...built };
    return built;
  }, [sopPlan]);

  return { plan: sopPlan, setSopPlan, nodes, edges };
}
