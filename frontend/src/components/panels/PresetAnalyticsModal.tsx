/**
 * Preset Analytics Modal - 单 Persona 专用分析
 *
 * 仅展示该人设指标（getPresetAnalytics）+ 锁死 persona 的钻取明细。
 * 不展示全局 by-agent / by-persona 对比看板。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  Clock,
  Hash,
  MessageSquare,
  ThumbsUp,
  Users,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { EditorSidebar } from "../common/EditorSidebar";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { analyticsApi } from "../../services/api/analytics";
import type {
  AnalyticsRangePreset,
  PresetAnalyticsResponse,
} from "../../types/analytics";
import type { PersonaPreset } from "../../types";
import {
  AnalyticsDrilldownList,
  type DrilldownKind,
} from "./AnalyticsDrilldownList";
import { AnalyticsRangePresetPicker } from "./analytics/AnalyticsFilterBar";
import {
  effectiveRangeFor,
  formatRangeLabel,
  type AnalyticsDateRange,
  type FixedRangePreset,
} from "./analytics/analyticsDates";

const BAR_COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444"];

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString();
}

interface PresetAnalyticsModalProps {
  open: boolean;
  preset: PersonaPreset | null;
  onClose: () => void;
}

function StatsCard({
  icon: Icon,
  label,
  value,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  onClick?: () => void;
}) {
  const className = [
    "glass-card flex w-full items-center gap-3 rounded-xl p-4 text-left",
    onClick
      ? "cursor-pointer transition-colors hover:bg-[var(--glass-bg-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  const content = (
    <>
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-[var(--glass-bg-subtle)]">
        <Icon
          size={22}
          className="text-stone-600 dark:text-stone-400"
          aria-hidden
        />
      </div>
      <div className="min-w-0">
        <p className="truncate text-xs text-stone-500 dark:text-stone-400">
          {label}
        </p>
        <p className="truncate text-lg font-bold text-stone-900 dark:text-stone-100">
          {value}
        </p>
      </div>
    </>
  );

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className}>
        {content}
      </button>
    );
  }

  return <div className={className}>{content}</div>;
}

export function PresetAnalyticsModal({
  open,
  preset,
  onClose,
}: PresetAnalyticsModalProps) {
  const { t } = useTranslation();
  const [rangePreset, setRangePreset] = useState<AnalyticsRangePreset>("7d");
  const [customRange, setCustomRange] = useState<AnalyticsDateRange | null>(null);

  const [metrics, setMetrics] = useState<PresetAnalyticsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drilldown, setDrilldown] = useState<{
    kind: DrilldownKind;
    rating?: "up" | "down";
  } | null>(null);

  const effectiveRange = useMemo(
    () => effectiveRangeFor(rangePreset, customRange),
    [rangePreset, customRange],
  );

  const rangeAnchor = useMemo(
    () => `${formatRangeLabel(effectiveRange)}（UTC+8）`,
    [effectiveRange],
  );

  const fetchMetrics = useCallback(async () => {
    if (!preset) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await analyticsApi.getPresetAnalytics(
        preset.id,
        effectiveRange.start,
        effectiveRange.end,
      );
      setMetrics(data ?? null);
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : t("common.loadFailed", "Load failed");
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [preset, effectiveRange, t]);

  useEffect(() => {
    if (open) {
      fetchMetrics();
    } else {
      setDrilldown(null);
    }
  }, [open, fetchMetrics]);

  useEffect(() => {
    setDrilldown(null);
  }, [preset?.id]);

  const handlePresetChange = useCallback((next: FixedRangePreset) => {
    setRangePreset(next);
    setCustomRange(null);
  }, []);

  const handleCustomRangeApply = useCallback((range: AnalyticsDateRange) => {
    setCustomRange(range);
    setRangePreset("custom");
  }, []);

  const downReasons = metrics?.down_reasons ?? [];
  const personaId = preset?.id;
  const titleName = preset?.name;

  return (
    <EditorSidebar
      open={open}
      onClose={onClose}
      title={
        titleName
          ? t("analytics.preset.titleNamed", "「{{name}}」分析", {
              name: titleName,
            })
          : t("analytics.preset.title", "Persona 分析")
      }
      subtitle={t("analytics.preset.subtitle", "单 Persona 指标")}
      icon={<BarChart3 size={18} aria-hidden />}
      width="wide"
    >
      {/* Time range filter (shared preset picker with the global dashboard) */}
      <div className="glass-card mb-4 flex flex-wrap items-center gap-2 rounded-xl p-3">
        <div className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-300">
          <Clock size={16} aria-hidden />
          <span className="font-medium">{t("analytics.timeRange.label")}</span>
        </div>
        <AnalyticsRangePresetPicker
          preset={rangePreset}
          customLabel={formatRangeLabel(effectiveRange)}
          onPresetChange={handlePresetChange}
          onCustomRangeApply={handleCustomRangeApply}
        />
        <span className="text-xs font-medium text-stone-500 dark:text-stone-400">
          {rangeAnchor}
        </span>
      </div>

      {error ? (
        <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
          {error}
        </div>
      ) : null}

      {drilldown && personaId ? (
        <AnalyticsDrilldownList
          kind={drilldown.kind}
          start={effectiveRange.start}
          end={effectiveRange.end}
          presetId={personaId}
          rating={drilldown.rating}
          initialFilters={{ personaPresetId: personaId }}
          lockPersonaPresetId
          personaOptions={[{ id: personaId, name: preset?.name || personaId }]}
          onBack={() => setDrilldown(null)}
        />
      ) : isLoading ? (
        <LoadingSpinner />
      ) : (
        <>
          {/* Overview cards — clickable for locked-persona drilldown */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatsCard
              icon={Users}
              label={t("analytics.preset.activeUsers", "活跃用户数")}
              value={metrics ? formatNumber(metrics.active_users) : "—"}
              onClick={() => setDrilldown({ kind: "users" })}
            />
            <StatsCard
              icon={MessageSquare}
              label={t("analytics.preset.totalSessions", "总会话数")}
              value={metrics ? formatNumber(metrics.total_sessions) : "—"}
              onClick={() => setDrilldown({ kind: "sessions" })}
            />
            <StatsCard
              icon={Hash}
              label={t("analytics.preset.totalTokens", "token 消耗总计")}
              value={metrics ? formatNumber(metrics.total_tokens) : "—"}
            />
            <StatsCard
              icon={ThumbsUp}
              label={t("analytics.preset.upVoteRate", "点赞率")}
              value={
                metrics ? `${metrics.up_vote_rate.toFixed(1)}%` : "—"
              }
              onClick={() => setDrilldown({ kind: "feedback" })}
            />
          </div>

          <div className="glass-card mt-4 rounded-xl p-4">
            <div className="flex items-center gap-2">
              <MessageSquare
                size={16}
                className="text-stone-600 dark:text-stone-400"
              />
              <span className="text-sm font-medium text-stone-700 dark:text-stone-200">
                {t("analytics.preset.totalMessages", "用户消息数")}
              </span>
              <span className="ml-auto text-lg font-bold text-stone-900 dark:text-stone-100">
                {metrics ? formatNumber(metrics.total_messages) : "—"}
              </span>
            </div>
          </div>

          {/* Down reasons bar chart */}
          <div className="glass-card mt-4 rounded-xl p-4">
            <h4 className="mb-2 text-sm font-medium text-stone-700 dark:text-stone-200">
              {t("analytics.preset.downReasons", "点踩原因分布")}
            </h4>
            {downReasons.length === 0 ? (
              <div className="flex h-32 items-center justify-center text-sm text-stone-500 dark:text-stone-400">
                {t("analytics.preset.noDownReasons", "暂无点踩原因数据")}
              </div>
            ) : (
              <button
                type="button"
                className="h-56 w-full cursor-pointer rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
                onClick={() =>
                  setDrilldown({ kind: "feedback", rating: "down" })
                }
                aria-label={t("analytics.preset.downReasons", "点踩原因分布")}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={downReasons}
                    layout="vertical"
                    margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="rgba(120,113,108,0.2)"
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      tick={{ fontSize: 11, fill: "currentColor" }}
                      allowDecimals={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="label"
                      tick={{ fontSize: 11, fill: "currentColor" }}
                      width={120}
                      tickFormatter={(value: string) =>
                        t(`feedback.reason.${value}`, value)
                      }
                    />
                    <Tooltip
                      formatter={(value: number) => [
                        formatNumber(Number(value)),
                        "",
                      ]}
                      labelFormatter={(label: string) =>
                        t(`feedback.reason.${label}`, label)
                      }
                    />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                      {downReasons.map((entry, index) => (
                        <Cell
                          key={entry.label}
                          fill={BAR_COLORS[index % BAR_COLORS.length]}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </button>
            )}
          </div>
        </>
      )}
    </EditorSidebar>
  );
}

export default PresetAnalyticsModal;
