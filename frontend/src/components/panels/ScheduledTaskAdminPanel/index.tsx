import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  CalendarClock,
  History,
  Pause,
  Play,
  Search,
  Trash2,
} from "lucide-react";
import { PanelHeader } from "../../common/PanelHeader";
import { PanelLoadingState } from "../../common/PanelLoadingState";
import { ConfirmDialog } from "../../common/ConfirmDialog";
import { Pagination } from "../../common/Pagination";
import { scheduledTaskAdminApi } from "../../../services/api/scheduledTaskAdmin";
import type {
  RunStatus,
  ScheduledTaskAdminItem,
  ScheduledTaskStatus,
} from "../../../types/scheduledTask";
import { PersonaAvatarBadge } from "../ScheduledTaskPanel/PersonaAvatarBadge";
import { TaskRunsDrawer } from "../ScheduledTaskPanel/TaskRunsDrawer";
import { STATUS_STYLES, formatFireTime } from "../ScheduledTaskPanel/scheduleUtils";
import { PauseReasonDialog } from "./PauseReasonDialog";

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;

const STATUS_OPTIONS: ScheduledTaskStatus[] = ["active", "paused", "completed"];
const LAST_RUN_OPTIONS: RunStatus[] = ["success", "failed", "timeout", "skipped"];

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

const controlClass =
  "h-10 rounded-xl border border-stone-200 bg-white px-3 text-sm text-stone-900 outline-none focus:border-stone-400 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100";

export function ScheduledTaskAdminPanel() {
  const { t, i18n } = useTranslation();
  const [items, setItems] = useState<ScheduledTaskAdminItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const [searchInput, setSearchInput] = useState("");
  const [ownerInput, setOwnerInput] = useState("");
  const [status, setStatus] = useState<ScheduledTaskStatus | "">("");
  const [lastRunStatus, setLastRunStatus] = useState<RunStatus | "">("");
  const q = useDebounce(searchInput.trim(), SEARCH_DEBOUNCE_MS);
  const owner = useDebounce(ownerInput.trim(), SEARCH_DEBOUNCE_MS);

  // 页码与筛选条件绑定：任一筛选变化后，旧页码自动失效并回到第 1 页。
  const filterKey = JSON.stringify([q, owner, status, lastRunStatus]);
  const [pageState, setPageState] = useState({ key: filterKey, page: 1 });
  const page = pageState.key === filterKey ? pageState.page : 1;
  const setPage = (next: number) => setPageState({ key: filterKey, page: next });

  const [runsFor, setRunsFor] = useState<ScheduledTaskAdminItem | null>(null);
  const [pausing, setPausing] = useState<ScheduledTaskAdminItem | null>(null);
  const [deleting, setDeleting] = useState<ScheduledTaskAdminItem | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const res = await scheduledTaskAdminApi.list({
        skip: (page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
        q,
        owner,
        status,
        last_run_status: lastRunStatus,
      });
      if (seq !== requestSeq.current) return;
      // 删除当前页最后一条后页码越界：回退到最后一页。
      if (res.items.length === 0 && res.total > 0 && page > 1) {
        setPageState({
          key: filterKey,
          page: Math.max(1, Math.ceil(res.total / PAGE_SIZE)),
        });
        return;
      }
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      if (seq !== requestSeq.current) return;
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [page, q, owner, status, lastRunStatus, filterKey]);

  useEffect(() => {
    void load();
  }, [load]);

  const errorMessage = (e: unknown) => (e instanceof Error ? e.message : String(e));

  const confirmPause = async (reason: string) => {
    if (!pausing) return;
    setBusyId(pausing.id);
    try {
      await scheduledTaskAdminApi.pause(pausing.id, reason);
      toast.success(t("scheduledTaskAdmin.toast.paused", "已暂停该任务"));
      setPausing(null);
      await load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  const resume = async (task: ScheduledTaskAdminItem) => {
    setBusyId(task.id);
    try {
      await scheduledTaskAdminApi.resume(task.id);
      toast.success(t("scheduledTaskAdmin.toast.resumed", "已恢复该任务"));
      await load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    const target = deleting;
    setBusyId(target.id);
    try {
      await scheduledTaskAdminApi.remove(target.id);
      toast.success(t("scheduledTaskAdmin.toast.deleted", "已删除该任务"));
      setDeleting(null);
      await load();
    } catch (e) {
      toast.error(errorMessage(e));
      setDeleting(null);
    } finally {
      setBusyId(null);
    }
  };

  const hasFilter = Boolean(q || owner || status || lastRunStatus);
  const locale = i18n.language;

  const rowProps = (task: ScheduledTaskAdminItem) => ({
    task,
    locale,
    busy: busyId === task.id,
    onRuns: () => setRunsFor(task),
    onPause: () => setPausing(task),
    onResume: () => void resume(task),
    onDelete: () => setDeleting(task),
  });

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PanelHeader
        title={t("scheduledTaskAdmin.title", "定时任务管理")}
        subtitle={t("scheduledTaskAdmin.subtitle", "查看与管控所有用户的定时任务")}
        icon={<CalendarClock />}
      />

      <div className="flex flex-wrap items-center gap-2 px-4 pb-3 sm:px-6">
        <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-stone-400"
          />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("scheduledTaskAdmin.searchPlaceholder", "搜索任务名")}
            aria-label={t("scheduledTaskAdmin.searchPlaceholder", "搜索任务名")}
            className={`${controlClass} w-full pl-9`}
          />
        </div>
        <input
          type="text"
          value={ownerInput}
          onChange={(e) => setOwnerInput(e.target.value)}
          placeholder={t("scheduledTaskAdmin.ownerPlaceholder", "创建人用户名")}
          aria-label={t("scheduledTaskAdmin.ownerPlaceholder", "创建人用户名")}
          className={`${controlClass} w-40`}
        />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as ScheduledTaskStatus | "")}
          aria-label={t("scheduledTaskAdmin.filter.status", "状态")}
          className={controlClass}
        >
          <option value="">{t("scheduledTaskAdmin.filter.statusAll", "全部状态")}</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {t(`scheduledTaskAdmin.statusLabel.${s}`, STATUS_FALLBACK[s])}
            </option>
          ))}
        </select>
        <select
          value={lastRunStatus}
          onChange={(e) => setLastRunStatus(e.target.value as RunStatus | "")}
          aria-label={t("scheduledTaskAdmin.filter.lastRun", "最近结果")}
          className={controlClass}
        >
          <option value="">{t("scheduledTaskAdmin.filter.lastRunAll", "全部结果")}</option>
          {LAST_RUN_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {t(`scheduledTask.runStatus.${s}`, RUN_FALLBACK[s])}
            </option>
          ))}
        </select>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-8 sm:px-6">
        {loading && items.length === 0 ? (
          <PanelLoadingState text={t("common.loading", "加载中...")} />
        ) : items.length === 0 ? (
          <div className="mt-10 flex flex-col items-center text-center text-stone-500 dark:text-stone-400">
            <CalendarClock size={40} className="mb-3 opacity-40" />
            <p>
              {hasFilter
                ? t("scheduledTaskAdmin.emptyFiltered", "没有符合条件的任务")
                : t("scheduledTaskAdmin.empty", "暂无定时任务")}
            </p>
          </div>
        ) : (
          <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
            {/* 桌面端：表格，窄于 lg 时横向滚动 */}
            <div className="hidden overflow-x-auto rounded-xl border border-stone-200 md:block dark:border-stone-700">
              <table className="min-w-[56rem] w-full divide-y divide-stone-200 text-sm dark:divide-stone-700">
                <thead className="bg-stone-50 dark:bg-stone-800/60">
                  <tr className="text-left text-xs font-medium text-stone-500 dark:text-stone-400">
                    {COLUMNS.map((col) => (
                      <th key={col.key} scope="col" className="whitespace-nowrap px-3 py-2.5">
                        {t(`scheduledTaskAdmin.columns.${col.key}`, col.label)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
                  {items.map((task) => (
                    <TaskRow key={task.id} {...rowProps(task)} />
                  ))}
                </tbody>
              </table>
            </div>
            {/* 窄屏：卡片列表 */}
            <div className="space-y-3 md:hidden">
              {items.map((task) => (
                <TaskAdminCard key={task.id} {...rowProps(task)} />
              ))}
            </div>
            <div className="mt-4">
              <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
            </div>
          </div>
        )}
      </div>

      {runsFor && (
        <TaskRunsDrawer
          task={runsFor}
          onClose={() => setRunsFor(null)}
          fetchRuns={scheduledTaskAdminApi.runs}
          allowOpenSession={false}
        />
      )}
      {pausing && (
        <PauseReasonDialog
          taskName={pausing.name}
          loading={busyId === pausing.id}
          onConfirm={(reason) => void confirmPause(reason)}
          onCancel={() => setPausing(null)}
        />
      )}
      <ConfirmDialog
        isOpen={!!deleting}
        title={t("scheduledTaskAdmin.deleteTitle", "删除定时任务")}
        message={t("scheduledTaskAdmin.deleteConfirm", {
          name: deleting?.name ?? "",
          owner: deleting?.owner_name ?? deleting?.owner_id ?? "",
          defaultValue:
            "确定删除 {{owner}} 的定时任务“{{name}}”吗？删除后不会再自动执行，运行记录也会一并删除，且无法恢复。",
        })}
        confirmText={t("common.delete", "删除")}
        variant="danger"
        loading={!!deleting && busyId === deleting.id}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

const STATUS_FALLBACK: Record<ScheduledTaskStatus, string> = {
  active: "启用中",
  paused: "已暂停",
  completed: "已完成",
};

const RUN_FALLBACK: Record<RunStatus, string> = {
  pending: "排队中",
  running: "执行中",
  success: "成功",
  failed: "失败",
  timeout: "超时",
  skipped: "已跳过",
};

const COLUMNS = [
  { key: "name", label: "任务名" },
  { key: "owner", label: "创建人" },
  { key: "schedule", label: "频率" },
  { key: "persona", label: "执行专家" },
  { key: "status", label: "状态" },
  { key: "lastRun", label: "最近结果" },
  { key: "nextRun", label: "下次执行" },
  { key: "actions", label: "操作" },
] as const;

interface RowProps {
  task: ScheduledTaskAdminItem;
  locale: string;
  busy: boolean;
  onRuns: () => void;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
}

function ownerLabel(task: ScheduledTaskAdminItem): string {
  return task.owner_name || task.owner_id;
}

function NameCell({ task }: { task: ScheduledTaskAdminItem }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="truncate font-medium text-stone-900 dark:text-stone-100" title={task.name}>
        {task.name}
      </span>
      {task.created_via === "chat" && (
        <span className="inline-flex shrink-0 items-center rounded-full bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
          {t("scheduledTask.createdViaChat", "聊天创建")}
        </span>
      )}
    </div>
  );
}

function PersonaCell({ task }: { task: ScheduledTaskAdminItem }) {
  const { t } = useTranslation();
  if (!task.persona_preset_id) {
    return (
      <span className="text-stone-400">
        {t("scheduledTaskAdmin.personaDefault", "默认助手")}
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="inline-flex max-w-[10rem] items-center gap-1.5 rounded-full bg-stone-100 py-0.5 pl-1 pr-2.5 text-xs text-stone-700 dark:bg-stone-800 dark:text-stone-200">
        <PersonaAvatarBadge avatar={task.persona_avatar} size={18} />
        <span className="truncate">{task.persona_name || "—"}</span>
      </span>
      {task.persona_available === false && (
        <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800 dark:bg-amber-900/30 dark:text-amber-200">
          <AlertTriangle size={11} className="shrink-0" />
          {t("scheduledTaskAdmin.personaUnavailable", "不可用")}
        </span>
      )}
    </div>
  );
}

function StatusCell({ task }: { task: ScheduledTaskAdminItem }) {
  const { t } = useTranslation();
  if (task.status === "paused") {
    const source = task.paused_by === "admin" ? "admin" : task.paused_by === "system" ? "system" : "user";
    const label = t(
      `scheduledTaskAdmin.pausedBy.${source}`,
      source === "admin" ? "管理员暂停" : source === "system" ? "系统暂停" : "用户暂停",
    );
    const tone =
      source === "admin"
        ? "bg-rose-50 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300"
        : "bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
    return (
      <div className="min-w-0">
        <span
          title={task.pause_reason || undefined}
          className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}
        >
          {label}
        </span>
        {task.pause_reason && (
          <div
            title={task.pause_reason}
            className="mt-1 line-clamp-2 max-w-[14rem] text-xs text-stone-500 dark:text-stone-400"
          >
            {task.pause_reason}
          </div>
        )}
      </div>
    );
  }
  const tone =
    task.status === "completed"
      ? "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300"
      : "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
  return (
    <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}>
      {t(`scheduledTaskAdmin.statusLabel.${task.status}`, STATUS_FALLBACK[task.status])}
    </span>
  );
}

function LastRunCell({ task, locale }: { task: ScheduledTaskAdminItem; locale: string }) {
  const { t } = useTranslation();
  if (!task.last_run_status) return <span className="text-stone-400">—</span>;
  return (
    <span
      title={task.last_run_at ? formatFireTime(task.last_run_at, locale) : undefined}
      className={`inline-flex rounded-md px-2 py-0.5 text-xs ${STATUS_STYLES[task.last_run_status]}`}
    >
      {t(`scheduledTask.runStatus.${task.last_run_status}`, RUN_FALLBACK[task.last_run_status])}
    </span>
  );
}

function nextRunText(task: ScheduledTaskAdminItem, locale: string): string {
  return task.status === "active" && task.next_run_at ? formatFireTime(task.next_run_at, locale) : "—";
}

const actionBtn =
  "inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2 py-1.5 text-xs text-stone-600 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-40 dark:text-stone-300 dark:hover:bg-stone-800";

function ActionButtons({ task, busy, onRuns, onPause, onResume, onDelete }: RowProps) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center gap-1">
      <button type="button" className={actionBtn} onClick={onRuns}>
        <History size={14} />
        {t("scheduledTaskAdmin.actions.runs", "运行记录")}
      </button>
      {task.status === "active" && (
        <button type="button" className={actionBtn} disabled={busy} onClick={onPause}>
          <Pause size={14} />
          {t("scheduledTaskAdmin.actions.pause", "暂停")}
        </button>
      )}
      {task.status === "paused" && (
        <button type="button" className={actionBtn} disabled={busy} onClick={onResume}>
          <Play size={14} />
          {t("scheduledTaskAdmin.actions.resume", "恢复")}
        </button>
      )}
      <button
        type="button"
        className={`${actionBtn} text-rose-600 dark:text-rose-400`}
        disabled={busy}
        onClick={onDelete}
      >
        <Trash2 size={14} />
        {t("scheduledTaskAdmin.actions.delete", "删除")}
      </button>
    </div>
  );
}

function TaskRow(props: RowProps) {
  const { task, locale } = props;
  return (
    <tr className="align-top">
      <td className="max-w-[16rem] px-3 py-3">
        <NameCell task={task} />
      </td>
      <td className="px-3 py-3 text-stone-700 dark:text-stone-200">{ownerLabel(task)}</td>
      <td className="px-3 py-3 text-stone-600 dark:text-stone-300">{task.schedule_text}</td>
      <td className="px-3 py-3">
        <PersonaCell task={task} />
      </td>
      <td className="px-3 py-3">
        <StatusCell task={task} />
      </td>
      <td className="px-3 py-3">
        <LastRunCell task={task} locale={locale} />
      </td>
      <td className="whitespace-nowrap px-3 py-3 text-stone-600 dark:text-stone-300">
        {nextRunText(task, locale)}
      </td>
      <td className="px-3 py-3">
        <ActionButtons {...props} />
      </td>
    </tr>
  );
}

function TaskAdminCard(props: RowProps) {
  const { t } = useTranslation();
  const { task, locale } = props;
  return (
    <div className="rounded-2xl border border-stone-200 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
      <NameCell task={task} />
      <div className="mt-1 text-xs text-stone-500 dark:text-stone-400">
        {t("scheduledTaskAdmin.columns.owner", "创建人")}：{ownerLabel(task)} · {task.schedule_text}
      </div>
      <div className="mt-2">
        <PersonaCell task={task} />
      </div>
      <div className="mt-3 flex flex-wrap items-start gap-x-4 gap-y-2 text-xs text-stone-500 dark:text-stone-400">
        <StatusCell task={task} />
        <span className="inline-flex items-center gap-1">
          {t("scheduledTaskAdmin.columns.lastRun", "最近结果")}：
          <LastRunCell task={task} locale={locale} />
        </span>
        <span>
          {t("scheduledTaskAdmin.columns.nextRun", "下次执行")}：{nextRunText(task, locale)}
        </span>
      </div>
      <div className="mt-3 border-t border-stone-100 pt-3 dark:border-stone-800">
        <ActionButtons {...props} />
      </div>
    </div>
  );
}
