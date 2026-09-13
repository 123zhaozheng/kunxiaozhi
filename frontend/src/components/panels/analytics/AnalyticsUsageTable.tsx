/**
 * Analytics Usage Table — per-user × persona usage detail.
 *
 * Search (employee id / name / persona) filters the loaded rows locally
 * (the backend list endpoint exposes no search param and caps limit at 100,
 * so server-side search is not available). Header meta shows
 * "N 人使用 / 共 M 人登录" (N = summary.using_users, M = summary.active_users)
 * to explain why the row count differs from the active-users KPI card.
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Download, Search } from "lucide-react";
import { Pagination } from "../../common/Pagination";
import { PanelLoadingState } from "../../common/PanelLoadingState";
import type { UsageByUserItem, UsageSummaryResponse } from "../../../types/analytics";
import { formatDateTime } from "../../../utils/datetime";
import { formatNumber } from "./analyticsFormat";

// Column order mirrors UsageByUserItem so the table and the CSV export match.
const USAGE_COLUMN_KEYS = [
  "userId",
  "name",
  "role",
  "persona",
  "newSessions",
  "activeSessions",
  "userMessages",
  "tokens",
  "lastActive",
] as const;

/** Case-insensitive match on employee id, display name or persona name. */
function matchesSearch(row: UsageByUserItem, needle: string): boolean {
  const query = needle.trim().toLowerCase();
  if (!query) return true;
  return [row.username, row.display_name ?? "", row.persona_preset_name].some(
    (field) => field.toLowerCase().includes(query),
  );
}

export interface AnalyticsUsageTableProps {
  rows: UsageByUserItem[];
  /** Backend-provided total for the whole filtered set (not the page sum). */
  total: number;
  page: number;
  pageSize: number;
  isLoading: boolean;
  error: string | null;
  isExporting: boolean;
  exportError: string | null;
  /** Controlled search box (employee id / name / persona). */
  search: string;
  onSearchChange: (value: string) => void;
  /** Supplies using_users / active_users for the header meta. */
  summary: UsageSummaryResponse | null;
  onPageChange: (page: number) => void;
  onExport: () => void;
}

export function AnalyticsUsageTable({
  rows,
  total,
  page,
  pageSize,
  isLoading,
  error,
  isExporting,
  exportError,
  search,
  onSearchChange,
  summary,
  onPageChange,
  onExport,
}: AnalyticsUsageTableProps) {
  const { t } = useTranslation();

  const visibleRows = useMemo(
    () => rows.filter((row) => matchesSearch(row, search)),
    [rows, search],
  );

  const usingUsers = summary ? summary.using_users : null;
  const loggedInUsers = summary ? summary.active_users : null;
  // The logged-in total only adds information when it exceeds the using count
  // (persona/agent filters collapse active_users to using_users).
  const showLoggedIn =
    usingUsers !== null &&
    loggedInUsers !== null &&
    loggedInUsers > usingUsers;

  return (
    <section className="mt-4">
      <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="flex flex-wrap items-baseline gap-2">
            <h2 className="text-sm font-semibold tracking-wide text-stone-600 uppercase dark:text-stone-400">
              {t("analytics.usage.title")}
            </h2>
            {usingUsers !== null ? (
              <span className="text-xs text-stone-500 dark:text-stone-400">
                {t("analytics.usage.usingCount", {
                  count: formatNumber(usingUsers),
                  defaultValue: "{{count}} 人使用",
                })}
                {showLoggedIn && loggedInUsers !== null
                  ? ` / ${t("analytics.usage.loggedInCount", {
                      count: formatNumber(loggedInUsers),
                      defaultValue: "共 {{count}} 人登录",
                    })}`
                  : ""}
              </span>
            ) : null}
          </div>
          <p className="text-xs text-stone-500 dark:text-stone-400">
            {t("analytics.usage.subtitle")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search
              size={14}
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-stone-400 dark:text-stone-500"
            />
            <input
              type="search"
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder={t(
                "analytics.usage.searchPlaceholder",
                "搜索工号 / 姓名 / Persona",
              )}
              aria-label={t(
                "analytics.usage.searchPlaceholder",
                "搜索工号 / 姓名 / Persona",
              )}
              className="glass-input w-56 py-1 pl-8 pr-2 text-xs"
            />
          </div>
          <button
            type="button"
            onClick={onExport}
            disabled={isExporting}
            className="flex items-center gap-1 rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg-subtle)] px-2.5 py-1 text-xs text-stone-700 hover:bg-[var(--glass-bg)] disabled:opacity-50 dark:text-stone-200"
          >
            <Download size={14} aria-hidden />
            <span>
              {isExporting
                ? t("analytics.usage.exporting")
                : t("analytics.usage.exportCsv")}
            </span>
          </button>
        </div>
        <p className="w-full text-right text-[10px] text-stone-500 dark:text-stone-400">
          {t(
            "analytics.usage.exportHint",
            "导出当前筛选全量（非本页，最多 10000 行）",
          )}
        </p>
      </div>

      {search.trim() ? (
        <p className="mb-2 text-[10px] text-stone-500 dark:text-stone-400">
          {t("analytics.usage.searchHint", "仅过滤当前页数据")}
        </p>
      ) : null}

      {exportError ? (
        <div className="mb-2 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
          {exportError}
        </div>
      ) : null}

      <div className="glass-card rounded-xl p-4">
        {error ? (
          <div className="mb-2 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
            {error}
          </div>
        ) : null}

        {isLoading ? (
          <PanelLoadingState />
        ) : visibleRows.length === 0 ? (
          <div className="py-8 text-center text-sm text-stone-500 dark:text-stone-400">
            {search.trim()
              ? t("analytics.usage.searchEmpty", "未找到匹配的记录")
              : t("analytics.usage.empty")}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="text-stone-500 dark:text-stone-400">
                <tr>
                  {USAGE_COLUMN_KEYS.map((key) => (
                    <th key={key} className="py-2 pr-3 font-medium">
                      {t(`analytics.usage.columns.${key}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--glass-border)]">
                {visibleRows.map((row) => (
                  <tr
                    key={`${row.user_id}-${row.persona_preset_id ?? "none"}`}
                    className="text-stone-700 dark:text-stone-200"
                  >
                    <td className="py-2 pr-3">{row.username || "—"}</td>
                    <td className="py-2 pr-3">
                      {row.display_name || row.username || "—"}
                    </td>
                    <td className="py-2 pr-3">
                      {row.roles.join(", ") || "—"}
                    </td>
                    <td className="py-2 pr-3">
                      {row.persona_preset_name || "—"}
                    </td>
                    <td className="py-2 pr-3">
                      {formatNumber(row.new_sessions)}
                    </td>
                    <td className="py-2 pr-3">
                      {formatNumber(row.active_sessions)}
                    </td>
                    <td className="py-2 pr-3">
                      {formatNumber(row.user_messages)}
                    </td>
                    <td className="py-2 pr-3">
                      {formatNumber(row.total_tokens)}
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {row.last_active_at
                        ? formatDateTime(row.last_active_at)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-3 flex justify-center">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onChange={onPageChange}
          />
        </div>
      </div>
    </section>
  );
}
