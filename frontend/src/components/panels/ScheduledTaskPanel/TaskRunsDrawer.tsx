import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { ExternalLink, Loader2, RefreshCw, X } from "lucide-react";
import { scheduledTaskApi } from "../../../services/api/scheduledTask";
import type { ScheduledTask, TaskRun } from "../../../types/scheduledTask";
import { STATUS_STYLES, formatDuration, formatFireTime } from "./scheduleUtils";

const PAGE = 20;

export function TaskRunsDrawer({ task, onClose }: { task: ScheduledTask; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<TaskRun[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (skip = 0) => {
      setLoading(true);
      try {
        const res = await scheduledTaskApi.runs(task.id, skip, PAGE);
        setRuns((prev) => (skip === 0 ? res.items : [...prev, ...res.items]));
        setTotal(res.total);
      } finally {
        setLoading(false);
      }
    },
    [task.id],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  // Auto-refresh while something is still running.
  const hasRunning = runs.some((r) => r.status === "running" || r.status === "pending");
  useEffect(() => {
    if (!hasRunning) return;
    const id = window.setInterval(() => void load(0), 5000);
    return () => window.clearInterval(id);
  }, [hasRunning, load]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-lg flex-col bg-white shadow-2xl dark:bg-stone-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-stone-100 px-5 py-4 dark:border-stone-800">
          <div className="min-w-0">
            <div className="truncate font-semibold text-stone-900 dark:text-stone-100">
              {t("scheduledTask.runsTitle", { name: task.name, defaultValue: "{{name}} · 运行记录" })}
            </div>
            <div className="text-xs text-stone-500 dark:text-stone-400">{task.schedule_text}</div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => void load(0)}
              className="rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
              aria-label={t("common.refresh", "刷新")}
            >
              <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
              aria-label={t("common.close", "关闭")}
            >
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {!loading && runs.length === 0 && (
            <div className="py-12 text-center text-sm text-stone-500 dark:text-stone-400">
              {t("scheduledTask.noRuns", "还没有执行过。可以点“立即执行一次”试试效果。")}
            </div>
          )}
          {runs.map((run) => (
            <div key={run.id} className="rounded-2xl border border-stone-200 p-4 dark:border-stone-700">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className={`rounded-md px-2 py-0.5 font-medium ${STATUS_STYLES[run.status]}`}>
                  {(run.status === "running" || run.status === "pending") && (
                    <Loader2 size={12} className="mr-1 inline animate-spin" />
                  )}
                  {t(`scheduledTask.runStatus.${run.status}`)}
                </span>
                <span className="text-stone-500 dark:text-stone-400">
                  {run.started_at ? formatFireTime(run.started_at, i18n.language) : ""}
                  {run.trigger === "manual" && ` · ${t("scheduledTask.manual", "手动")}`}
                  {run.duration_ms != null && ` · ${formatDuration(run.duration_ms)}`}
                </span>
              </div>
              {run.result_preview && (
                <p className="mt-2 whitespace-pre-wrap text-sm text-stone-700 dark:text-stone-200">
                  {run.result_preview}
                </p>
              )}
              {run.error_message && (
                <p className="mt-2 text-sm text-rose-600 dark:text-rose-400">{run.error_message}</p>
              )}
              {run.session_id && run.status !== "pending" && (
                <button
                  type="button"
                  onClick={() => navigate(`/chat/${run.session_id}`)}
                  className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-stone-900 hover:underline dark:text-stone-100"
                >
                  <ExternalLink size={14} />
                  {t("scheduledTask.viewResult", "查看完整结果")}
                </button>
              )}
            </div>
          ))}
          {runs.length < total && (
            <button
              type="button"
              disabled={loading}
              onClick={() => void load(runs.length)}
              className="w-full rounded-xl py-2 text-sm text-stone-600 hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800"
            >
              {t("common.loadMore", "加载更多")}
            </button>
          )}
        </div>
      </aside>
    </div>
  );
}
