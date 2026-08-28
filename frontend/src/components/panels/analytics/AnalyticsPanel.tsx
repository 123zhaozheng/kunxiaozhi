/**
 * Analytics Panel — 全局统计看板（筛选 state + 数据编排 + 区块布局）
 *
 * 唯一发请求的组件：时间预设 / Persona / 智能体 三个筛选驱动所有区块，
 * 八个一致性契约请求（summary / trend / insights / by-agent / by-persona /
 * by-model / by-user / export）共用同一批 `start`/`end`（纯 YYYY-MM-DD 日期
 * 字符串）与 `usageFilters`。日界由后端（UTC+8 半开区间）负责。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BarChart3 } from "lucide-react";
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
  effectiveRangeFor,
  type AnalyticsDateRange,
  type FixedRangePreset,
} from "./analyticsDates";

const USAGE_PAGE_SIZE = 20;

export function AnalyticsPanel() {
  const { t } = useTranslation();

  // ── Filter state (single source of truth for every request) ──────────
  const [preset, setPreset] = useState<AnalyticsRangePreset>("7d");
  const [customRange, setCustomRange] = useState<AnalyticsDateRange | null>(null);
  const [personaPresetId, setPersonaPresetId] = useState("");
  const [agentId, setAgentId] = useState("");

  const effectiveRange = useMemo(
    () => effectiveRangeFor(preset, customRange),
    [preset, customRange],
  );

  const usageFilters = useMemo<UsageFilters>(
    () => ({
      personaPresetId: personaPresetId || undefined,
      agentId: agentId || undefined,
    }),
    [personaPresetId, agentId],
  );

  // ── Data state ────────────────────────────────────────────────────────
  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
  const [insights, setInsights] = useState<UsageInsightsResponse | null>(null);
  const [usageTrend, setUsageTrend] = useState<UsageTrendPoint[]>([]);
  const [activeTrend, setActiveTrend] = useState<TrendDataPoint[]>([]);
  const [sessionsByAgent, setSessionsByAgent] = useState<ByLabelItem[]>([]);
  const [sessionsByPersona, setSessionsByPersona] = useState<ByLabelItem[]>([]);
  const [tokensByModel, setTokensByModel] = useState<ByLabelItem[]>([]);
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
  const usageTableRef = useRef<HTMLDivElement | null>(null);

  const [personaOptions, setPersonaOptions] = useState<Array<{ id: string; name: string }>>([]);

  const [drilldown, setDrilldown] = useState<{
    kind: DrilldownKind;
    presetId?: string;
    rating?: "up" | "down";
    initialFilters?: AnalyticsDrilldownFilters;
  } | null>(null);

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  // ── Data orchestration: one batch of requests per filter change ──────
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const { start, end } = effectiveRange;
    try {
      const [
        summaryData,
        insightsData,
        usageTrendData,
        activeData,
        byAgentData,
        byPersonaData,
        byModelData,
        feedbackSummaryData,
        feedbackByPresetData,
      ] = await Promise.all([
        analyticsApi.getUsageSummary(start, end, usageFilters),
        analyticsApi.getUsageInsights(start, end, usageFilters),
        analyticsApi.getUsageTrend(start, end, usageFilters),
        analyticsApi.getActiveUserTrend(start, end, usageFilters),
        analyticsApi.getSessionsByAgent(start, end, usageFilters, 100),
        analyticsApi.getSessionsByPersona(start, end, usageFilters, 10),
        analyticsApi.getTokensByModel(start, end, usageFilters),
        analyticsApi.getFeedbackSummary(start, end),
        analyticsApi.getFeedbackByPreset(start, end),
      ]);
      setSummary(summaryData ?? null);
      setInsights(insightsData ?? null);
      setUsageTrend(Array.isArray(usageTrendData?.items) ? usageTrendData.items : []);
      setActiveTrend(Array.isArray(activeData?.items) ? activeData.items : []);
      setSessionsByAgent(
        Array.isArray(byAgentData?.items) ? byAgentData.items : [],
      );
      setSessionsByPersona(
        Array.isArray(byPersonaData?.items) ? byPersonaData.items : [],
      );
      setTokensByModel(Array.isArray(byModelData?.items) ? byModelData.items : []);
      setFeedbackSummary(feedbackSummaryData ?? null);
      setFeedbackByPreset(
        Array.isArray(feedbackByPresetData?.items)
          ? feedbackByPresetData.items
          : [],
      );
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("common.loadFailed", "Load failed");
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [effectiveRange, usageFilters, t]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Usage detail (paginated) shares the exact same filter batch.
  useEffect(() => {
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
  }, [effectiveRange, usageFilters, usagePage, t]);

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
  }, []);

  const handleCustomRangeApply = useCallback((range: AnalyticsDateRange) => {
    setCustomRange(range);
    setPreset("custom");
  }, []);

  // A persona/agent filter switches the first KPI card to 使用用户 (using_users).
  const isFiltered = Boolean(personaPresetId || agentId);

  // Insight "Token 大户" click → filter the usage detail by that user and reveal it.
  const handleInsightUserDrilldown = useCallback(
    (user: UsageInsightsTopTokenUser) => {
      setUsageSearch(user.username || user.user_id);
      setUsagePage(1);
      usageTableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [],
  );

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

      {error ? (
        <div className="px-4 sm:px-6">
          <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
            {error}
          </div>
        </div>
      ) : null}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 pb-6 sm:px-6">
        {/* KPI row */}
        <AnalyticsKpiRow
          summary={summary}
          usageTrend={usageTrend}
          activeTrend={activeTrend}
          isFiltered={isFiltered}
          onUsersDrilldown={() => setDrilldown({ kind: "users" })}
          onSessionsDrilldown={() => setDrilldown({ kind: "sessions" })}
        />

        {/* Core trend + insight column */}
        <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <AnalyticsTrendChart
              usageTrend={usageTrend}
              activeTrend={activeTrend}
              isLoading={isLoading}
            />
          </div>
          <AnalyticsInsightPanel
            insights={insights}
            isLoading={isLoading}
            onPersonaSelect={setPersonaPresetId}
            onUserDrilldown={handleInsightUserDrilldown}
            onNewUsersDrilldown={() => setDrilldown({ kind: "users" })}
          />
        </div>

        {/* Dimension donuts + feedback overview */}
        <AnalyticsTopRow
          summary={summary}
          sessionsByAgent={sessionsByAgent}
          sessionsByPersona={sessionsByPersona}
          tokensByModel={tokensByModel}
          feedbackSummary={feedbackSummary}
          feedbackByPreset={feedbackByPreset}
          isLoading={isLoading}
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
          onModelSliceClick={() => setDrilldown({ kind: "runs" })}
          onFeedbackPresetClick={(entry) =>
            setDrilldown({ kind: "feedback", presetId: entry.preset_id })
          }
          onReasonClick={() => setDrilldown({ kind: "feedback", rating: "down" })}
        />

        {/* Usage detail */}
        <div ref={usageTableRef} className="scroll-mt-4">
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
        </div>
      </div>

      {drilldown ? (
        <div className="px-4 pb-6 sm:px-6">
          <AnalyticsDrilldownList
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
        </div>
      ) : null}
    </div>
  );
}
