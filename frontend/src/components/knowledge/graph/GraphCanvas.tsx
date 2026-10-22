import { useEffect } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
} from "@xyflow/react";
import { EntityNode } from "./EntityNode";
import { ENTITY_NODE_TYPE, type EntityFlowNode } from "./graphMapping";

// Module-level so React Flow does not see a new nodeTypes object on every render.
const nodeTypes = { [ENTITY_NODE_TYPE]: EntityNode };

interface GraphCanvasProps {
  nodes: EntityFlowNode[];
  edges: Edge[];
  /** Changes whenever the layout is recomputed, so the viewport re-fits. */
  fitKey: string;
  onSelect: (id: string | null) => void;
}

function GraphCanvasInner({ nodes, edges, fitKey, onSelect }: GraphCanvasProps) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const timer = setTimeout(() => void fitView({ padding: 0.2, duration: 200 }), 30);
    return () => clearTimeout(timer);
  }, [fitKey, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesConnectable={false}
      nodesDraggable
      minZoom={0.1}
      maxZoom={2.5}
      onNodeClick={(_, node) => onSelect(node.id)}
      onPaneClick={() => onSelect(null)}
      proOptions={{ hideAttribution: true }}
      defaultEdgeOptions={{
        labelStyle: { fontSize: 10, fill: "var(--theme-text-secondary)" },
        labelBgStyle: { fill: "var(--theme-bg-card)", fillOpacity: 0.8 },
      }}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} className="!opacity-30" />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

export default function GraphCanvas(props: GraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <GraphCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
