import type { KBGraphStatus } from "../../../services/api/knowledgeBase";

export const GRAPH_POLL_MS = 3000;
export const GRAPH_NODE_OPTIONS = [50, 100, 200, 500] as const;
export const GRAPH_DEPTH_OPTIONS = [1, 2, 3] as const;
export const GRAPH_DEFAULT_NODES = 100;
export const GRAPH_DEFAULT_DEPTH = 2;

export type GraphPhase = "disabled" | "building" | "failed" | "pending" | "ready";

export function graphPhase(status: KBGraphStatus): GraphPhase {
  if (!status.enabled) return "disabled";
  if (status.active_task) return "building";
  if (status.last_error || status.failed > 0) return "failed";
  if (status.pending > 0) return "pending";
  return "ready";
}

/** Percentage of enrolled chunks that finished (done or failed); 100 when nothing is enrolled. */
export function graphProgressPercent(status: KBGraphStatus): number {
  const total = status.done + status.failed + status.pending;
  if (total <= 0) return 100;
  return Math.round(((status.done + status.failed) / total) * 100);
}

export function graphNeedsPolling(status: KBGraphStatus | null): boolean {
  return Boolean(status?.enabled && status.active_task);
}

/** Whether the "continue / retry" action makes sense. */
export function graphCanBuild(status: KBGraphStatus): boolean {
  return status.enabled && !status.active_task && (status.pending > 0 || status.failed > 0);
}

/** The retrieval test may use the graph once at least one chunk has been extracted. */
export function graphSearchAvailable(status: KBGraphStatus | null): boolean {
  return Boolean(status?.enabled && status.done > 0);
}
