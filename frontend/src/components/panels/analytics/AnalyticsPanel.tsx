/**
 * Analytics Panel — 全局统计看板（筛选 state + 数据编排 + 区块布局）
 *
 * 唯一发请求的组件：时间预设 / Persona / 智能体 三个筛选驱动所有区块，
 * 八个一致性契约请求（summary / trend / insights / by-agent / by-persona /
 * by-model / by-user / export）共用同一批 `start`/`end`（纯 YYYY-MM-DD 日期
 * 字符串）与 `usageFilters`。日界由后端（UTC+8 半开区间）负责。
 *
 * 后端只读后台预聚合的快照（今日每分钟刷新），打开看板不触发实时计算；
 * 默认「今日」。版面：「数据看板 / 使用明细」两个 tab，钻取替换内容区。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BarChart3, LayoutDashboard, Table2 } from "lucide-react";
import { PanelHeader } from "../../common/PanelHeader";
import { analyticsApi } from "../../../services/api/analytics";
import { personaPresetApi } from "../../../services/api/personaPreset";
import type {
  AnalyticsRangePreset,
  ByLabelItem,
  ByPresetFeedbackItem,
  FeedbackSummaryResponse,
  TrendDataPoint,
  UsageByUserItem,
  UsageFilters,
  UsageHourPoint,
  UsageInsightsResponse,
  UsageInsightsTopTokenUser,
  UsageSummaryResponse,
  UsageTrendPoint,
} from "../../../types/analytics";
import {
  AnalyticsDrilldownList,
  type AnalyticsDrilldownFilters,
  type DrilldownKind,
} from "../AnalyticsDrilldownList";
import { AnalyticsFilterBar } from "./AnalyticsFilterBar";
import { AnalyticsInsightPanel } from "./AnalyticsInsightPanel";
import { AnalyticsKpiRow } from "./AnalyticsKpiRow";
import { AnalyticsTopRow } from "./AnalyticsTopRow";
import { AnalyticsTrendChart } from "./AnalyticsTrendChart";
import { AnalyticsUsageTable } from "./AnalyticsUsageTable";
import {
  addDaysString,
  effectiveRangeFor,
  todayString,
  type AnalyticsDateRange,
  type FixedRangePreset,
} from "./analyticsDates";
import { formatTime } from "../../../utils/datetime";

const USAGE_PAGE_SIZE = 20;

type AnalyticsTab = "dashboard" | "usage";

interface AnalyticsSectionErrors {
  summary: string | null;
  insights: string | null;
  usageTrend: string | null;
  activeTrend: string | null;
  sessionsByAgent: string | null;
  sessionsByPersona: string | null;
  tokensByModel: string | null;
  feedbackSummary: string | null;
  feedbackByPreset: string | null;
}

const EMPTY_SECTION_ERRORS: AnalyticsSectionErrors = {
  summary: null,
  insights: null,
  usageTrend: null,
  activeTrend: null,
  sessionsByAgent: null,
  sessionsByPersona: null,
  tokensByModel: null,
  feedbackSummary: null,
  feedbackByPreset: null,
};

function settledError(
  result: PromiseSettledResult<unknown>,
  fallback: string,
): string | null {
  return result.status === "rejected"
    ? result.reason instanceof Error
      ? result.reason.message
      : fallback
    : null;
}

function safeRequest<T>(request: () => Promise<T>): Promise<T> {
  return Promise.resolve().then(request);
}

export function AnalyticsPanel() {
  const { t } = useTranslation();

  // ── Filter state (single source of truth for every request) ──────────
  const [preset, setPreset] = useState<AnalyticsRangePreset>("1d");
  const [tab, setTab] = useState<AnalyticsTab>("dashboard");
  const [customRange, setCustomRange] = useState<AnalyticsDateRange | null>(null);
  const [personaPresetId, setPersonaPresetId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [analyticsToday, setAnalyticsToday] = useState(() => todayString());
  const analyticsNow = useMemo(
    () => new Date(`${analyticsToday}T12:00:00+08:00`),
    [analyticsToday],
  );

  const effectiveRange = useMemo(
    () => effectiveRangeFor(preset, customRange, analyticsNow),
    [preset, customRange, analyticsNow],
  );

  const usageFilters = useMemo<UsageFilters>(
    () => ({
      personaPresetId: personaPresetId || undefined,
      agentId: agentId || undefined,
    }),
    [personaPresetId, agentId],
  );

  // Login activity has no persona/agent dimension, so a dimensional filter
  // switches the first KPI card to the using-user metric (PRD R4).
  const isFiltered = Boolean(personaPresetId || agentId);

  // ── Data state ────────────────────────────────────────────────────────
  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
  const [insights, setInsights] = useState<UsageInsightsResponse | null>(null);
  const [usageTrend, setUsageTrend] = useState<UsageTrendPoint[]>([]);
  const [usageHourly, setUsageHourly] = useState<UsageHourPoint[]>([]);
  const [activeTrend, setActiveTrend] = useState<TrendDataPoint[]>([]);
  const [sessionsByAgent, setSessionsByAgent] = useState<ByLabelItem[]>([]);
  const [sessionsByPersona, setSessionsByPersona] = useState<ByLabelItem[]>([]);
  const [tokensByModel, setTokensByModel] = useState<ByLabelItem[]>([]);
  const [tokensByModelPartial, setTokensByModelPartial] = useState<{
    partial: boolean;
    since: string | null;
  }>({ partial: false, since: null });
  const [feedbackSummary, setFeedbackSummary] =
    useState<FeedbackSummaryResponse | null>(null);
  const [feedbackByPreset, setFeedbackByPreset] = useState<ByPresetFeedbackItem[]>(
    [],
  );

  const [usageRows, setUsageRows] = useState<UsageByUserItem[]>([]);
  const [usageTotal, setUsageTotal] = useState(0);
  const [usagePage, setUsagePage] = useState(1);
  const [usageSearch, setUsageSearch] = useState("");
  const [usageLoading, setUsageLoading] = useState(true);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [isUsageExporting, setIsUsageExporting] = useState(false);
  const [usageExportError, setUsageExportError] = useState<string | null>(null);

  const [personaOptions, setPersonaOptions] = useState<Array<{ id: string; name: string }>>([]);

  const [drilldown, setDrilldown] = useState<{
    kind: DrilldownKind;
    presetId?: string;
    rating?: "up" | "down";
    initialFilters?: AnalyticsDrilldownFilters;
  } | null>(null);

  const [isLoading, setIsLoading] = useState(true);
  const [sectionErrors, setSectionErrors] =
    useState<AnalyticsSectionErrors>(EMPTY_SECTION_ERRORS);
  const requestIdRef = useRef(0);

  // Agent options come from the data itself, so a custom or "default" agent is selectable.
  const agentOptions = useMemo(
    () => sessionsByAgent.map((item) => item.label).filter(Boolean),
    [sessionsByAgent],
  );

  useEffect(() => {
    let cancelled = false;
    personaPresetApi
      .list({ scope: "global", limit: 100 })
      .then((response) => {
        if (!cancelled) {
          setPersonaOptions(
            Array.isArray(response?.presets)
              ? response.presets.map((presetItem) => ({ id: presetItem.id, name: presetItem.name }))
              : [],
          );
        }
      })
      .catch(() => {
        if (!cancelled) setPersonaOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fixed presets are anchored to the UTC+8 day, not the browser's local day.
  // Schedule the next boundary so an open dashboard rolls over without a reload.
  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const scheduleNextBoundary = () => {
      const current = todayString();
      setAnalyticsToday((previous) => (previous === current ? previous : current));
      const next = addDaysString(current, 1);
      const nextBoundary = Date.parse(`${next}T00:00:00+08:00`);
      timeout = setTimeout(
        scheduleNextBoundary,
        Math.max(nextBoundary - Date.now(), 1_000),
      );
    };
    scheduleNextBoundary();
    return () => {
      if (timeout) clearTimeout(timeout);
    };
  }, []);

  // ── Data orchestration: one batch of requests per filter change ──────
  const fetchData = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    setSectionErrors(EMPTY_SECTION_ERRORS);
    const { start, end } = effectiveRange;
    const results = await Promise.allSettled([
      safeRequest(() => analyticsApi.getUsageSummary(start, end, usageFilters)),
      safeRequest(() => analyticsApi.getUsageInsights(start, end, usageFilters)),
      safeRequest(() => analyticsApi.getUsageTrend(start, end, usageFilters)),
      safeRequest(() => analyticsApi.getActiveUserTrend(start, end, usageFilters)),
      safeRequest(() => analyticsApi.getSessionsByAgent(start, end, usageFilters, 100)),
      safeRequest(() => analyticsApi.getSessionsByPersona(start, end, usageFilters, 10)),
      safeRequest(() => analyticsApi.getTokensByModel(start, end, usageFilters)),
      safeRequest(() => analyticsApi.getFeedbackSummary(start, end)),
      safeRequest(() => analyticsApi.getFeedbackByPreset(start, end)),
    ]);
    if (requestId !== requestIdRef.current) return;

    const [
      summaryResult,
      insightsResult,
      usageTrendResult,
      activeTrendResult,
      byAgentResult,
      byPersonaResult,
      byModelResult,
      feedbackSummaryResult,
      feedbackByPresetResult,
    ] = results;
    const errorMessage = t("common.loadFailed", "Load failed");
    setSummary(summaryResult.status === "fulfilled" ? summaryResult.value ?? null : null);
    setInsights(insightsResult.status === "fulfilled" ? insightsResult.value ?? null : null);
    setUsageTrend(
      usageTrendResult.status === "fulfilled" && Array.isArray(usageTrendResult.value?.items)
        ? usageTrendResult.value.items
        : [],
    );
    setUsageHourly(
      usageTrendResult.status === "fulfilled" && Array.isArray(usageTrendResult.value?.hourly)
        ? usageTrendResult.value.hourly
        : [],
    );
    setActiveTrend(
      activeTrendResult.status === "fulfilled" && Array.isArray(activeTrendResult.value?.items)
        ? activeTrendResult.value.items
        : [],
    );
    setSessionsByAgent(
      byAgentResult.status === "fulfilled" && Array.isArray(byAgentResult.value?.items)
        ? byAgentResult.value.items
        : [],
    );
    setSessionsByPersona(
      byPersonaResult.status === "fulfilled" && Array.isArray(byPersonaResult.value?.items)
        ? byPersonaResult.value.items
        : [],
    );
    setTokensByModel(
      byModelResult.status === "fulfilled" && Array.isArray(byModelResult.value?.items)
        ? byModelResult.value.items
        : [],
    );
    setTokensByModelPartial(
      byModelResult.status === "fulfilled" && byModelResult.value?.partial
        ? {
            partial: true,
            since: byModelResult.value.model_data_since ?? null,
          }
        : { partial: false, since: null },
    );
    setFeedbackSummary(
      feedbackSummaryResult.status === "fulfilled"
        ? feedbackSummaryResult.value ?? null
        : null,
    );
    setFeedbackByPreset(
      feedbackByPresetResult.status === "fulfilled" &&
        Array.isArray(feedbackByPresetResult.value?.items)
        ? feedbackByPresetResult.value.items
        : [],
    );
    setSectionErrors({
      summary: settledError(summaryResult, errorMessage),
      insights: settledError(insightsResult, errorMessage),
      usageTrend: settledError(usageTrendResult, errorMessage),
      activeTrend: settledError(activeTrendResult, errorMessage),
      sessionsByAgent: settledError(byAgentResult, errorMessage),
      sessionsByPersona: settledError(byPersonaResult, errorMessage),
      tokensByModel: settledError(byModelResult, errorMessage),
      feedbackSummary: settledError(feedbackSummaryResult, errorMessage),
      feedbackByPreset: settledError(feedbackByPresetResult, errorMessage),
    });
    setIsLoading(false);
  }, [effectiveRange, usageFilters, t]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Usage detail (paginated) shares the exact same filter batch; it is only
  // fetched while its tab is open.
  useEffect(() => {
    if (tab !== "usage") return;
    let cancelled = false;
    const { start, end } = effectiveRange;
    setUsageLoading(true);
    setUsageError(null);
    analyticsApi
      .listUsageByUser(start, end, usageFilters, {
        skip: (usagePage - 1) * USAGE_PAGE_SIZE,
        limit: USAGE_PAGE_SIZE,
      })
      .then((response) => {
        if (cancelled) return;
        setUsageRows(Array.isArray(response?.items) ? response.items : []);
        setUsageTotal(response?.total ?? 0);
      })
      .catch((err) => {
        if (!cancelled) {
          setUsageError(
            err instanceof Error ? err.message : t("common.loadFailed", "Load failed"),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setUsageLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveRange, usageFilters, usagePage, tab, t]);

  useEffect(() => {
    setUsagePage(1);
    setUsageSearch("");
  }, [effectiveRange, usageFilters]);

  const handleUsageExport = useCallback(async () => {
    setIsUsageExporting(true);
    setUsageExportError(null);
    const { start, end } = effectiveRange;
    try {
      await analyticsApi.exportUsageCsv(start, end, usageFilters);
    } catch (err) {
      setUsageExportError(
        err instanceof Error ? err.message : t("analytics.usage.exportFailed"),
      );
    } finally {
      setIsUsageExporting(false);
    }
  }, [effectiveRange, usageFilters, t]);

  const handlePresetChange = useCallback((next: FixedRangePreset) => {
    setPreset(next);
    setCustomRange(null);
    setDrilldown(null);
  }, []);

  const handleCustomRangeApply = useCallback((range: AnalyticsDateRange) => {
    setCustomRange(range);
    setPreset("custom");
    setDrilldown(null);
  }, []);

  const handleTabChange = useCallback((next: AnalyticsTab) => {
    setTab(next);
    setDrilldown(null);
  }, []);

  // Insight "Token 大户" click → open the usage detail tab filtered by that user.
  const handleInsightUserDrilldown = useCallback(
    (user: UsageInsightsTopTokenUser) => {
      setUsageSearch(user.username || user.user_id);
      setUsagePage(1);
      setTab("usage");
      setDrilldown(null);
    },
    [],
  );

  const dataUpdatedAt = summary?.data_updated_at ?? null;

  return (
    <div className="glass-shell flex h-full flex-col min-h-0">
      <PanelHeader
        title={t("analytics.title")}
        subtitle={t("analytics.subtitle", "System usage and token metrics")}
        icon={
          <BarChart3
            size={20}
            className="text-stone-600 dark:text-stone-400"
            aria-hidden
          />
        }
      />

      <AnalyticsFilterBar
        preset={preset}
        range={effectiveRange}
        personaPresetId={personaPresetId}
        agentId={agentId}
        personaOptions={personaOptions}
        agentOptions={agentOptions}
        onPresetChange={handlePresetChange}
        onCustomRangeApply={handleCustomRangeApply}
        onPersonaChange={setPersonaPresetId}
        onAgentChange={setAgentId}
      />

      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pb-3 sm:px-6">
        <div
          className="inline-flex items-center gap-1 rounded-lg bg-[var(--glass-bg-subtle)] p-1"
          role="tablist"
          aria-label={t("analytics.title")}
        >
          {(
            [
              { key: "dashboard", label: t("analytics.tabs.dashboard", "数据看板"), icon: LayoutDashboard },
              { key: "usage", label: t("analytics.tabs.usage", "使用明细"), icon: Table2 },
            ] as const
          ).map(({ key, label, icon: Icon }) => {
            const active = tab === key && !drilldown;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => handleTabChange(key)}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  active
                    ? "bg-[var(--theme-bg-card)] text-stone-900 shadow-sm dark:text-stone-100"
                    : "text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-stone-200"
                }`}
              >
                <Icon size={15} aria-hidden />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
        {dataUpdatedAt ? (
          <span
            className="text-xs text-stone-500 dark:text-stone-400"
            title={t(
              "analytics.dataUpdatedHint",
              "统计由后台每分钟预计算，打开看板无需等待实时计算",
            )}
          >
            {t("analytics.dataUpdatedAt", {
              time: formatTime(dataUpdatedAt),
              defaultValue: "数据更新于 {{time}}",
            })}
          </span>
        ) : null}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 pb-6 sm:px-6">
        {drilldown ? (
          <AnalyticsDrilldownList
            key={`${drilldown.kind}-${drilldown.presetId ?? ""}-${drilldown.rating ?? ""}-${JSON.stringify(drilldown.initialFilters ?? {})}`}
            kind={drilldown.kind}
            start={effectiveRange.start}
            end={effectiveRange.end}
            presetId={drilldown.presetId}
            rating={drilldown.rating}
            initialFilters={drilldown.initialFilters}
            personaOptions={personaOptions}
            agentOptions={agentOptions}
            onBack={() => setDrilldown(null)}
          />
        ) : tab === "dashboard" ? (
          <>
            <AnalyticsKpiRow
              summary={summary}
              usageTrend={usageTrend}
              activeTrend={activeTrend}
              hourly={usageHourly}
              isFiltered={isFiltered}
              isLoading={isLoading}
              error={sectionErrors.summary}
              onUsersDrilldown={() => setDrilldown({ kind: "users" })}
              onSessionsDrilldown={() => setDrilldown({ kind: "sessions" })}
            />

            {/* Core trend + insight column */}
            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <AnalyticsTrendChart
                  usageTrend={usageTrend}
                  activeTrend={activeTrend}
                  hourly={usageHourly}
                  range={effectiveRange}
                  isLoading={isLoading}
                  error={sectionErrors.usageTrend || sectionErrors.activeTrend}
                />
              </div>
              <AnalyticsInsightPanel
                insights={insights}
                isLoading={isLoading}
                error={sectionErrors.insights}
                onPersonaSelect={setPersonaPresetId}
                onUserDrilldown={handleInsightUserDrilldown}
                onPeakDrilldown={() => setDrilldown({ kind: "users" })}
                onNewUsersDrilldown={() =>
                  setDrilldown({ kind: "users", initialFilters: { firstUse: true } })
                }
              />
            </div>

            {/* Dimension rankings + feedback overview */}
            <AnalyticsTopRow
              summary={summary}
              sessionsByAgent={sessionsByAgent}
              sessionsByPersona={sessionsByPersona}
              tokensByModel={tokensByModel}
              tokensByModelPartial={tokensByModelPartial}
              feedbackSummary={feedbackSummary}
              feedbackByPreset={feedbackByPreset}
              isLoading={isLoading}
              errors={sectionErrors}
              onAgentSliceClick={(entry) =>
                setDrilldown({
                  kind: "sessions",
                  initialFilters: { agentId: entry.label },
                })
              }
              onPersonaSliceClick={(entry) =>
                setDrilldown({
                  kind: "sessions",
                  initialFilters: {
                    personaPresetId: entry.id || entry.label,
                  },
                })
              }
              onModelSliceClick={(entry) =>
                setDrilldown({
                  kind: "runs",
                  // label is the display name; the runs filter needs the raw model id.
                  initialFilters: { model: entry.id || entry.label },
                })
              }
              onFeedbackPresetClick={(entry) =>
                setDrilldown({ kind: "feedback", presetId: entry.preset_id })
              }
              onReasonClick={() => setDrilldown({ kind: "feedback", rating: "down" })}
            />
          </>
        ) : (
          <AnalyticsUsageTable
            rows={usageRows}
            total={usageTotal}
            page={usagePage}
            pageSize={USAGE_PAGE_SIZE}
            isLoading={usageLoading}
            error={usageError}
            isExporting={isUsageExporting}
            exportError={usageExportError}
            search={usageSearch}
            onSearchChange={setUsageSearch}
            summary={summary}
            onPageChange={setUsagePage}
            onExport={handleUsageExport}
          />
        )}
      </div>
    </div>
  );
}
