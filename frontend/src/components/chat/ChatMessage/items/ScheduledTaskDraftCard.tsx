import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronUp,
  Eye,
  Loader2,
  X,
} from "lucide-react";
import { useAuth } from "../../../../hooks/useAuth";
import { ApiRequestError } from "../../../../services/api/fetch";
import { scheduledTaskApi } from "../../../../services/api/scheduledTask";
import { Permission } from "../../../../types/auth";
import type {
  ScheduledTaskDraft,
  ScheduledTaskDraftChangeKey,
  ScheduledTaskDraftStatus,
  ScheduledTaskDraftSummary,
  ScheduledTaskDraftToolResult,
} from "../../../../types/scheduledTask";
import { dispatchScheduledTaskRefresh } from "../../../../utils/scheduledTaskEvents";
import { PersonaAvatarBadge } from "../../../panels/ScheduledTaskPanel/PersonaAvatarBadge";
import { formatFireTime } from "../../../panels/ScheduledTaskPanel/scheduleUtils";
import { DRAFT_CHANGE_LABELS } from "./scheduledTaskDraftResult";

const MAX_PREVIEW_RUNS = 5;
const LONG_PROMPT_CHARS = 80;
const TICK_MS = 30_000;

type Access = "loading" | "interactive" | "readonly";
type Busy = "confirm" | "cancel" | null;

function toMillis(iso?: string | null): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? null : ms;
}

function Row({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex gap-3 text-sm">
      <dt className="w-20 shrink-0 text-stone-500 dark:text-stone-400">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-stone-900 dark:text-stone-100">
        {children}
      </dd>
    </div>
  );
}

function PersonaValue({
  persona,
  fallback,
}: {
  persona: ScheduledTaskDraftSummary["persona"];
  fallback: string;
}) {
  if (!persona) {
    return <span className="text-stone-600 dark:text-stone-300">{fallback}</span>;
  }
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-stone-100 py-0.5 pl-1 pr-2.5 text-xs text-stone-700 dark:bg-stone-800 dark:text-stone-200">
      <PersonaAvatarBadge avatar={persona.avatar} size={18} />
      <span className="truncate">{persona.name}</span>
    </span>
  );
}

export function ScheduledTaskDraftCard({
  result,
}: {
  result: ScheduledTaskDraftToolResult;
}) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { isAuthenticated, hasAllPermissions } = useAuth();
  const canManage =
    isAuthenticated &&
    hasAllPermissions([Permission.SCHEDULED_TASK_USE, Permission.CHAT_WRITE]);

  const draftId = result.draft_id;
  const [access, setAccess] = useState<Access>(
    canManage ? "loading" : "readonly",
  );
  const [serverDraft, setServerDraft] = useState<ScheduledTaskDraft | null>(
    null,
  );
  const [busy, setBusy] = useState<Busy>(null);
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const loadDraft = useCallback(async (): Promise<boolean> => {
    try {
      const draft = await scheduledTaskApi.getDraft(draftId);
      setServerDraft(draft);
      setAccess("interactive");
      return true;
    } catch {
      // 404 / 403 / 其他失败：只展示摘要，不提供操作
      setAccess("readonly");
      return false;
    }
  }, [draftId]);

  useEffect(() => {
    // 未登录（如分享页）或无权限时绝不发请求，避免 401 触发跳转登录页
    if (!canManage) {
      setAccess("readonly");
      return;
    }
    let cancelled = false;
    setAccess("loading");
    scheduledTaskApi
      .getDraft(draftId)
      .then((draft) => {
        if (cancelled) return;
        setServerDraft(draft);
        setAccess("interactive");
      })
      .catch(() => {
        if (!cancelled) setAccess("readonly");
      });
    return () => {
      cancelled = true;
    };
  }, [canManage, draftId]);

  const rawStatus: ScheduledTaskDraftStatus | null = serverDraft?.status ?? null;
  const expiresAt = serverDraft?.expires_at ?? result.expires_at;
  const expiresMs = toMillis(expiresAt);

  useEffect(() => {
    if (rawStatus !== "pending" || expiresMs === null) return;
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [rawStatus, expiresMs]);

  const status: ScheduledTaskDraftStatus | null =
    rawStatus === "pending" && expiresMs !== null && expiresMs <= now
      ? "expired"
      : rawStatus;

  const isUpdate = result.action === "update";
  const { summary, before } = result;
  const changes = useMemo<ScheduledTaskDraftChangeKey[]>(
    () => serverDraft?.changes ?? result.changes,
    [serverDraft, result.changes],
  );
  const changeLabels = changes.map((key) =>
    t(`scheduledTask.draft.change.${key}`, DRAFT_CHANGE_LABELS[key]),
  );
  const locale = i18n.language;
  const lastError = serverDraft?.last_error ?? null;
  const defaultPersonaLabel = t(
    "scheduledTask.draft.defaultAssistant",
    "默认助手",
  );
  const enabledLabel = (enabled: boolean) =>
    enabled
      ? t("scheduledTask.draft.enabledOn", "启用，到点自动执行")
      : t("scheduledTask.draft.enabledOff", "暂停，不会自动执行");

  const remainingText = useMemo(() => {
    if (expiresMs === null) return null;
    const minutes = Math.ceil((expiresMs - now) / 60_000);
    if (minutes <= 0) return null;
    return minutes <= 1
      ? t("scheduledTask.draft.expiresSoon", "不到 1 分钟内有效")
      : t("scheduledTask.draft.expiresIn", {
          count: minutes,
          defaultValue: "{{count}} 分钟内有效",
        });
  }, [expiresMs, now, t]);

  const handleConfirm = async () => {
    if (busy) return;
    setBusy("confirm");
    try {
      const res = await scheduledTaskApi.confirmDraft(draftId);
      setServerDraft(res.draft);
      setAccess("interactive");
      toast.success(
        isUpdate
          ? t("scheduledTask.draft.toast.updated", "定时任务已修改")
          : t("scheduledTask.draft.toast.created", "定时任务已创建"),
      );
      dispatchScheduledTaskRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      const code = e instanceof ApiRequestError ? e.status : 0;
      // 400 时草稿保持 pending 并记录 last_error；409/410 说明状态已变，都需要刷新
      if (code === 400 || code === 409 || code === 410) await loadDraft();
    } finally {
      setBusy(null);
    }
  };

  const handleCancel = async () => {
    if (busy) return;
    setBusy("cancel");
    try {
      const draft = await scheduledTaskApi.cancelDraft(draftId);
      setServerDraft(draft);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      if (e instanceof ApiRequestError && (e.status === 409 || e.status === 410)) {
        await loadDraft();
      }
    } finally {
      setBusy(null);
    }
  };

  const longPrompt =
    summary.prompt.length > LONG_PROMPT_CHARS || summary.prompt.includes("\n");
  const nextRuns = summary.next_runs.slice(0, MAX_PREVIEW_RUNS);

  const badgeBase =
    "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium";
  const btnBase =
    "inline-flex items-center justify-center gap-1.5 rounded-xl px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50";
  const primaryBtn = `${btnBase} bg-stone-900 text-white hover:bg-stone-800 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white`;
  const secondaryBtn = `${btnBase} border border-stone-200 text-stone-700 hover:bg-stone-100 dark:border-stone-700 dark:text-stone-200 dark:hover:bg-stone-800`;

  const viewTasksButton = (
    <button
      type="button"
      className={secondaryBtn}
      onClick={() => navigate("/scheduled-tasks")}
    >
      {t("scheduledTask.draft.viewTasks", "查看定时任务")}
      <ArrowRight size={14} />
    </button>
  );

  return (
    <div
      className="my-2 w-full max-w-xl rounded-2xl border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-700 dark:bg-stone-900"
      data-testid="scheduled-task-draft-card"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-stone-900 dark:text-stone-100">
          <CalendarClock size={16} className="shrink-0 text-stone-500" />
          {isUpdate
            ? t("scheduledTask.draft.titleUpdate", "定时任务草稿 · 修改")
            : t("scheduledTask.draft.titleCreate", "定时任务草稿 · 新建")}
        </div>
        {status === "confirmed" && (
          <span
            role="status"
            className={`${badgeBase} bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300`}
          >
            <Check size={12} />
            {t("scheduledTask.draft.status.confirmed", "已确认生效")}
          </span>
        )}
        {status === "cancelled" && (
          <span
            role="status"
            className={`${badgeBase} bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300`}
          >
            <X size={12} />
            {t("scheduledTask.draft.status.cancelled", "已取消")}
          </span>
        )}
        {status === "expired" && (
          <span
            role="status"
            className={`${badgeBase} bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300`}
          >
            {t("scheduledTask.draft.status.expiredBadge", "已过期")}
          </span>
        )}
        {status === "confirming" && (
          <span
            role="status"
            className={`${badgeBase} bg-sky-50 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300`}
          >
            <Loader2 size={12} className="animate-spin" />
            {t("scheduledTask.draft.status.confirming", "处理中…")}
          </span>
        )}
        {access === "readonly" && (
          <span
            role="status"
            className={`${badgeBase} bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300`}
          >
            <Eye size={12} />
            {t("scheduledTask.draft.readOnly", "仅供查看")}
          </span>
        )}
      </div>

      <dl className="mt-3 space-y-2">
        <Row label={t("scheduledTask.draft.field.name", "名称")}>
          <span className="break-words font-medium">{summary.name}</span>
        </Row>
        <Row label={t("scheduledTask.draft.field.prompt", "要做的事")}>
          <p
            className={`whitespace-pre-wrap break-words text-stone-700 dark:text-stone-300 ${
              expanded ? "" : "line-clamp-3"
            }`}
          >
            {summary.prompt}
          </p>
          {longPrompt && (
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((v) => !v)}
              className="mt-1 inline-flex items-center gap-0.5 text-xs text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-stone-200"
            >
              {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              {expanded
                ? t("scheduledTask.draft.collapse", "收起")
                : t("scheduledTask.draft.expand", "展开")}
            </button>
          )}
        </Row>
        <Row label={t("scheduledTask.draft.field.schedule", "执行频率")}>
          {summary.schedule_text}
        </Row>
        {nextRuns.length > 0 && (
          <Row label={t("scheduledTask.draft.field.nextRuns", "接下来")}>
            <ul className="space-y-0.5 text-stone-600 dark:text-stone-300">
              {nextRuns.map((iso) => (
                <li key={iso}>{formatFireTime(iso, locale)}</li>
              ))}
            </ul>
          </Row>
        )}
        <Row label={t("scheduledTask.draft.field.persona", "执行专家")}>
          <PersonaValue
            persona={summary.persona}
            fallback={defaultPersonaLabel}
          />
        </Row>
        <Row label={t("scheduledTask.draft.field.enabled", "启用状态")}>
          {enabledLabel(summary.enabled)}
        </Row>
      </dl>

      {isUpdate && changes.length > 0 && (
        <div
          className="mt-3 rounded-xl bg-stone-50 px-3 py-2.5 text-sm dark:bg-stone-800/60"
          data-testid="draft-changes"
        >
          <div className="font-medium text-stone-900 dark:text-stone-100">
            {t("scheduledTask.draft.changes", "改动")}：
            <span className="font-normal text-stone-600 dark:text-stone-300">
              {changeLabels.join("、")}
            </span>
          </div>
          {before && (
            <ul className="mt-1.5 space-y-1 text-xs text-stone-600 dark:text-stone-300">
              {changes.includes("name") && (
                <li>
                  {t("scheduledTask.draft.field.name", "名称")}：{before.name}
                  <ArrowRight size={11} className="mx-1 inline" />
                  {summary.name}
                </li>
              )}
              {changes.includes("schedule") && (
                <li>
                  {t("scheduledTask.draft.field.schedule", "执行频率")}：
                  {before.schedule_text}
                  <ArrowRight size={11} className="mx-1 inline" />
                  {summary.schedule_text}
                </li>
              )}
              {changes.includes("persona") && (
                <li>
                  {t("scheduledTask.draft.field.persona", "执行专家")}：
                  {before.persona?.name ?? defaultPersonaLabel}
                  <ArrowRight size={11} className="mx-1 inline" />
                  {summary.persona?.name ?? defaultPersonaLabel}
                </li>
              )}
              {changes.includes("enabled") && (
                <li>
                  {t("scheduledTask.draft.field.enabled", "启用状态")}：
                  {enabledLabel(before.enabled)}
                  <ArrowRight size={11} className="mx-1 inline" />
                  {enabledLabel(summary.enabled)}
                </li>
              )}
            </ul>
          )}
        </div>
      )}

      {lastError && status === "pending" && (
        <div
          role="alert"
          className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-900/30 dark:text-amber-200"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {lastError}
        </div>
      )}

      {access === "loading" && (
        <div className="mt-3 flex items-center gap-1.5 text-xs text-stone-500 dark:text-stone-400">
          <Loader2 size={12} className="animate-spin" />
          {t("scheduledTask.draft.loading", "正在确认草稿状态…")}
        </div>
      )}

      {access === "interactive" && status === "pending" && (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={primaryBtn}
              disabled={busy !== null}
              onClick={handleConfirm}
            >
              {busy === "confirm" && (
                <Loader2 size={14} className="animate-spin" />
              )}
              {busy === "confirm"
                ? t("scheduledTask.draft.confirming", "处理中…")
                : isUpdate
                  ? t("scheduledTask.draft.confirmUpdate", "确认修改")
                  : t("scheduledTask.draft.confirmCreate", "确认创建")}
            </button>
            <button
              type="button"
              className={secondaryBtn}
              disabled={busy !== null}
              onClick={handleCancel}
            >
              {busy === "cancel" && (
                <Loader2 size={14} className="animate-spin" />
              )}
              {t("scheduledTask.draft.cancel", "取消")}
            </button>
          </div>
          <p className="mt-2 text-xs text-stone-500 dark:text-stone-400">
            {t("scheduledTask.draft.pendingHint", "确认后才会生效")}
            {remainingText ? `，${remainingText}` : ""}
          </p>
        </div>
      )}

      {access === "interactive" && status === "confirmed" && (
        <div className="mt-4">{viewTasksButton}</div>
      )}

      {access === "interactive" && status === "expired" && (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
          {t(
            "scheduledTask.draft.status.expired",
            "已过期，可以让助手重新生成",
          )}
        </p>
      )}

      {access === "readonly" && (
        <p className="mt-3 text-xs text-stone-500 dark:text-stone-400">
          {t(
            "scheduledTask.draft.readOnlyHint",
            "这是一份草稿预览，需要在你自己的会话中确认后才会生效",
          )}
        </p>
      )}
    </div>
  );
}
