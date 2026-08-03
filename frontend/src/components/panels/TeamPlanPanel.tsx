import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  CheckCircle2,
  Circle,
  CircleAlert,
  Clock3,
  FileCheck2,
  GitBranch,
  Loader2,
  MessageSquare,
  X,
  XCircle,
} from "lucide-react";
import type {
  TeamPlanAttachment,
  TeamPlanState,
  TeamPlanStatus,
  TeamPlanStep,
} from "../../types/teamPlan";

interface TeamPlanPanelProps {
  plan: TeamPlanState | null;
  onRespond: (
    approvalId: string,
    response: Record<string, unknown>,
    approved: boolean,
  ) => void | Promise<unknown>;
  isLoading?: boolean;
}

const statusClasses: Record<TeamPlanStatus, string> = {
  proposed: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  awaiting_confirmation: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  approved: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  rejected: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
  planning: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  running: "bg-indigo-500/10 text-indigo-700 dark:text-indigo-300",
  synthesizing: "bg-indigo-500/10 text-indigo-700 dark:text-indigo-300",
  partial_failure: "bg-orange-500/10 text-orange-700 dark:text-orange-300",
  completed: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  cancelled: "bg-stone-500/10 text-stone-600 dark:text-stone-300",
  failed: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
};

function statusLabel(status: TeamPlanStatus, t: ReturnType<typeof useTranslation>["t"]): string {
  return t(`teamPlan.status.${status}`, status.replaceAll("_", " "));
}

function StepIcon({ status }: { status: TeamPlanStep["status"] }) {
  if (status === "succeeded") return <CheckCircle2 size={16} className="text-emerald-600" />;
  if (status === "failed") return <XCircle size={16} className="text-rose-600" />;
  if (status === "running" || status === "retrying") {
    return <Loader2 size={16} className="animate-spin text-indigo-600" />;
  }
  if (status === "cancelled" || status === "skipped") return <CircleAlert size={16} className="text-stone-500" />;
  return <Circle size={16} className="text-stone-400" />;
}

function AttachmentRow({ attachment, t }: { attachment: TeamPlanAttachment; t: ReturnType<typeof useTranslation>["t"] }) {
  const failed = attachment.status === "failed";
  const ready = attachment.status === "materialized";
  return (
    <li className="flex items-start gap-2 rounded-md border border-[var(--theme-border)] px-2.5 py-2 text-xs">
      {ready ? <FileCheck2 size={15} className="mt-0.5 shrink-0 text-emerald-600" /> : failed ? <XCircle size={15} className="mt-0.5 shrink-0 text-rose-600" /> : <Clock3 size={15} className="mt-0.5 shrink-0 text-amber-600" />}
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-[var(--theme-text)]">{attachment.name}</span>
        <span className={`block ${failed ? "text-rose-600" : "text-[var(--theme-text-tertiary)]"}`}>
          {failed ? attachment.error?.message ?? t("teamPlan.attachmentFailed", "Materialization failed") : ready ? t("teamPlan.materialized", "Ready in sandbox") : t("teamPlan.materializing", "Preparing in sandbox")}
        </span>
        {ready && attachment.sandbox_path && <span className="block truncate font-mono text-[10px] text-[var(--theme-text-tertiary)]">{attachment.sandbox_path}</span>}
      </span>
    </li>
  );
}

export function TeamPlanPanel({ plan, onRespond, isLoading = false }: TeamPlanPanelProps) {
  const { t } = useTranslation();
  const [feedback, setFeedback] = useState("");
  if (!plan) return null;

  const canRespond = Boolean(plan.approval_id) && (plan.status === "proposed" || plan.status === "awaiting_confirmation");
  const submitResponse = (approved: boolean) => {
    if (!plan.approval_id || isLoading) return;
    onRespond(plan.approval_id, feedback.trim() ? { feedback: feedback.trim() } : {}, approved);
  };

  return (
    <section className="w-full max-h-[72dvh] shrink min-h-0 overflow-y-auto border-y border-[var(--theme-border)] bg-[var(--theme-bg)] px-3 py-3 sm:px-4" aria-label={t("teamPlan.title", "Team plan")}>
      <div className="mx-auto max-w-4xl rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-sm">
        <div className="flex items-start gap-3 border-b border-[var(--theme-border)] px-3 py-3 sm:px-4">
          <div className="mt-0.5 rounded-md bg-[var(--theme-primary-light)] p-1.5 text-[var(--theme-primary)]"><GitBranch size={17} /></div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-[var(--theme-text)]">{t("teamPlan.title", "Team plan")}</h2>
            <p className="mt-0.5 text-sm text-[var(--theme-text-secondary)]">{plan.summary || t("teamPlan.noSummary", "The team prepared this execution plan.")}</p>
          </div>
          <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${statusClasses[plan.status]}`}>{statusLabel(plan.status, t)}</span>
        </div>

        {plan.attachments.length > 0 && (
          <div className="border-b border-[var(--theme-border)] px-3 py-3 sm:px-4">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--theme-text-secondary)]">{t("teamPlan.attachments", "Attachments")}</h3>
            <ul className="grid gap-2 sm:grid-cols-2">{plan.attachments.map((attachment) => <AttachmentRow key={attachment.attachment_id} attachment={attachment} t={t} />)}</ul>
          </div>
        )}

        <div className="px-3 py-3 sm:px-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--theme-text-secondary)]">{t("teamPlan.steps", "Execution steps")}</h3>
          <ol className="space-y-2">
            {plan.steps.map((step, index) => (
              <li key={step.step_id} className="rounded-md border border-[var(--theme-border)] px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--theme-bg-subtle)] text-[10px] font-semibold text-[var(--theme-text-secondary)]">{step.order || index + 1}</span>
                  <StepIcon status={step.status} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                      <h4 className="text-sm font-medium text-[var(--theme-text)]">{step.objective || t("teamPlan.untitledStep", "Untitled step")}</h4>
                      {(step.member_name || step.subagent_type) && <span className="text-xs text-[var(--theme-text-tertiary)]">{step.member_name ?? step.subagent_type}</span>}
                    </div>
                    {step.dependencies.length > 0 && <p className="mt-1 text-xs text-[var(--theme-text-tertiary)]"><span className="font-medium">{t("teamPlan.dependsOn", "Depends on")}:</span> {step.dependencies.join(", ")}</p>}
                    {step.expected_artifacts.length > 0 && <p className="mt-1 text-xs text-[var(--theme-text-tertiary)]"><span className="font-medium">{t("teamPlan.outputs", "Expected outputs")}:</span> {step.expected_artifacts.join(", ")}</p>}
                    {step.error && <p className="mt-1 text-xs text-rose-600">{step.error}</p>}
                  </div>
                  {step.attempt && step.attempt > 1 && <span className="text-[10px] text-[var(--theme-text-tertiary)]">{t("teamPlan.attempt", "Attempt {{count}}", { count: step.attempt })}</span>}
                </div>
              </li>
            ))}
          </ol>
        </div>

        {plan.rejection_feedback && <div className="mx-3 mb-3 flex items-start gap-2 rounded-md bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-300 sm:mx-4"><MessageSquare size={14} className="mt-0.5 shrink-0" /><span>{plan.rejection_feedback}</span></div>}

        {canRespond && (
          <div className="border-t border-[var(--theme-border)] px-3 py-3 sm:px-4">
            <label className="mb-1.5 block text-xs font-medium text-[var(--theme-text-secondary)]" htmlFor="team-plan-feedback">{t("teamPlan.feedbackLabel", "Feedback (optional)")}</label>
            <textarea id="team-plan-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} rows={2} className="w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2.5 py-2 text-sm text-[var(--theme-text)] outline-none focus:border-[var(--theme-primary)]" placeholder={t("teamPlan.feedbackPlaceholder", "Tell the team what to adjust if rejecting")}/>
            <div className="mt-2 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button type="button" onClick={() => submitResponse(false)} disabled={isLoading} className="inline-flex items-center justify-center gap-1.5 rounded-md border border-[var(--theme-border)] px-3 py-2 text-sm text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-subtle)] disabled:cursor-not-allowed disabled:opacity-50"><X size={14} />{t("teamPlan.reject", "Reject plan")}</button>
              <button type="button" onClick={() => submitResponse(true)} disabled={isLoading} className="inline-flex items-center justify-center gap-1.5 rounded-md bg-[var(--theme-primary)] px-3 py-2 text-sm text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">{isLoading ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}{t("teamPlan.approve", "Approve and run")}</button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
