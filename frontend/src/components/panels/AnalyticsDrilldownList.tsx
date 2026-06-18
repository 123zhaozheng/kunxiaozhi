/**
 * Analytics Drilldown List - 钻取明细列表
 *
 * 支持三种明细：sessions / feedback / runs。复用统一的分页 + 时间区间，
 * 由 kind prop 决定列与数据源。点击饼图元素后跳转进来。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { Pagination } from "../common/Pagination";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { analyticsApi } from "../../services/api/analytics";
import type {
  FeedbackListItem,
  FeedbackListResponse,
  RunListItem,
  RunListResponse,
  SessionListItem,
  SessionListResponse,
} from "../../types/analytics";

export type DrilldownKind = "sessions" | "feedback" | "runs";

interface AnalyticsDrilldownListProps {
  kind: DrilldownKind;
  start: string;
  end: string;
  presetId?: string;
  rating?: "up" | "down";
  onBack: () => void;
}

const PAGE_SIZE = 20;

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
  onBack,
}: AnalyticsDrilldownListProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<SessionListResponse | null>(null);
  const [feedback, setFeedback] = useState<FeedbackListResponse | null>(null);
  const [runs, setRuns] = useState<RunListResponse | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const skip = (page - 1) * PAGE_SIZE;
    try {
      if (kind === "sessions") {
        const res = await analyticsApi.listSessions(start, end, {
          presetId,
          skip,
          limit: PAGE_SIZE,
        });
        setSessions(res ?? null);
      } else if (kind === "feedback") {
        const res = await analyticsApi.listFeedback(start, end, {
          presetId,
          rating,
          skip,
          limit: PAGE_SIZE,
        });
        setFeedback(res ?? null);
      } else {
        const res = await analyticsApi.listRuns(start, end, {
          presetId,
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
  }, [kind, start, end, presetId, rating, page, t]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    setPage(1);
  }, [kind, start, end, presetId, rating]);

  const titleKey =
    kind === "sessions"
      ? "analytics.drilldown.sessionsTitle"
      : kind === "feedback"
        ? "analytics.drilldown.feedbackTitle"
        : "analytics.drilldown.runsTitle";

  const total =
    kind === "sessions"
      ? sessions?.total ?? 0
      : kind === "feedback"
        ? feedback?.total ?? 0
        : runs?.total ?? 0;
  const items =
    kind === "sessions"
      ? sessions?.items ?? []
      : kind === "feedback"
        ? feedback?.items ?? []
        : runs?.items ?? [];

  return (
    <div className="glass-card flex min-h-0 flex-col rounded-xl p-4 sm:p-5">
      <div className="mb-3 flex items-center gap-2">
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
      </div>

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
                      {t("analytics.drilldown.agent", "Agent")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.status", "状态")}
                    </th>
                    <th className="py-2 pr-3 font-medium">
                      {t("analytics.drilldown.createdAt", "创建时间")}
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
                      {t("analytics.drilldown.preset", "角色")}
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
                      {t("analytics.drilldown.agent", "Agent")}
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
                          {row.user_id || "—"}
                        </td>
                        <td className="py-2 pr-3">{row.agent_id}</td>
                        <td className="py-2 pr-3">
                          {row.task_status || "—"}
                        </td>
                        <td className="whitespace-nowrap py-2 pr-3">
                          {formatDateTime(row.created_at)}
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
