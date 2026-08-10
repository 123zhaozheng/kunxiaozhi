import type { SopStepStatus } from "../../types/sop";

// Status-based styling for SOP DAG nodes.
// pending / cancelled: gray · running: theme-primary + breathing · succeeded:
// green · failed: red

export const sopNodeStatusClasses: Record<SopStepStatus, string> = {
  pending:
    "border-stone-300/80 dark:border-stone-600/50 bg-stone-100/70 dark:bg-stone-800/50",
  running:
    "border-[var(--theme-primary)] bg-[color-mix(in_srgb,var(--theme-primary)_7%,transparent)]",
  succeeded:
    "border-emerald-400/70 dark:border-emerald-500/50 bg-emerald-50/80 dark:bg-emerald-950/40",
  failed:
    "border-rose-400/80 dark:border-rose-500/50 bg-rose-50/80 dark:bg-rose-950/40",
  cancelled:
    "border-stone-300/80 dark:border-stone-600/50 bg-stone-100/70 dark:bg-stone-800/50",
};

export const sopNodeStatusIconClasses: Record<SopStepStatus, string> = {
  pending: "text-stone-400 dark:text-stone-500",
  running: "text-[var(--theme-primary)]",
  succeeded: "text-emerald-500 dark:text-emerald-400",
  failed: "text-rose-500 dark:text-rose-400",
  cancelled: "text-stone-400 dark:text-stone-500",
};
