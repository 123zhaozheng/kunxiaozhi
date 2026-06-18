/**
 * Analytics Panel - 全局统计看板
 *
 * 提供时间筛选器 + 4 个概览卡片 + 用户/会话/Token 三大板块图表。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  Calendar,
  Check,
  ChevronDown,
  Clock,
  Cpu,
  Hash,
  MessageSquare,
  ThumbsUp,
  User as UserIcon,
  Users,
} from "lucide-react";
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PanelHeader } from "../common/PanelHeader";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { analyticsApi } from "../../services/api/analytics";
import type {
  AnalyticsRangePreset,
  ByLabelItem,
  HeatmapCell,
  OverviewResponse,
  SessionsTrendResponse,
  TrendDataPoint,
} from "../../types/analytics";

const PIE_COLORS = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#3b82f6",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#84cc16",
];

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

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
  // 30d
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

function StatsCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
}) {
  return (
    <div className="glass-card flex items-center gap-3 rounded-xl p-4 sm:p-5">
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-[var(--glass-bg-subtle)] sm:h-12 sm:w-12">
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
        <p className="truncate text-lg font-bold text-stone-900 dark:text-stone-100 sm:text-xl">
          {value}
        </p>
      </div>
    </div>
  );
}

function ChartCard({
  title,
  subtitle,
  icon,
  isLoading,
  isEmpty,
  emptyText,
  children,
}: {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  isLoading?: boolean;
  isEmpty?: boolean;
  emptyText?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="glass-card flex min-h-[260px] flex-col rounded-xl p-4 sm:p-5">
      <div className="mb-3 flex items-center gap-2">
        {icon ? (
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--glass-bg-subtle)]">
            {icon}
          </div>
        ) : null}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-100">
            {title}
          </h3>
          {subtitle ? (
            <p className="truncate text-xs text-stone-500 dark:text-stone-400">
              {subtitle}
            </p>
          ) : null}
        </div>
      </div>
      <div className="relative flex-1">
        {isLoading ? (
          <PanelLoadingState containerClassName="absolute inset-0" />
        ) : isEmpty ? (
          <div className="flex h-full items-center justify-center text-sm text-stone-500 dark:text-stone-400">
            {emptyText ?? "No data"}
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

interface PresetButtonProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function PresetButton({ label, active, onClick }: PresetButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
        active
          ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
          : "text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
      }`}
    >
      {label}
    </button>
  );
}

interface CustomRangePickerProps {
  open: boolean;
  start: string;
  end: string;
  onStartChange: (value: string) => void;
  onEndChange: (value: string) => void;
  onApply: () => void;
  onCancel: () => void;
}

function CustomRangePicker({
  open,
  start,
  end,
  onStartChange,
  onEndChange,
  onApply,
  onCancel,
}: CustomRangePickerProps) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <div className="absolute right-0 top-full z-40 mt-2 w-72 rounded-xl border border-[var(--glass-border)] bg-[var(--theme-bg-card)] p-3 shadow-xl">
      <label className="block text-xs text-stone-500 dark:text-stone-400">
        {t("analytics.timeRange.start")}
      </label>
      <input
        type="date"
        value={start}
        onChange={(e) => onStartChange(e.target.value)}
        className="glass-input mt-1 w-full px-2 py-1.5 text-sm"
      />
      <label className="mt-2 block text-xs text-stone-500 dark:text-stone-400">
        {t("analytics.timeRange.end")}
      </label>
      <input
        type="date"
        value={end}
        onChange={(e) => onEndChange(e.target.value)}
        className="glass-input mt-1 w-full px-2 py-1.5 text-sm"
      />
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-3 py-1.5 text-sm text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
        >
          {t("common.cancel", "Cancel")}
        </button>
        <button
          type="button"
          onClick={onApply}
          className="rounded-lg bg-[var(--theme-primary)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          <span className="inline-flex items-center gap-1">
            <Check size={14} />
            {t("analytics.timeRange.apply", "Apply")}
          </span>
        </button>
      </div>
    </div>
  );
}

interface HeatmapGridProps {
  cells: HeatmapCell[];
}

function HeatmapGrid({ cells }: HeatmapGridProps) {
  const countMap = useMemo(() => {
    const map = new Map<string, number>();
    (cells ?? []).forEach((cell) => {
      if (cell == null) return;
      map.set(`${cell.weekday}-${cell.hour}`, cell.count);
    });
    return map;
  }, [cells]);

  const max = useMemo(
    () => (cells ?? []).reduce((acc, c) => Math.max(acc, c?.count ?? 0), 0) || 1,
    [cells],
  );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-1 pl-9 text-[10px] text-stone-500 dark:text-stone-400">
        {Array.from({ length: 24 }, (_, hour) => (
          <div
            key={hour}
            className="flex-1 text-center"
            style={{ minWidth: 12 }}
          >
            {hour % 3 === 0 ? hour : ""}
          </div>
        ))}
      </div>
      {WEEKDAY_LABELS.map((label, weekday) => (
        <div key={label} className="flex items-center gap-1">
          <div className="w-8 text-[10px] font-medium text-stone-500 dark:text-stone-400">
            {label}
          </div>
          <div className="flex flex-1 gap-[2px]">
            {Array.from({ length: 24 }, (_, hour) => {
              const count = countMap.get(`${weekday}-${hour}`) ?? 0;
              const intensity = count / max;
              const bg =
                count === 0
                  ? "rgba(120,113,108,0.08)"
                  : `rgba(99,102,241,${0.15 + intensity * 0.85})`;
              return (
                <div
                  key={hour}
                  className="h-5 flex-1 rounded-sm"
                  style={{ backgroundColor: bg, minWidth: 12 }}
                  title={`${label} ${hour}:00 - ${count}`}
                />
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

interface PieBlockProps {
  title: string;
  data: ByLabelItem[];
  unitFormatter?: (value: number) => string;
}

function PieBlock({ title, data, unitFormatter }: PieBlockProps) {
  const renderLabel = useCallback(
    (entry: ByLabelItem) => {
      const label = entry?.label ?? "—";
      const truncated =
        label.length > 12 ? `${label.slice(0, 12)}…` : label;
      const value = unitFormatter
        ? unitFormatter(entry?.value ?? 0)
        : formatNumber(entry?.value ?? 0);
      return `${truncated} (${value})`;
    },
    [unitFormatter],
  );
  return (
    <div>
      <h4 className="mb-2 text-sm font-medium text-stone-700 dark:text-stone-200">
        {title}
      </h4>
      {(data?.length ?? 0) === 0 ? (
        <div className="flex h-32 items-center justify-center text-sm text-stone-500 dark:text-stone-400">
          —
        </div>
      ) : (
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="label"
                cx="50%"
                cy="50%"
                outerRadius={70}
                innerRadius={30}
                paddingAngle={1}
              >
                {data.map((entry, index) => (
                  <Cell
                    key={entry.label}
                    fill={PIE_COLORS[index % PIE_COLORS.length]}
                  />
                ))}
              </Pie>
              <Tooltip
                formatter={(value: number, _name, props) => [
                  unitFormatter ? unitFormatter(value) : formatNumber(value),
                  props?.payload?.label ?? "",
                ]}
              />
              <Legend
                layout="vertical"
                align="right"
                verticalAlign="middle"
                iconSize={8}
                wrapperStyle={{ fontSize: 11, lineHeight: "16px" }}
                formatter={renderLabel}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

interface LineTrendProps {
  series: Array<{
    key: string;
    color: string;
    label: string;
    data: TrendDataPoint[];
  }>;
  valueFormatter?: (value: number) => string;
}

function LineTrend({ series, valueFormatter }: LineTrendProps) {
  const merged = useMemo(() => {
    const dates = new Set<string>();
    (series ?? []).forEach((s) => {
      (s?.data ?? []).forEach((d) => {
        if (d?.date) dates.add(d.date);
      });
    });
    const sortedDates = Array.from(dates).sort();
    return sortedDates.map((date) => {
      const row: Record<string, string | number> = { date };
      (series ?? []).forEach((s) => {
        const match = (s?.data ?? []).find((d) => d?.date === date);
        row[s?.key ?? ""] = match ? Number(match.value ?? 0) : 0;
      });
      return row;
    });
  }, [series]);

  if (merged.length === 0) return null;

  const singlePoint = merged.length === 1;

  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={merged} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(120,113,108,0.2)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={(value) => {
              if (value == null) return "";
              const parts = String(value).split("-");
              if (parts.length === 3) {
                return `${parts[1]}/${parts[2]}`;
              }
              return String(value);
            }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={(value: number) => formatNumber(Number(value))}
            width={40}
          />
          <Tooltip
            formatter={(value: number, name: string) => [
              valueFormatter ? valueFormatter(value) : formatNumber(value),
              name,
            ]}
            labelFormatter={(label) => `Date: ${label}`}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              dot={singlePoint ? { r: 4 } : false}
              activeDot={{ r: 4 }}
              label={
                singlePoint
                  ? (props: {
                      x?: number;
                      y?: number;
                      value?: number;
                    }) => {
                      const v = Number(props?.value ?? 0);
                      const text = valueFormatter ? valueFormatter(v) : formatNumber(v);
                      return (
                        <text
                          x={props.x}
                          y={(props.y ?? 0) - 10}
                          fill="currentColor"
                          fontSize={11}
                          textAnchor="middle"
                        >
                          {text}
                        </text>
                      );
                    }
                  : false
              }
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function AnalyticsPanel() {
  const { t } = useTranslation();
  const [preset, setPreset] = useState<AnalyticsRangePreset>("7d");
  const [customRange, setCustomRange] = useState<DateRange | null>(null);
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [activeTrend, setActiveTrend] = useState<TrendDataPoint[]>([]);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [sessionsTrend, setSessionsTrend] =
    useState<SessionsTrendResponse | null>(null);
  const [tokensByModel, setTokensByModel] = useState<ByLabelItem[]>([]);
  const [tokensByPreset, setTokensByPreset] = useState<ByLabelItem[]>([]);
  const [tokensTrend, setTokensTrend] = useState<TrendDataPoint[]>([]);

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const effectiveRange: DateRange = useMemo(
    () =>
      preset === "custom" && customRange
        ? customRange
        : rangeForPreset(preset),
    [preset, customRange],
  );

  const rangeAnchor = useMemo(
    () =>
      `${formatCSTDate(effectiveRange.start)} → ${formatCSTDate(
        effectiveRange.end,
      )}（UTC+8）`,
    [effectiveRange.start, effectiveRange.end],
  );

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const start = toIso(effectiveRange.start);
    const end = toIso(effectiveRange.end);
    try {
      const [
        overviewData,
        activeData,
        heatmapData,
        sessionsData,
        byModelData,
        byPresetData,
        tokensTrendData,
      ] = await Promise.all([
        analyticsApi.getOverview(start, end),
        analyticsApi.getActiveUserTrend(start, end),
        analyticsApi.getUsersHeatmap(start, end),
        analyticsApi.getSessionsTrend(start, end),
        analyticsApi.getTokensByModel(start, end),
        analyticsApi.getTokensByPreset(start, end, 10),
        analyticsApi.getTokensTrend(start, end),
      ]);
      setOverview(overviewData ?? null);
      setActiveTrend(Array.isArray(activeData?.items) ? activeData.items : []);
      setHeatmap(Array.isArray(heatmapData?.cells) ? heatmapData.cells : []);
      setSessionsTrend(sessionsData ?? null);
      setTokensByModel(Array.isArray(byModelData?.items) ? byModelData.items : []);
      setTokensByPreset(
        Array.isArray(byPresetData?.items) ? byPresetData.items : [],
      );
      setTokensTrend(
        Array.isArray(tokensTrendData?.items) ? tokensTrendData.items : [],
      );
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("common.loadFailed", "Load failed");
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [effectiveRange.start, effectiveRange.end, t]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

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
    setPreset("custom");
    setPickerOpen(false);
  };

  const handlePresetClick = (next: AnalyticsRangePreset) => {
    if (next === "custom") {
      setPickerOpen((open) => !open);
      return;
    }
    setPreset(next);
    setPickerOpen(false);
    setCustomRange(null);
  };

  const presetLabel = useMemo(() => {
    if (preset === "custom" && customRange) {
      const fmt = (d: Date) =>
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      return `${fmt(customRange.start)} → ${fmt(customRange.end)}`;
    }
    return t(`analytics.timeRange.${preset}`);
  }, [preset, customRange, t]);

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

      {/* Time range filter */}
      <div className="px-4 pb-3 pt-1 sm:px-6">
        <div className="glass-card flex flex-wrap items-center gap-2 rounded-xl p-3">
          <div className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-300">
            <Clock size={16} aria-hidden />
            <span className="font-medium">{t("analytics.timeRange.label")}</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="tablist">
            <PresetButton
              label={t("analytics.timeRange.1d", "1 day")}
              active={preset === "1d"}
              onClick={() => handlePresetClick("1d")}
            />
            <PresetButton
              label={t("analytics.timeRange.7d", "7 days")}
              active={preset === "7d"}
              onClick={() => handlePresetClick("7d")}
            />
            <PresetButton
              label={t("analytics.timeRange.30d", "30 days")}
              active={preset === "30d"}
              onClick={() => handlePresetClick("30d")}
            />
          </div>
          <span className="text-xs font-medium text-stone-500 dark:text-stone-400">
            {rangeAnchor}
          </span>
          <div ref={pickerRef} className="relative ml-auto">
            <button
              type="button"
              onClick={() => handlePresetClick("custom")}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                preset === "custom"
                  ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                  : "text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
              }`}
            >
              <Calendar size={14} aria-hidden />
              <span>{presetLabel}</span>
              <ChevronDown size={14} aria-hidden />
            </button>
            <CustomRangePicker
              open={pickerOpen}
              start={customStart}
              end={customEnd}
              onStartChange={setCustomStart}
              onEndChange={setCustomEnd}
              onApply={applyCustomRange}
              onCancel={() => setPickerOpen(false)}
            />
          </div>
        </div>
      </div>

      {error ? (
        <div className="px-4 sm:px-6">
          <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
            {error}
          </div>
        </div>
      ) : null}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 pb-6 sm:px-6">
        {/* Overview cards */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatsCard
            icon={Users}
            label={t("analytics.overview.activeUsers")}
            value={overview ? formatNumber(overview.active_users) : "—"}
          />
          <StatsCard
            icon={MessageSquare}
            label={t("analytics.overview.totalSessions")}
            value={overview ? formatNumber(overview.total_sessions) : "—"}
          />
          <StatsCard
            icon={Hash}
            label={t("analytics.overview.totalTokens")}
            value={overview ? formatNumber(overview.total_tokens) : "—"}
          />
          <StatsCard
            icon={ThumbsUp}
            label={t("analytics.overview.upVoteRate")}
            value={
              overview ? `${overview.up_vote_rate.toFixed(1)}%` : "—"
            }
          />
        </div>

        {/* Users section */}
        <section className="mt-4">
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-stone-600 uppercase dark:text-stone-400">
            {t("analytics.users.title", "Users")}
          </h2>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <ChartCard
                title={t("analytics.users.activeTrend", "Active users")}
                subtitle={t(
                  "analytics.users.activeTrendHint",
                  "Distinct users active per day",
                )}
                icon={<UserIcon size={16} aria-hidden />}
                isLoading={isLoading}
                isEmpty={!isLoading && (activeTrend?.length ?? 0) === 0}
              >
                <LineTrend
                  series={[
                    {
                      key: "active",
                      color: "#6366f1",
                      label: t("analytics.users.active", "Active"),
                      data: activeTrend,
                    },
                  ]}
                />
              </ChartCard>
            </div>
            <ChartCard
              title={t("analytics.users.heatmap", "Activity heatmap")}
              subtitle={t(
                "analytics.users.heatmapHint",
                "Weekday × hour (sessions)",
              )}
              icon={<BarChart3 size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={!isLoading && (heatmap?.length ?? 0) === 0}
            >
              <div className="pt-2">
                <HeatmapGrid cells={heatmap} />
              </div>
            </ChartCard>
          </div>
        </section>

        {/* Sessions section */}
        <section className="mt-4">
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-stone-600 uppercase dark:text-stone-400">
            {t("analytics.sessions.title", "Sessions")}
          </h2>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            <div className="lg:col-span-3">
              <ChartCard
                title={t("analytics.sessions.trend", "Sessions & messages")}
                subtitle={t(
                  "analytics.sessions.trendHint",
                  "Counts per day across the time range",
                )}
                icon={<MessageSquare size={16} aria-hidden />}
                isLoading={isLoading}
                isEmpty={
                  !isLoading &&
                  (sessionsTrend?.sessions.length ?? 0) === 0 &&
                  (sessionsTrend?.messages.length ?? 0) === 0
                }
              >
                <LineTrend
                  series={[
                    {
                      key: "sessions",
                      color: "#6366f1",
                      label: t("analytics.sessions.sessions", "Sessions"),
                      data: sessionsTrend?.sessions ?? [],
                    },
                    {
                      key: "messages",
                      color: "#10b981",
                      label: t("analytics.sessions.messages", "Messages"),
                      data: sessionsTrend?.messages ?? [],
                    },
                  ]}
                />
              </ChartCard>
            </div>
          </div>
        </section>

        {/* Tokens section */}
        <section className="mt-4">
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-stone-600 uppercase dark:text-stone-400">
            {t("analytics.tokens.title", "Tokens")}
          </h2>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <ChartCard
              title={t("analytics.tokens.byModel", "Tokens by model")}
              subtitle={t(
                "analytics.tokens.byModelHint",
                "Total tokens per model",
              )}
              icon={<Cpu size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={!isLoading && (tokensByModel?.length ?? 0) === 0}
            >
              <PieBlock title="" data={tokensByModel} />
            </ChartCard>
            <ChartCard
              title={t("analytics.tokens.byPreset", "Tokens by agent type (Top 10)")}
              subtitle={t(
                "analytics.tokens.byPresetHint",
                "Total tokens per agent type",
              )}
              icon={<UserIcon size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={!isLoading && (tokensByPreset?.length ?? 0) === 0}
            >
              <PieBlock title="" data={tokensByPreset} />
            </ChartCard>
          </div>
          <div className="mt-3">
            <ChartCard
              title={t("analytics.tokens.trend", "Token usage over time")}
              subtitle={t(
                "analytics.tokens.trendHint",
                "Total tokens per day",
              )}
              icon={<Hash size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={!isLoading && (tokensTrend?.length ?? 0) === 0}
            >
              <LineTrend
                series={[
                  {
                    key: "tokens",
                    color: "#6366f1",
                    label: t("analytics.tokens.total", "Total tokens"),
                    data: tokensTrend,
                  },
                ]}
                valueFormatter={(value) =>
                  `${formatNumber(value)} ${t("analytics.tokens.unit", "tokens")}`
                }
              />
            </ChartCard>
          </div>
        </section>
      </div>
    </div>
  );
}

export default AnalyticsPanel;
