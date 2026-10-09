import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  CalendarClock,
  History,
  Pause,
  Pencil,
  Play,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";
import { PanelHeader } from "../../common/PanelHeader";
import { PanelLoadingState } from "../../common/PanelLoadingState";
import { ConfirmDialog } from "../../common/ConfirmDialog";
import { useSettingsContext } from "../../../contexts/SettingsContext";
import { scheduledTaskApi } from "../../../services/api/scheduledTask";
import type {
  ScheduledTask,
  ScheduledTaskTemplate,
} from "../../../types/scheduledTask";
import { TaskEditorDialog } from "./TaskEditorDialog";
import { TaskRunsDrawer } from "./TaskRunsDrawer";
import { STATUS_STYLES, formatFireTime, relativeFromNow } from "./scheduleUtils";

export const SCHEDULED_TASK_REFRESH_EVENT = "scheduled-task:refresh";

export function ScheduledTaskPanel() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const {
    settings,
    isLoading: settingsLoading,
    enableScheduledTasks,
  } = useSettingsContext();
  // getBooleanSetting is false while settings are still loading; only trust it once loaded.
  const featureDisabled =
    settings !== null && !settingsLoading && !enableScheduledTasks;
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [limit, setLimit] = useState(20);
  const [templates, setTemplates] = useState<ScheduledTaskTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ScheduledTask | null>(null);
  const [creating, setCreating] = useState<{ template: ScheduledTaskTemplate | null } | null>(null);
  const [runsFor, setRunsFor] = useState<ScheduledTask | null>(null);
  const [deleting, setDeleting] = useState<ScheduledTask | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await scheduledTaskApi.list();
      setTasks(res.items);
      setLimit(res.limit);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    scheduledTaskApi.templates().then(setTemplates).catch(() => setTemplates([]));
    const onRefresh = () => void load();
    window.addEventListener(SCHEDULED_TASK_REFRESH_EVENT, onRefresh);
    return () => window.removeEventListener(SCHEDULED_TASK_REFRESH_EVENT, onRefresh);
  }, [load]);

  const replace = (task: ScheduledTask) =>
    setTasks((prev) => {
      const idx = prev.findIndex((x) => x.id === task.id);
      if (idx < 0) return [task, ...prev];
      const next = [...prev];
      next[idx] = task;
      return next;
    });

  const toggle = async (task: ScheduledTask) => {
    setBusyId(task.id);
    try {
      const updated = task.enabled
        ? await scheduledTaskApi.pause(task.id)
        : await scheduledTaskApi.resume(task.id);
      replace(updated);
      toast.success(
        updated.enabled
          ? t("scheduledTask.toast.resumed", "已启用，到点会自动执行")
          : t("scheduledTask.toast.paused", "已暂停，不会再自动执行"),
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      if (task.schedule.kind === "once") setEditing(task);
    } finally {
      setBusyId(null);
    }
  };

  const runNow = async (task: ScheduledTask) => {
    setBusyId(task.id);
    try {
      await scheduledTaskApi.runNow(task.id);
      toast.success(
        t("scheduledTask.toast.runStarted", "已开始执行，完成后会通知你"),
      );
      setRunsFor(task);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    try {
      await scheduledTaskApi.remove(deleting.id);
      setTasks((prev) => prev.filter((x) => x.id !== deleting.id));
      toast.success(t("scheduledTask.toast.deleted", "已删除"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(null);
    }
  };

  const atLimit = tasks.length >= limit;
  const openCreate = (template: ScheduledTaskTemplate | null = null) => {
    if (featureDisabled) return;
    if (atLimit) {
      toast.error(t("scheduledTask.limitReached", { limit, defaultValue: "最多创建 {{limit}} 个定时任务" }));
      return;
    }
    setCreating({ template });
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PanelHeader
        title={t("scheduledTask.title", "定时任务")}
        subtitle={t(
          "scheduledTask.subtitle",
          "设定好时间，昆小智会自动帮你完成，并把结果通知你",
        )}
        icon={<CalendarClock />}
        actions={
          <button
            type="button"
            onClick={() => openCreate()}
            disabled={featureDisabled}
            className="inline-flex items-center gap-1.5 rounded-xl bg-stone-900 px-4 py-2 text-sm font-medium text-white hover:bg-stone-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-stone-100 dark:text-stone-900"
          >
            <Plus size={16} />
            {t("scheduledTask.create", "新建定时任务")}
          </button>
        }
      />

      {featureDisabled && (
        <div
          role="alert"
          className="mx-4 mb-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800 sm:mx-6 dark:bg-amber-900/30 dark:text-amber-200"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          {t(
            "scheduledTask.disabledBanner",
            "定时任务功能已被管理员关闭，已有任务暂停执行，可以查看和删除",
          )}
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-4 pb-8 sm:px-6">
        {loading ? (
          <PanelLoadingState text={t("common.loading", "加载中...")} />
        ) : tasks.length === 0 ? (
          <div className="mx-auto mt-6 max-w-3xl">
            <div className="rounded-3xl border border-stone-200 p-6 text-center dark:border-stone-800">
              <CalendarClock className="mx-auto mb-3 text-stone-400" size={36} />
              <h3 className="text-lg font-semibold text-stone-900 dark:text-stone-100">
                {t("scheduledTask.emptyTitle", "还没有定时任务")}
              </h3>
              <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
                {t(
                  "scheduledTask.emptyDesc",
                  "选一个常用场景，一分钟就能设置好。例如每个工作日早上自动整理今日工作重点。",
                )}
              </p>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {templates.map((tpl) => (
                <button
                  key={tpl.id}
                  type="button"
                  onClick={() => openCreate(tpl)}
                  className="rounded-2xl border border-stone-200 p-4 text-left transition hover:border-stone-400 hover:shadow-sm dark:border-stone-700"
                >
                  <div className="font-medium text-stone-900 dark:text-stone-100">{tpl.title}</div>
                  <div className="mt-1 text-sm text-stone-500 dark:text-stone-400">{tpl.description}</div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="mb-3 mt-2 text-xs text-stone-500 dark:text-stone-400">
              {t("scheduledTask.quota", {
                count: tasks.length,
                limit,
                defaultValue: "已创建 {{count}} / {{limit}} 个",
              })}
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {tasks.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  locale={i18n.language}
                  busy={busyId === task.id}
                  onToggle={() => toggle(task)}
                  onRun={() => runNow(task)}
                  onEdit={() => setEditing(task)}
                  onRuns={() => setRunsFor(task)}
                  onDelete={() => setDeleting(task)}
                  onOpenLast={() =>
                    task.last_session_id && navigate(`/chat/${task.last_session_id}`)
                  }
                />
              ))}
            </div>
          </>
        )}
      </div>

      {(creating || editing) && (
        <TaskEditorDialog
          task={editing}
          templates={templates}
          initialTemplate={creating?.template ?? null}
          onClose={() => {
            setCreating(null);
            setEditing(null);
          }}
          onSaved={(saved) => {
            replace(saved);
            setCreating(null);
            setEditing(null);
          }}
        />
      )}
      {runsFor && <TaskRunsDrawer task={runsFor} onClose={() => setRunsFor(null)} />}
      <ConfirmDialog
        isOpen={!!deleting}
        title={t("scheduledTask.deleteTitle", "删除定时任务")}
        message={t("scheduledTask.deleteConfirm", {
          name: deleting?.name ?? "",
          defaultValue: "确定删除“{{name}}”吗？删除后不会再自动执行，运行记录也会一并删除。",
        })}
        confirmText={t("common.delete", "删除")}
        variant="danger"
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

interface TaskCardProps {
  task: ScheduledTask;
  locale: string;
  busy: boolean;
  onToggle: () => void;
  onRun: () => void;
  onEdit: () => void;
  onRuns: () => void;
  onDelete: () => void;
  onOpenLast: () => void;
}

function TaskCard({ task, locale, busy, onToggle, onRun, onEdit, onRuns, onDelete, onOpenLast }: TaskCardProps) {
  const { t } = useTranslation();
  const actionBtn =
    "inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs text-stone-600 hover:bg-stone-100 disabled:opacity-40 dark:text-stone-300 dark:hover:bg-stone-800";
  return (
    <div
      className={`rounded-2xl border p-4 transition dark:bg-stone-900 ${
        task.enabled
          ? "border-stone-200 bg-white dark:border-stone-700"
          : "border-stone-200 bg-stone-50 opacity-90 dark:border-stone-800 dark:bg-stone-900/60"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-semibold text-stone-900 dark:text-stone-100">{task.name}</div>
          <div className="mt-0.5 text-sm text-stone-600 dark:text-stone-300">{task.schedule_text}</div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={task.enabled}
          disabled={busy}
          onClick={onToggle}
          title={task.enabled ? t("scheduledTask.pause", "暂停") : t("scheduledTask.resume", "启用")}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
            task.enabled ? "bg-emerald-500" : "bg-stone-300 dark:bg-stone-600"
          }`}
        >
          <span
            className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${
              task.enabled ? "left-[22px]" : "left-0.5"
            }`}
          />
        </button>
      </div>

      <p className="mt-2 line-clamp-2 text-sm text-stone-500 dark:text-stone-400">{task.prompt}</p>

      {task.pause_reason && (
        <div className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-900/30 dark:text-amber-200">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {task.pause_reason}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-stone-500 dark:text-stone-400">
        {task.status === "completed" ? (
          <span>{t("scheduledTask.completed", "已完成（一次性任务）")}</span>
        ) : task.enabled && task.next_run_at ? (
          <span>
            {t("scheduledTask.nextRun", "下次执行")}：{formatFireTime(task.next_run_at, locale)}
            <span className="ml-1 text-stone-400">({relativeFromNow(task.next_run_at, locale)})</span>
          </span>
        ) : (
          <span>{t("scheduledTask.pausedLabel", "已暂停")}</span>
        )}
        {task.last_run_status && (
          <button type="button" onClick={onOpenLast} className="inline-flex items-center gap-1 hover:underline">
            {t("scheduledTask.lastRun", "上次")}：
            <span className={`rounded-md px-1.5 py-0.5 ${STATUS_STYLES[task.last_run_status]}`}>
              {t(`scheduledTask.runStatus.${task.last_run_status}`)}
            </span>
          </button>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-1 border-t border-stone-100 pt-3 dark:border-stone-800">
        <button type="button" className={actionBtn} disabled={busy} onClick={onRun}>
          <Zap size={14} />
          {t("scheduledTask.runNow", "立即执行一次")}
        </button>
        <button type="button" className={actionBtn} onClick={onRuns}>
          <History size={14} />
          {t("scheduledTask.runs", "运行记录")}
        </button>
        <button type="button" className={actionBtn} onClick={onEdit}>
          <Pencil size={14} />
          {t("common.edit", "编辑")}
        </button>
        <button type="button" className={actionBtn} disabled={busy} onClick={onToggle}>
          {task.enabled ? <Pause size={14} /> : <Play size={14} />}
          {task.enabled ? t("scheduledTask.pause", "暂停") : t("scheduledTask.resume", "启用")}
        </button>
        <button
          type="button"
          className={`${actionBtn} ml-auto text-rose-600 dark:text-rose-400`}
          onClick={onDelete}
        >
          <Trash2 size={14} />
          {t("common.delete", "删除")}
        </button>
      </div>
    </div>
  );
}
