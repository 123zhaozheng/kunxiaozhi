/**
 * Analytics Drilldown List - 钻取明细列表
 *
 * kinds: sessions / users / feedback / runs
 * Filters (sessions/users): agent_id · persona_preset_id · RBAC role · sort
 * Query params mirror backend list APIs (CSV export reuses the same contract).
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Download } from "lucide-react";
import { Pagination } from "../common/Pagination";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { analyticsApi } from "../../services/api/analytics";
import { roleApi } from "../../services/api/role";
import type {
  ActiveUserListItem,
  ActiveUserListResponse,
  AnalyticsListSort,
  FeedbackListItem,
  FeedbackListResponse,
  RunListItem,
  RunListResponse,
  SessionListItem,
  SessionListResponse,
} from "../../types/analytics";
import type { Role } from "../../types";

export type DrilldownKind = "sessions" | "users" | "feedback" | "runs";

export interface AnalyticsDrilldownFilters {
  agentId?: string;
  personaPresetId?: string;
  roleId?: string;
  sort?: AnalyticsListSort;
}

interface AnalyticsDrilldownListProps {
  kind: DrilldownKind;
  start: string;
  end: string;
  presetId?: string;
  rating?: "up" | "down";
  /** Optional initial filters when opening drilldown */
  initialFilters?: AnalyticsDrilldownFilters;
  /**
   * When true, persona_preset_id filter is locked (single-persona view).
   * Input is read-only / hidden edit.
   */
  lockPersonaPresetId?: boolean;
  onBack: () => void;
}

const PAGE_SIZE = 20;
const AGENT_OPTIONS = ["fast", "search", "team"] as const;

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return value.toLocaleString();
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export function AnalyticsDrilldownList({
  kind,
  start,
  end,
  presetId,
  rating,
  initialFilters,
  lockPersonaPresetId = false,
  onBack,
}: AnalyticsDrilldownListProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [agentId, setAgentId] = useState(initialFilters?.agentId ?? "");
  const [personaPresetId, setPersonaPresetId] = useState(
    initialFilters?.personaPresetId ?? presetId ?? "",
  );
  const [roleId, setRoleId] = useState(initialFilters?.roleId ?? "");
  const [sort, setSort] = useState<AnalyticsListSort>(
    initialFilters?.sort ??
      (kind === "users" ? "frequency" : "recent"),
  );
  const [roles, setRoles] = useState<Role[]>([]);

  const [sessions, setSessions] = useState<SessionListResponse | null>(null);
  const [users, setUsers] = useState<ActiveUserListResponse | null>(null);
  const [feedback, setFeedback] = useState<FeedbackListResponse | null>(null);
  const [runs, setRuns] = useState<RunListResponse | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const supportsListFilters = kind === "sessions" || kind === "users";
  const supportsCsvExport = kind === "sessions" || kind === "users";

  useEffect(() => {
    if (!supportsListFilters) return;
    let cancelled = false;
    roleApi
      .list({ skip: 0, limit: 100 })
      .then((res) => {
        if (!cancelled) setRoles(Array.isArray(res?.roles) ? res.roles : []);
      })
      .catch(() => {
        if (!cancelled) setRoles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [supportsListFilters]);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const skip = (page - 1) * PAGE_SIZE;
    try {
      if (kind === "sessions") {
        const res = await analyticsApi.listSessions(start, end, {
          agentId: agentId || undefined,
          personaPresetId: personaPresetId || undefined,
          roleId: roleId || undefined,
          sort,
          skip,
          limit: PAGE_SIZE,
        });
        setSessions(res ?? null);
      } else if (kind === "users") {
        const res = await analyticsApi.listActiveUsers(start, end, {
          agentId: agentId || undefined,
          personaPresetId: personaPresetId || undefined,
          roleId: roleId || undefined,
          sort,
          skip,
          limit: PAGE_SIZE,
        });
        setUsers(res ?? null);
      } else if (kind === "feedback") {
        const res = await analyticsApi.listFeedback(start, end, {
          presetId: personaPresetId || presetId,
          rating,
          skip,
          limit: PAGE_SIZE,
        });
        setFeedback(res ?? null);
      } else {
        const res = await analyticsApi.listRuns(start, end, {
          presetId: personaPresetId || presetId,
          skip,
          limit: PAGE_SIZE,
        });
        setRuns(res ?? null);
      }
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : t("common.loadFailed", "Load failed");
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [
    kind,
    start,
    end,
    presetId,
    rating,
    page,
    agentId,
    personaPresetId,
    roleId,
    sort,
    t,
  ]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    setPage(1);
  }, [kind, start, end, presetId, rating, agentId, personaPresetId, roleId, sort]);

  const handleExport = useCallback(async () => {
    if (!supportsCsvExport) return;
    setIsExporting(true);
    setExportError(null);
    const filters = {
      agentId: agentId || undefined,
      personaPresetId: personaPresetId || undefined,
      roleId: roleId || undefined,
      sort,
    };
    try {
      if (kind === "sessions") {
        await analyticsApi.exportSessionsCsv(start, end, filters);
      } else {
        await analyticsApi.exportUsersCsv(start, end, filters);
      }
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : t("analytics.drilldown.exportFailed", "导出失败");
      setExportError(message);
    } finally {
      setIsExporting(false);
    }
  }, [
    supportsCsvExport,
    kind,
    start,
    end,
    agentId,
    personaPresetId,
    roleId,
    sort,
    t,
  ]);

  const titleKey =
    kind === "sessions"
      ? "analytics.drilldown.sessionsTitle"
      : kind === "users"
        ? "analytics.drilldown.usersTitle"
        : kind === "feedback"
          ? "analytics.drilldown.feedbackTitle"
          : "analytics.drilldown.runsTitle";

  const total =
    kind === "sessions"
      ? (sessions?.total ?? 0)
      : kind === "users"
        ? (users?.total ?? 0)
        : kind === "feedback"
          ? (feedback?.total ?? 0)
          : (runs?.total ?? 0);

  const items =
    kind === "sessions"
      ? (sessions?.items ?? [])
      : kind === "users"
        ? (users?.items ?? [])
        : kind === "feedback"
          ? (feedback?.items ?? [])
          : (runs?.items ?? []);

  const selectClass =
    "glass-input max-w-[10rem] rounded-lg px-2 py-1 text-xs text-stone-700 dark:text-stone-200";

  return (
    <div className="glass-card flex min-h-0 flex-col rounded-xl p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
        >
          <ArrowLeft size={16} aria-hidden />
          <span>{t("analytics.drilldown.back", "返回")}</span>
        </button>
        <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-100">
          {t(titleKey)}
        </h3>
        {supportsCsvExport ? (
          <div className="ml-auto flex flex-col items-end gap-1">
            <button
              type="button"
              onClick={handleExport}
              disabled={isExporting}
              className="flex items-center gap-1 rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg-subtle)] px-2.5 py-1 text-xs text-stone-700 hover:bg-[var(--glass-bg)] disabled:opacity-50 dark:text-stone-200"
            >
              <Download size={14} aria-hidden />
              <span>
                {isExporting
                  ? t("analytics.drilldown.exporting", "导出中…")
                  : t("analytics.drilldown.exportCsv", "导出 CSV")}
              </span>
            </button>
            <span className="max-w-[18rem] text-right text-[10px] text-stone-500 dark:text-stone-400">
              {t(
                "analytics.drilldown.exportHint",
                "导出当前筛选全量（非本页，最多 10000 行）",
              )}
            </span>
          </div>
        ) : null}
      </div>

      {exportError ? (
        <div className="mb-3 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
          {exportError}
        </div>
      ) : null}

      {supportsListFilters ? (
        <div className="mb-3 flex flex-wrap items-end gap-2 border-b border-[var(--glass-border)] pb-3">
          <label className="flex flex-col gap-1 text-[10px] text-stone-500 dark:text-stone-400">
            {t("analytics.filters.agent", "智能体")}
            <select
              className={selectClass}
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
            >
              <option value="">{t("analytics.filters.all", "全部")}</option>
              {AGENT_OPTIONS.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[10px] text-stone-500 dark:text-stone-400">
            {t("analytics.filters.persona", "Persona")}
            <input
              type="text"
              className={`${selectClass}${
                lockPersonaPresetId ? " opacity-70" : ""
              }`}
              placeholder={t(
                "analytics.filters.personaPlaceholder",
                "preset id",
              )}
              value={personaPresetId}
              readOnly={lockPersonaPresetId}
              disabled={lockPersonaPresetId}
              onChange={(e) => {
                if (lockPersonaPresetId) return;
                setPersonaPresetId(e.target.value.trim());
              }}
              title={
                lockPersonaPresetId
                  ? t(
                      "analytics.filters.personaLocked",
                      "已锁定为当前 Persona",
                    )
                  : undefined
              }
            />
          </label>
          <label className="flex flex-col gap-1 text-[10px] text-stone-500 dark:text-stone-400">
            {t("analytics.filters.userRole", "用户角色")}
            <select
              className={selectClass}
              value={roleId}
              onChange={(e) => setRoleId(e.target.value)}
            >
              <option value="">{t("analytics.filters.all", "全部")}</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name || role.id}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[10px] text-stone-500 dark:text-stone-400">
            {t("analytics.filters.sort", "排序")}
            <select
              className={selectClass}
              value={sort}
              onChange={(e) => setSort(e.target.value as AnalyticsListSort)}
            >
              <option value="recent">
                {t("analytics.filters.sortRecent", "最近活跃")}
              </option>
              <option value="frequency">
                {t("analytics.filters.sortFrequency", "使用频次")}
              </option>
            </select>
          </label>
        </div>
      ) : null}

      {error ? (
        <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
          {error}
        </div>
      ) : null}

      <div className="relative flex-1 overflow-auto">
        {isLoading ? (
          <PanelLoadingState />
        ) : items.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-stone-500 dark:text-stone-400">
            {t("analytics.drilldown.empty", "暂无数据")}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-stone-500 dark:text-stone-400">
                {kind === "sessions" ? (
                  <tr>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.sessionName", "会话")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.user", "用户")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.agent", "智能体")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.persona", "Persona")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.status", "状态")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.createdAt", "创建时间")}
                    </th>
                  </tr>
                ) : kind === "users" ? (
                  <tr>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.user", "用户")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.filters.userRole", "用户角色")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.sessionCount", "会话数")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.lastActive", "最近活跃")}
                    </th>
                  </tr>
                ) : kind === "feedback" ? (
                  <tr>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.rating", "评分")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.reason", "原因")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.user", "用户")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.persona", "Persona")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.comment", "评论")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.createdAt", "创建时间")}
                    </th>
                  </tr>
                ) : (
                  <tr>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.runId", "运行")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.agent", "智能体")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.status", "状态")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.eventCount", "事件数")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.tokens", "Token")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.startedAt", "开始时间")}
                    </th>
                  </tr>
                )}
              </thead>
              <tbody className="divide-y divide-[var(--glass-border)]">
                {kind === "sessions"
                  ? (items as SessionListItem[]).map((row) => (
                      <tr
                        key={row.id}
                        className="text-stone-700 dark:text-stone-200"
                      >
                        <td className="max-w-[18ch] truncate py-2 pr-3">
                          {row.name || row.id}
                        </td>
                        <td className="max-w-[12ch] truncate py-2 pr-3">
                          {row.username || row.user_id || "—"}
                        </td>
                        <td className="py-2 pr-3">{row.agent_id}</td>
                        <td className="max-w-[14ch] truncate py-2 pr-3">
                          {row.persona_preset_name ||
                            row.persona_preset_id ||
                            "—"}
                        </td>
                        <td className="py-2 pr-3">
                          {row.task_status || "—"}
                        </td>
                        <td className="whitespace-nowrap py-2 pr-3">
                          {formatDateTime(row.created_at)}
                        </td>
                      </tr>
                    ))
                  : kind === "users"
                    ? (items as ActiveUserListItem[]).map((row) => (
                        <tr
                          key={row.user_id}
                          className="text-stone-700 dark:text-stone-200"
                        >
                          <td className="max-w-[16ch] truncate py-2 pr-3">
                            <div className="truncate font-medium">
                              {row.username || row.user_id || "—"}
                            </div>
                            {row.display_name &&
                            row.display_name !== row.username ? (
                              <div className="truncate text-[10px] text-stone-500 dark:text-stone-400">
                                {row.display_name}
                              </div>
                            ) : null}
                          </td>
                          <td className="max-w-[16ch] truncate py-2 pr-3">
                            {row.roles?.length ? row.roles.join(", ") : "—"}
                          </td>
                          <td className="py-2 pr-3">
                            {formatNumber(row.session_count)}
                          </td>
                          <td className="whitespace-nowrap py-2 pr-3">
                            {formatDateTime(row.last_active_at)}
                          </td>
                        </tr>
                      ))
                    : kind === "feedback"
                      ? (items as FeedbackListItem[]).map((row) => (
                          <tr
                            key={row.id}
                            className="text-stone-700 dark:text-stone-200"
                          >
                            <td className="py-2 pr-3">
                              {row.rating === "up"
                                ? t("feedback.positive")
                                : t("feedback.negative")}
                            </td>
                            <td className="py-2 pr-3">
                              {row.reason
                                ? t(`feedback.reason.${row.reason}`)
                                : "—"}
                            </td>
                            <td className="max-w-[12ch] truncate py-2 pr-3">
                              {row.username || row.user_id || "—"}
                            </td>
                            <td className="max-w-[14ch] truncate py-2 pr-3">
                              {row.persona_preset_name || "—"}
                            </td>
                            <td className="max-w-[24ch] truncate py-2 pr-3">
                              {row.comment || "—"}
                            </td>
                            <td className="whitespace-nowrap py-2 pr-3">
                              {formatDateTime(row.created_at)}
                            </td>
                          </tr>
                        ))
                      : (items as RunListItem[]).map((row) => (
                          <tr
                            key={row.run_id}
                            className="text-stone-700 dark:text-stone-200"
                          >
                            <td className="max-w-[16ch] truncate py-2 pr-3">
                              {row.run_id}
                            </td>
                            <td className="py-2 pr-3">{row.agent_id}</td>
                            <td className="py-2 pr-3">{row.status}</td>
                            <td className="py-2 pr-3">
                              {formatNumber(row.event_count)}
                            </td>
                            <td className="py-2 pr-3">
                              {formatNumber(row.total_tokens)}
                            </td>
                            <td className="whitespace-nowrap py-2 pr-3">
                              {formatDateTime(row.started_at)}
                            </td>
                          </tr>
                        ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {total > PAGE_SIZE ? (
        <div className="mt-3 flex justify-center">
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onChange={setPage}
          />
        </div>
      ) : null}
    </div>
  );
}

export default AnalyticsDrilldownList;
