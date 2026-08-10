import { useEffect, useRef } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useReactFlow,
} from "@xyflow/react";
import { SopNode } from "./SopNode";
import type { SopFlowEdge, SopFlowNode } from "../../types/sop";

// NOTE: React Flow's base stylesheet is imported at the global entrypoint in
// main.tsx. `./sopPlanFlow.css` remains a separate global SOP theme stylesheet.
// Keeping stylesheet imports out of this module lets `tsx --test` import the
// component tree without a CSS loader.

// nodeTypes must be a module-level constant (or memoized) so React Flow does
// not treat the node type as changed on every render.
const nodeTypes = { sop: SopNode };

/**
 * The card is intentionally compact, while a 12-step TB plan can be much
 * taller than its viewport. React Flow clamps fitView to minZoom, so this
 * must accommodate the worst-case plan rather than the common 2-3 step case.
 */
export const SOP_FLOW_MIN_ZOOM = 0.08;

interface SopFlowProps {
  nodes: SopFlowNode[];
  edges: SopFlowEdge[];
  /** Show MiniMap when the plan has many steps (> 8). */
  showMiniMap?: boolean;
}

function SopFlowInner({ nodes, edges, showMiniMap }: SopFlowProps) {
  const { fitView } = useReactFlow();

  // Fit the view once per layout structure (same step set). Status-only
  // updates keep the same coordinates, so re-fitting would make nodes jump.
  const prevStructureRef = useRef<string | null>(null);
  useEffect(() => {
    const structure = nodes
      .map((node) => node.id)
      .slice()
      .sort()
      .join(",");
    if (prevStructureRef.current !== structure) {
      prevStructureRef.current = structure;
      if (nodes.length > 0) {
        fitView({ padding: 0.2, duration: 200 });
      }
    }
  }, [nodes, fitView]);

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        minZoom={SOP_FLOW_MIN_ZOOM}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        className="sop-flow-canvas"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="var(--theme-primary)"
          className="!opacity-[0.12]"
        />
        <Controls
          showInteractive={false}
          position="bottom-left"
          className="sop-flow-controls"
        />
        {showMiniMap && (
          <MiniMap
            pannable
            zoomable
            position="bottom-right"
            className="!bg-[var(--theme-bg-card)] !border !border-[var(--theme-border)] !rounded-lg !overflow-hidden"
            nodeColor="var(--theme-primary)"
            maskColor="color-mix(in srgb, var(--theme-bg) 70%, transparent)"
          />
        )}
      </ReactFlow>
    </div>
  );
}

export function SopFlow({ nodes, edges, showMiniMap }: SopFlowProps) {
  if (nodes.length === 0) return null;
  return (
    <ReactFlowProvider>
      <SopFlowInner nodes={nodes} edges={edges} showMiniMap={showMiniMap} />
    </ReactFlowProvider>
  );
}
