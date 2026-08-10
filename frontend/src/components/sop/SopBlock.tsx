import { useState } from "react";
import { clsx } from "clsx";
import { useTranslation } from "react-i18next";
import {
  Check,
  ChevronDown,
  GitBranch,
  Loader2,
  MessageSquare,
  RefreshCcw,
} from "lucide-react";
import type { SopPlan, SopPlanStatus } from "../../types/sop";
import { useSopStatus } from "../../hooks/useSopStatus";
import { buildSopRespondResponse } from "./sopBlockUtils";
import { SopFlow } from "./SopFlow";

export interface SopBlockProps {
  plan: SopPlan | null;
  isStreaming?: boolean;
  onRespond?: (
    approvalId: string,
    response: Record<string, unknown>,
    approved: boolean,
  ) => void | Promise<unknown>;
  isLoading?: boolean;
}

const statusClasses: Record<SopPlanStatus, string> = {
  draft: "bg-stone-500/10 text-stone-600 dark:text-stone-300",
  awaiting_confirmation: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  running: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  completed: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  failed: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  cancelled: "bg-stone-500/10 text-stone-600 dark:text-stone-300",
  rejected: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  timed_out: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
};

const legendItems: Array<{
  key: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  dotClass: string;
}> = [
  { key: "pending", dotClass: "bg-stone-400 dark:bg-stone-500" },
  { key: "running", dotClass: "bg-[var(--theme-primary)]" },
  { key: "succeeded", dotClass: "bg-emerald-500 dark:bg-emerald-400" },
  { key: "failed", dotClass: "bg-rose-500 dark:bg-rose-400" },
  { key: "cancelled", dotClass: "bg-stone-400 dark:bg-stone-500" },
];

export function SopBlock({
  plan,
  isStreaming,
  onRespond,
  isLoading = false,
}: SopBlockProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(true);
  const [feedback, setFeedback] = useState("");
  const { nodes, edges } = useSopStatus(plan);

  if (!plan || plan.steps.length === 0) return null;

  const total = plan.steps.length;
  const succeededCount = plan.steps.filter(
    (step) => step.status === "succeeded",
  ).length;
  const progress = total > 0 ? (succeededCount / total) * 100 : 0;
  const isAllDone = succeededCount === total && total > 0;

  const canRespond =
    plan.status === "awaiting_confirmation" &&
    Boolean(plan.approval_id) &&
    Boolean(onRespond);
  const isExpired = plan.status === "timed_out";

  const submitResponse = (approved: boolean) => {
    if (!plan.approval_id || isLoading) return;
    const response = buildSopRespondResponse(approved, feedback);
    void onRespond?.(plan.approval_id, response, approved);
    if (!approved) setFeedback("");
  };

  const statusLabel = (() => {
    const key = `chat.sop.status.${plan.status}`;
    const fallback = plan.status.replaceAll("_", " ");
    return t(key, fallback);
  })();

  return (
    <section
      className="my-1.5 overflow-hidden rounded-xl ring-1 ring-stone-200 dark:ring-stone-700/80 bg-stone-50/80 dark:bg-stone-800/40"
      aria-label={t("chat.sop.title", "SOP plan")}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3.5 py-2.5">
        <span className="rounded-md bg-[var(--theme-primary-light)] p-1.5 text-[var(--theme-primary)]">
          <GitBranch size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-xs font-semibold text-[var(--theme-text)]">
            {t("chat.sop.title", "SOP plan")}
            {plan.team_id ? (
              <span className="ml-1.5 font-normal text-[var(--theme-text-tertiary)]">
                · {t("chat.sop.team", "Team")}
              </span>
            ) : null}
          </h3>
          {plan.goal ? (
            <p className="mt-0.5 truncate text-[11px] text-[var(--theme-text-secondary)]">
              {plan.goal}
            </p>
          ) : null}
        </div>
        <span
          className={clsx(
            "shrink-0 rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide",
            statusClasses[plan.status],
          )}
        >
          {statusLabel}
        </span>
        {isStreaming && (
          <Loader2
            size={13}
            className="shrink-0 animate-spin text-stone-400 dark:text-stone-500"
          />
        )}
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={expanded ? t("chat.sop.collapse", "Collapse") : t("chat.sop.expand", "Expand")}
          className="shrink-0 rounded-md p-1 text-[var(--theme-text-tertiary)] transition-colors hover:bg-[var(--theme-bg-subtle)] hover:text-[var(--theme-text-secondary)]"
        >
          <ChevronDown
            size={15}
            className={clsx(
              "transition-transform duration-200",
              !expanded && "-rotate-90",
            )}
          />
        </button>
      </div>

      {expanded && (
        <>
          {/* Progress bar */}
          <div className="flex items-center gap-2 border-t border-stone-200/60 dark:border-stone-700/50 px-3.5 py-2">
            <span className="text-xs text-stone-500 dark:text-stone-400">
              {t("chat.sop.progress", "{{completed}}/{{total}}", {
                completed: succeededCount,
                total,
              })}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700">
              <div
                className={clsx(
                  "h-full rounded-full transition-all duration-500 ease-out",
                  isAllDone
                    ? "bg-emerald-500 dark:bg-emerald-400"
                    : "bg-[var(--theme-primary)]",
                )}
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="text-[11px] tabular-nums text-stone-400 dark:text-stone-500">
              {Math.round(progress)}%
            </span>
          </div>

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-stone-200/60 dark:border-stone-700/50 px-3.5 py-1.5">
            {legendItems.map((item) => (
              <span
                key={item.key}
                className="inline-flex items-center gap-1 text-[10px] text-[var(--theme-text-tertiary)]"
              >
                <span
                  className={clsx(
                    "size-[6px] rounded-full",
                    item.dotClass,
                    item.key === "running" && "animate-pulse",
                  )}
                />
                {t(`chat.sop.legend.${item.key}`, item.key)}
              </span>
            ))}
          </div>

          {/* DAG flow */}
          <div className="border-t border-stone-200/60 dark:border-stone-700/50">
            <div className="h-64 sm:h-72">
              <SopFlow
                nodes={nodes}
                edges={edges}
                showMiniMap={total > 8}
              />
            </div>
          </div>

          {/* User feedback */}
          {plan.user_feedback ? (
            <div className="mx-3 mb-2 flex items-start gap-2 rounded-md bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
              <MessageSquare size={13} className="mt-0.5 shrink-0" />
              <span>{plan.user_feedback}</span>
            </div>
          ) : null}

          {isExpired ? (
            <div className="mx-3 mb-2 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              {t("chat.sop.expired", "This approval expired. Please replan the task.")}
            </div>
          ) : null}

          {/* Confirm / replan */}
          {canRespond && (
            <div className="border-t border-stone-200/60 dark:border-stone-700/50 px-3.5 py-2.5">
              <label
                htmlFor="sop-block-feedback"
                className="mb-1.5 block text-xs font-medium text-[var(--theme-text-secondary)]"
              >
                {t("chat.sop.feedbackLabel", "Feedback (optional)")}
              </label>
              <textarea
                id="sop-block-feedback"
                value={feedback}
                onChange={(event) => setFeedback(event.target.value)}
                rows={2}
                placeholder={t(
                  "chat.sop.feedbackPlaceholder",
                  "Tell the agent what to adjust if replanning",
                )}
                className="w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2.5 py-2 text-sm text-[var(--theme-text)] outline-none focus:border-[var(--theme-primary)]"
              />
              <div className="mt-2 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <button
                  type="button"
                  onClick={() => submitResponse(false)}
                  disabled={isLoading}
                  className="inline-flex items-center justify-center gap-1.5 rounded-md border border-[var(--theme-border)] px-3 py-2 text-sm text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-subtle)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <RefreshCcw size={14} />
                  {t("chat.sop.replan", "Replan")}
                </button>
                <button
                  type="button"
                  onClick={() => submitResponse(true)}
                  disabled={isLoading}
                  className="inline-flex items-center justify-center gap-1.5 rounded-md bg-[var(--theme-primary)] px-3 py-2 text-sm text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isLoading ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Check size={14} />
                  )}
                  {t("chat.sop.confirm", "Confirm and run")}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
