import { memo } from "react";
import { clsx } from "clsx";
import { Handle, Position } from "@xyflow/react";
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SopNodeData } from "../../types/sop";
import {
  sopNodeStatusClasses,
  sopNodeStatusIconClasses,
} from "./sopNodeStyles";

function SopNodeComponent({ data }: { data: SopNodeData }) {
  const { t } = useTranslation();
  const tooltipParts: string[] = [];
  if (data.description) tooltipParts.push(data.description);
  if (data.expectedOutput)
    tooltipParts.push(t("chat.sop.expectedOutput", "Expected output: {{text}}", { text: data.expectedOutput }));
  if (data.error)
    tooltipParts.push(t("chat.sop.error", "Error: {{text}}", { text: data.error }));
  const tooltip =
    tooltipParts.length > 0 ? tooltipParts.join("\n") : data.title;

  return (
    <div
      title={tooltip}
      className={clsx(
        "relative w-[220px] rounded-xl border px-3 py-2.5 shadow-sm",
        "transition-shadow duration-200",
        sopNodeStatusClasses[data.status],
        data.status === "running" && "sop-node-breath",
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!w-[6px] !h-[6px] !border-none !-top-[3px] !rounded-full !bg-stone-400 dark:!bg-stone-500"
      />
      <div className="flex items-start gap-2">
        {data.status === "running" ? (
          <Loader2
            size={14}
            className={clsx(
              "mt-0.5 shrink-0 animate-spin",
              sopNodeStatusIconClasses.running,
            )}
          />
        ) : data.status === "succeeded" ? (
          <CheckCircle2
            size={14}
            className={clsx("mt-0.5 shrink-0", sopNodeStatusIconClasses.succeeded)}
          />
        ) : data.status === "failed" ? (
          <XCircle
            size={14}
            className={clsx("mt-0.5 shrink-0", sopNodeStatusIconClasses.failed)}
          />
        ) : (
          <Circle
            size={14}
            className={clsx("mt-0.5 shrink-0", sopNodeStatusIconClasses.pending)}
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-medium leading-snug text-[var(--theme-text)] line-clamp-2">
            {data.title}
          </p>
          <p className="mt-1 truncate text-[10px] text-[var(--theme-text-tertiary)]">
            {data.assignee}
          </p>
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-[6px] !h-[6px] !border-none !-bottom-[3px] !rounded-full !bg-stone-400 dark:!bg-stone-500"
      />
    </div>
  );
}

export const SopNode = memo(SopNodeComponent);
