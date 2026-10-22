import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { EntityFlowNode } from "./graphMapping";

function EntityNodeComponent({ data }: NodeProps<EntityFlowNode>) {
  const { size, color, name, label, selected, mentionCount } = data;
  return (
    <div
      title={`${name} · ${label} · ${mentionCount}`}
      className="relative flex flex-col items-center"
      style={{ width: size }}
    >
      <Handle type="target" position={Position.Top} className="!h-1 !w-1 !border-none !opacity-0" />
      <div
        className="rounded-full border-2 shadow-sm transition-shadow"
        style={{
          width: size,
          height: size,
          backgroundColor: color,
          borderColor: selected ? "var(--theme-text)" : "rgba(255,255,255,0.85)",
          boxShadow: selected ? `0 0 0 3px ${color}55` : undefined,
        }}
      />
      <span
        className="pointer-events-none mt-1 max-w-[140px] truncate rounded bg-[var(--theme-bg-card)]/80 px-1 text-[11px] font-medium text-[var(--theme-text)]"
        style={{ width: "max-content" }}
      >
        {name}
      </span>
      <Handle type="source" position={Position.Bottom} className="!h-1 !w-1 !border-none !opacity-0" />
    </div>
  );
}

export const EntityNode = memo(EntityNodeComponent);
