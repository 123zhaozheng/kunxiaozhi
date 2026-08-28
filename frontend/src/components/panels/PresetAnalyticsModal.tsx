/**
 * Preset Analytics Modal - 单 Persona 专用分析
 *
 * 仅展示该人设指标（getPresetAnalytics）+ 锁死 persona 的钻取明细。
 * 不展示全局 by-agent / by-persona 对比看板。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  Calendar,
  Check,
  ChevronDown,
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

const BAR_COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444"];

interface DateRange {
  start: Date;
  end: Date;
}

function endOfDayCST(d: Date): Date {
  const result = new Date(d);
  result.setHours(23, 59, 59, 999);
  return result;
}

function startOfDayCST(d: Date): Date {
  const result = new Date(d);
  result.setHours(0, 0, 0, 0);
  return result;
}

function rangeForPreset(preset: AnalyticsRangePreset): DateRange {
  const end = endOfDayCST(new Date());
  if (preset === "1d") {
    return { start: startOfDayCST(end), end };
  }
  const start = new Date(end);
  if (preset === "7d") {
    start.setDate(start.getDate() - 6);
    return { start: startOfDayCST(start), end };
  }
  start.setDate(start.getDate() - 29);
  return { start: startOfDayCST(start), end };
}

function toIso(d: Date): string {
  return d.toISOString();
}

function formatCSTDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

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
  const [customRange, setCustomRange] = useState<DateRange | null>(null);
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  const [metrics, setMetrics] = useState<PresetAnalyticsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drilldown, setDrilldown] = useState<{
    kind: DrilldownKind;
    rating?: "up" | "down";
  } | null>(null);

  const effectiveRange: DateRange = useMemo(
    () =>
      rangePreset === "custom" && customRange
        ? customRange
        : rangeForPreset(rangePreset),
    [rangePreset, customRange],
  );

  const rangeAnchor = useMemo(
    () =>
      `${formatCSTDate(effectiveRange.start)} → ${formatCSTDate(
        effectiveRange.end,
      )}（UTC+8）`,
    [effectiveRange.start, effectiveRange.end],
  );

  const fetchMetrics = useCallback(async () => {
    if (!preset) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await analyticsApi.getPresetAnalytics(
        preset.id,
        toIso(effectiveRange.start),
        toIso(effectiveRange.end),
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
  }, [preset, effectiveRange.start, effectiveRange.end, t]);

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

  useEffect(() => {
    if (!pickerOpen) return;
    const handler = (event: MouseEvent) => {
      if (
        pickerRef.current &&
        !pickerRef.current.contains(event.target as Node)
      ) {
        setPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pickerOpen]);

  const applyCustomRange = () => {
    if (!customStart || !customEnd) return;
    const start = new Date(`${customStart}T00:00:00+08:00`);
    const end = new Date(`${customEnd}T23:59:59+08:00`);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return;
    setCustomRange({
      start: start < end ? start : end,
      end: start < end ? end : start,
    });
    setRangePreset("custom");
    setPickerOpen(false);
  };

  const handlePresetClick = (next: AnalyticsRangePreset) => {
    if (next === "custom") {
      setPickerOpen((openState) => !openState);
      return;
    }
    setRangePreset(next);
    setPickerOpen(false);
    setCustomRange(null);
  };

  const presetLabel = useMemo(() => {
    if (rangePreset === "custom" && customRange) {
      const fmt = (d: Date) =>
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      return `${fmt(customRange.start)} → ${fmt(customRange.end)}`;
    }
    return t(`analytics.timeRange.${rangePreset}`);
  }, [rangePreset, customRange, t]);

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
      {/* Time range filter */}
      <div className="glass-card mb-4 flex flex-wrap items-center gap-2 rounded-xl p-3">
        <div className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-300">
          <Clock size={16} aria-hidden />
          <span className="font-medium">{t("analytics.timeRange.label")}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5" role="tablist">
          {(["1d", "7d", "30d"] as AnalyticsRangePreset[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => handlePresetClick(key)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                rangePreset === key
                  ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                  : "text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
              }`}
            >
              {t(`analytics.timeRange.${key}`)}
            </button>
          ))}
        </div>
        <span className="text-xs font-medium text-stone-500 dark:text-stone-400">
          {rangeAnchor}
        </span>
        <div ref={pickerRef} className="relative ml-auto">
          <button
            type="button"
            onClick={() => handlePresetClick("custom")}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              rangePreset === "custom"
                ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                : "text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
            }`}
          >
            <Calendar size={14} aria-hidden />
            <span>{presetLabel}</span>
            <ChevronDown size={14} aria-hidden />
          </button>
          {pickerOpen ? (
            <div className="absolute right-0 top-full z-40 mt-2 w-72 rounded-xl border border-[var(--glass-border)] bg-[var(--theme-bg-card)] p-3 shadow-xl">
              <label className="block text-xs text-stone-500 dark:text-stone-400">
                {t("analytics.timeRange.start")}
              </label>
              <input
                type="date"
                value={customStart}
                onChange={(e) => setCustomStart(e.target.value)}
                className="glass-input mt-1 w-full px-2 py-1.5 text-sm"
              />
              <label className="mt-2 block text-xs text-stone-500 dark:text-stone-400">
                {t("analytics.timeRange.end")}
              </label>
              <input
                type="date"
                value={customEnd}
                onChange={(e) => setCustomEnd(e.target.value)}
                className="glass-input mt-1 w-full px-2 py-1.5 text-sm"
              />
              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setPickerOpen(false)}
                  className="rounded-lg px-3 py-1.5 text-sm text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
                >
                  {t("common.cancel", "Cancel")}
                </button>
                <button
                  type="button"
                  onClick={applyCustomRange}
                  className="rounded-lg bg-[var(--theme-primary)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                >
                  <span className="inline-flex items-center gap-1">
                    <Check size={14} />
                    {t("analytics.timeRange.apply", "Apply")}
                  </span>
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
          {error}
        </div>
      ) : null}

      {drilldown && personaId ? (
        <AnalyticsDrilldownList
          kind={drilldown.kind}
          start={toIso(effectiveRange.start)}
          end={toIso(effectiveRange.end)}
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
