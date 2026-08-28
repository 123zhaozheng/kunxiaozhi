/**
 * Analytics Panel - 全局统计看板
 *
 * 时间 + Persona + 智能体 三个全局筛选，驱动概览卡片、趋势图、使用明细与导出。
 * 「用户消息」= 用户实际发出的消息数；「活跃」= 区间内发过消息。
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
  Download,
  Hash,
  MessageSquare,
  ThumbsDown,
  ThumbsUp,
  User as UserIcon,
  Users,
} from "lucide-react";
import {
  Bar,
  BarChart,
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
import { Pagination } from "../common/Pagination";
import { analyticsApi } from "../../services/api/analytics";
import { personaPresetApi } from "../../services/api/personaPreset";
import type {
  AnalyticsRangePreset,
  ByLabelItem,
  ByPresetFeedbackItem,
  FeedbackSummaryResponse,
  HeatmapCell,
  TrendDataPoint,
  UsageByUserItem,
  UsageFilters,
  UsageSummaryResponse,
  UsageTrendPoint,
} from "../../types/analytics";
import {
  AnalyticsDrilldownList,
  type AnalyticsDrilldownFilters,
  type DrilldownKind,
} from "./AnalyticsDrilldownList";

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

const USAGE_PAGE_SIZE = 20;

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

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function StatsCard({
  icon: Icon,
  label,
  value,
  hint,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  hint?: string;
  onClick?: () => void;
}) {
  const className = [
    "glass-card flex w-full items-center gap-3 rounded-xl p-4 sm:p-5 text-left",
    onClick
      ? "cursor-pointer transition-colors hover:bg-[var(--glass-bg-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  const content = (
    <>
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
        {hint ? (
          <p className="truncate text-[11px] text-stone-500 dark:text-stone-400">
            {hint}
          </p>
        ) : null}
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
  onSliceClick?: (entry: ByLabelItem) => void;
}

function PieBlock({ title, data, unitFormatter, onSliceClick }: PieBlockProps) {
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
                onClick={onSliceClick ? (entry) => onSliceClick(entry as ByLabelItem) : undefined}
              >
                {data.map((entry, index) => (
                  <Cell
                    key={entry.label}
                    fill={PIE_COLORS[index % PIE_COLORS.length]}
                    cursor={onSliceClick ? "pointer" : undefined}
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

interface FeedbackByPresetBarProps {
  data: ByPresetFeedbackItem[];
  onSliceClick?: (entry: ByPresetFeedbackItem) => void;
}

function FeedbackByPresetBar({ data, onSliceClick }: FeedbackByPresetBarProps) {
  const { t } = useTranslation();
  if ((data?.length ?? 0) === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-stone-500 dark:text-stone-400">
        —
      </div>
    );
  }
  const rows = data.map((d) => ({
    name: d.preset_name || d.preset_id,
    up: d.up_count,
    down: d.down_count,
    raw: d,
  }));
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="rgba(120,113,108,0.2)"
            horizontal={false}
          />
          <XAxis type="number" tick={{ fontSize: 11, fill: "currentColor" }} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fontSize: 11, fill: "currentColor" }}
            width={100}
          />
          <Tooltip
            formatter={(value: number, name: string) => [formatNumber(Number(value)), name]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar
            dataKey="up"
            name={t("analytics.feedback.up", "好评")}
            stackId="a"
            fill="#10b981"
            cursor={onSliceClick ? "pointer" : undefined}
            onClick={onSliceClick ? (entry) => onSliceClick((entry as { raw: ByPresetFeedbackItem }).raw) : undefined}
          />
          <Bar
            dataKey="down"
            name={t("analytics.feedback.down", "差评")}
            stackId="a"
            fill="#ef4444"
            cursor={onSliceClick ? "pointer" : undefined}
            onClick={onSliceClick ? (entry) => onSliceClick((entry as { raw: ByPresetFeedbackItem }).raw) : undefined}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

interface ReasonBarProps {
  data: ByLabelItem[];
  onSliceClick?: (entry: ByLabelItem) => void;
}

function ReasonBar({ data, onSliceClick }: ReasonBarProps) {
  const { t } = useTranslation();
  if ((data?.length ?? 0) === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-stone-500 dark:text-stone-400">
        {t("analytics.feedback.noReasons", "暂无点踩原因数据")}
      </div>
    );
  }
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="rgba(120,113,108,0.2)"
            horizontal={false}
          />
          <XAxis type="number" tick={{ fontSize: 11, fill: "currentColor" }} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="label"
            tick={{ fontSize: 11, fill: "currentColor" }}
            width={120}
            tickFormatter={(value: string) => t(`feedback.reason.${value}`, value)}
          />
          <Tooltip
            formatter={(value: number) => [formatNumber(Number(value)), ""]}
            labelFormatter={(label: string) => t(`feedback.reason.${label}`, label)}
          />
          <Bar
            dataKey="value"
            radius={[0, 4, 4, 0]}
            fill="#ef4444"
            cursor={onSliceClick ? "pointer" : undefined}
            onClick={onSliceClick ? (entry) => onSliceClick(entry as ByLabelItem) : undefined}
          />
        </BarChart>
      </ResponsiveContainer>
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

  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
  const [activeTrend, setActiveTrend] = useState<TrendDataPoint[]>([]);
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([]);
  const [usageTrend, setUsageTrend] = useState<UsageTrendPoint[]>([]);
  const [usageRows, setUsageRows] = useState<UsageByUserItem[]>([]);
  const [usageTotal, setUsageTotal] = useState(0);
  const [usagePage, setUsagePage] = useState(1);
  const [usageLoading, setUsageLoading] = useState(true);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [isUsageExporting, setIsUsageExporting] = useState(false);
  const [usageExportError, setUsageExportError] = useState<string | null>(null);
  const [personaOptions, setPersonaOptions] = useState<Array<{ id: string; name: string }>>([]);
  const [personaPresetId, setPersonaPresetId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [tokensByModel, setTokensByModel] = useState<ByLabelItem[]>([]);
  const [tokensByPreset, setTokensByPreset] = useState<ByLabelItem[]>([]);
  const [tokensTrend, setTokensTrend] = useState<TrendDataPoint[]>([]);
  const [feedbackSummary, setFeedbackSummary] =
    useState<FeedbackSummaryResponse | null>(null);
  const [feedbackByPreset, setFeedbackByPreset] = useState<ByPresetFeedbackItem[]>(
    [],
  );
  const [sessionsByAgent, setSessionsByAgent] = useState<ByLabelItem[]>([]);
  const [sessionsByPersona, setSessionsByPersona] = useState<ByLabelItem[]>([]);

  const [drilldown, setDrilldown] = useState<{
    kind: DrilldownKind;
    presetId?: string;
    rating?: "up" | "down";
    initialFilters?: AnalyticsDrilldownFilters;
  } | null>(null);

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

  const usageFilters = useMemo<UsageFilters>(
    () => ({
      personaPresetId: personaPresetId || undefined,
      agentId: agentId || undefined,
    }),
    [personaPresetId, agentId],
  );

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
              ? response.presets.map((preset) => ({ id: preset.id, name: preset.name }))
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

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const start = toIso(effectiveRange.start);
    const end = toIso(effectiveRange.end);
    try {
      const [
        summaryData,
        activeData,
        heatmapData,
        usageTrendData,
        byModelData,
        byPresetData,
        tokensTrendData,
        feedbackSummaryData,
        feedbackByPresetData,
        byAgentData,
        byPersonaData,
      ] = await Promise.all([
        analyticsApi.getUsageSummary(start, end, usageFilters),
        analyticsApi.getActiveUserTrend(start, end, usageFilters),
        analyticsApi.getUsersHeatmap(start, end),
        analyticsApi.getUsageTrend(start, end, usageFilters),
        analyticsApi.getTokensByModel(start, end),
        analyticsApi.getTokensByPreset(start, end, 10),
        analyticsApi.getTokensTrend(start, end),
        analyticsApi.getFeedbackSummary(start, end),
        analyticsApi.getFeedbackByPreset(start, end),
        analyticsApi.getSessionsByAgent(start, end, 100),
        analyticsApi.getSessionsByPersona(start, end, 10),
      ]);
      setSummary(summaryData ?? null);
      setActiveTrend(Array.isArray(activeData?.items) ? activeData.items : []);
      setHeatmap(Array.isArray(heatmapData?.cells) ? heatmapData.cells : []);
      setUsageTrend(Array.isArray(usageTrendData?.items) ? usageTrendData.items : []);
      setTokensByModel(Array.isArray(byModelData?.items) ? byModelData.items : []);
      setTokensByPreset(
        Array.isArray(byPresetData?.items) ? byPresetData.items : [],
      );
      setTokensTrend(
        Array.isArray(tokensTrendData?.items) ? tokensTrendData.items : [],
      );
      setFeedbackSummary(feedbackSummaryData ?? null);
      setFeedbackByPreset(
        Array.isArray(feedbackByPresetData?.items)
          ? feedbackByPresetData.items
          : [],
      );
      setSessionsByAgent(
        Array.isArray(byAgentData?.items) ? byAgentData.items : [],
      );
      setSessionsByPersona(
        Array.isArray(byPersonaData?.items) ? byPersonaData.items : [],
      );
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("common.loadFailed", "Load failed");
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [effectiveRange.start, effectiveRange.end, usageFilters, t]);

  useEffect(() => {
    let cancelled = false;
    const start = toIso(effectiveRange.start);
    const end = toIso(effectiveRange.end);
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
  }, [effectiveRange.start, effectiveRange.end, usageFilters, usagePage, t]);

  useEffect(() => {
    setUsagePage(1);
  }, [effectiveRange.start, effectiveRange.end, usageFilters]);

  const handleUsageExport = useCallback(async () => {
    setIsUsageExporting(true);
    setUsageExportError(null);
    try {
      await analyticsApi.exportUsageCsv(
        toIso(effectiveRange.start),
        toIso(effectiveRange.end),
        usageFilters,
      );
    } catch (err) {
      setUsageExportError(
        err instanceof Error ? err.message : t("analytics.usage.exportFailed"),
      );
    } finally {
      setIsUsageExporting(false);
    }
  }, [effectiveRange.start, effectiveRange.end, usageFilters, t]);

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
        {/* Persona and agent filters */}
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-stone-500 dark:text-stone-400">
            {t("analytics.filters.persona")}
            <select
              className="glass-input min-w-[10rem] px-2 py-1.5 text-sm"
              value={personaPresetId}
              onChange={(event) => setPersonaPresetId(event.target.value)}
            >
              <option value="">{t("analytics.filters.all")}</option>
              {personaOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-stone-500 dark:text-stone-400">
            {t("analytics.filters.agent")}
            <select
              className="glass-input min-w-[10rem] px-2 py-1.5 text-sm"
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
            >
              <option value="">{t("analytics.filters.all")}</option>
              {agentOptions.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
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
            value={summary ? formatNumber(summary.active_users) : "—"}
            onClick={() => setDrilldown({ kind: "users" })}
          />
          <StatsCard
            icon={MessageSquare}
            label={t("analytics.overview.sessions")}
            value={summary ? formatNumber(summary.new_sessions) : "—"}
            hint={
              summary
                ? t("analytics.overview.activeSessions", {
                    count: formatNumber(summary.active_sessions),
                  })
                : undefined
            }
            onClick={() => setDrilldown({ kind: "sessions" })}
          />
          <StatsCard
            icon={Hash}
            label={t("analytics.overview.userMessages")}
            value={summary ? formatNumber(summary.user_messages) : "—"}
          />
          <StatsCard
            icon={Cpu}
            label={t("analytics.overview.totalTokens")}
            value={summary ? formatNumber(summary.total_tokens) : "—"}
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
                isEmpty={!isLoading && usageTrend.length === 0}
              >
                <LineTrend
                  series={[
                    {
                      key: "sessions",
                      color: "#6366f1",
                      label: t("analytics.sessions.sessions", "Sessions"),
                      data: usageTrend.map((point) => ({
                        date: point.date,
                        value: point.new_sessions,
                      })),
                    },
                    {
                      key: "messages",
                      color: "#10b981",
                      label: t("analytics.sessions.userMessages"),
                      data: usageTrend.map((point) => ({
                        date: point.date,
                        value: point.user_messages,
                      })),
                    },
                  ]}
                />
              </ChartCard>
            </div>
            <ChartCard
              title={t("analytics.dimensions.byAgent", "按智能体")}
              subtitle={t(
                "analytics.dimensions.byAgentHint",
                "会话数按 agent_id 聚合",
              )}
              icon={<Cpu size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={!isLoading && (sessionsByAgent?.length ?? 0) === 0}
            >
              <PieBlock
                title=""
                data={sessionsByAgent}
                onSliceClick={(entry) =>
                  setDrilldown({
                    kind: "sessions",
                    initialFilters: { agentId: entry.label },
                  })
                }
              />
            </ChartCard>
            <ChartCard
              title={t("analytics.dimensions.byPersona", "按 Persona")}
              subtitle={t(
                "analytics.dimensions.byPersonaHint",
                "会话数按 Persona 聚合",
              )}
              icon={<UserIcon size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={!isLoading && (sessionsByPersona?.length ?? 0) === 0}
            >
              <PieBlock
                title=""
                data={sessionsByPersona}
                onSliceClick={(entry) =>
                  setDrilldown({
                    kind: "sessions",
                    initialFilters: {
                      personaPresetId: entry.id || entry.label,
                    },
                  })
                }
              />
            </ChartCard>
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
              <PieBlock
                title=""
                data={tokensByModel}
                onSliceClick={() => setDrilldown({ kind: "runs" })}
              />
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
              <PieBlock
                title=""
                data={tokensByPreset}
                onSliceClick={() => setDrilldown({ kind: "runs" })}
              />
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

        {/* Usage detail section */}
        <section className="mt-4">
          <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold tracking-wide text-stone-600 uppercase dark:text-stone-400">
                {t("analytics.usage.title")}
              </h2>
              <p className="text-xs text-stone-500 dark:text-stone-400">
                {t("analytics.usage.subtitle")}
              </p>
            </div>
            <button
              type="button"
              onClick={handleUsageExport}
              disabled={isUsageExporting}
              className="flex items-center gap-1 rounded-lg border border-[var(--glass-border)] bg-[var(--glass-bg-subtle)] px-2.5 py-1 text-xs text-stone-700 hover:bg-[var(--glass-bg)] disabled:opacity-50 dark:text-stone-200"
            >
              <Download size={14} aria-hidden />
              <span>
                {isUsageExporting
                  ? t("analytics.usage.exporting")
                  : t("analytics.usage.exportCsv")}
              </span>
            </button>
          </div>

          {usageExportError ? (
            <div className="mb-2 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
              {usageExportError}
            </div>
          ) : null}

          <div className="glass-card rounded-xl p-4">
            {usageError ? (
              <div className="mb-2 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200">
                {usageError}
              </div>
            ) : null}

            {usageLoading ? (
              <PanelLoadingState />
            ) : usageRows.length === 0 ? (
              <div className="py-8 text-center text-sm text-stone-500 dark:text-stone-400">
                {t("analytics.usage.empty")}
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
                    {usageRows.map((row) => (
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
                          {formatDateTime(row.last_active_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="mt-3 flex justify-center">
              <Pagination
                page={usagePage}
                pageSize={USAGE_PAGE_SIZE}
                total={usageTotal}
                onChange={setUsagePage}
              />
            </div>
          </div>
        </section>

        {/* Feedback section */}
        <section className="mt-4">
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-stone-600 uppercase dark:text-stone-400">
            {t("analytics.feedback.title", "反馈")}
          </h2>
          <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatsCard
              icon={ThumbsUp}
              label={t("analytics.feedback.total", "反馈总数")}
              value={feedbackSummary ? formatNumber(feedbackSummary.total) : "—"}
            />
            <StatsCard
              icon={ThumbsUp}
              label={t("analytics.feedback.upCount", "好评数")}
              value={feedbackSummary ? formatNumber(feedbackSummary.up_count) : "—"}
            />
            <StatsCard
              icon={ThumbsDown}
              label={t("analytics.feedback.downCount", "差评数")}
              value={feedbackSummary ? formatNumber(feedbackSummary.down_count) : "—"}
            />
            <StatsCard
              icon={ThumbsUp}
              label={t("analytics.feedback.upRate", "好评率")}
              value={
                feedbackSummary
                  ? `${feedbackSummary.up_percentage.toFixed(1)}%`
                  : "—"
              }
            />
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <ChartCard
              title={t("analytics.feedback.byPreset", "按角色分反馈")}
              subtitle={t(
                "analytics.feedback.byPresetHint",
                "按角色智能体统计好评/差评",
              )}
              icon={<UserIcon size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={!isLoading && (feedbackByPreset?.length ?? 0) === 0}
            >
              <FeedbackByPresetBar
                data={feedbackByPreset}
                onSliceClick={(entry) =>
                  setDrilldown({ kind: "feedback", presetId: entry.preset_id })
                }
              />
            </ChartCard>
            <ChartCard
              title={t("analytics.feedback.reasonDistribution", "点踩原因分布")}
              subtitle={t(
                "analytics.feedback.reasonDistributionHint",
                "差评的 reason 分布",
              )}
              icon={<ThumbsDown size={16} aria-hidden />}
              isLoading={isLoading}
              isEmpty={
                !isLoading &&
                (feedbackSummary?.reason_distribution.length ?? 0) === 0
              }
            >
              <ReasonBar
                data={feedbackSummary?.reason_distribution ?? []}
                onSliceClick={() =>
                  setDrilldown({ kind: "feedback", rating: "down" })
                }
              />
            </ChartCard>
          </div>
        </section>
      </div>

      {drilldown ? (
        <div className="px-4 pb-6 sm:px-6">
          <AnalyticsDrilldownList
            kind={drilldown.kind}
            start={toIso(effectiveRange.start)}
            end={toIso(effectiveRange.end)}
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

export default AnalyticsPanel;
