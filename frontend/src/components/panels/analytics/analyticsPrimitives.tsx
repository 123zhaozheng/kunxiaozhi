/**
 * Analytics shared primitives — cards, chart shells and formatters.
 *
 * Ported unchanged from the former monolithic `AnalyticsPanel.tsx` so the
 * dashboard blocks keep identical visuals while being split into sections.
 * Colors stay on existing constants / theme tokens; S3/S4 will restyle.
 */

import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
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
import { PanelLoadingState } from "../../common/PanelLoadingState";
import type {
  ByLabelItem,
  ByPresetFeedbackItem,
  TrendDataPoint,
} from "../../../types/analytics";
import { PIE_COLORS, formatDonutLegendLabel, formatNumber } from "./analyticsFormat";

export function StatsCard({
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

export function ChartCard({
  title,
  subtitle,
  icon,
  isLoading,
  error,
  isEmpty,
  emptyText,
  children,
}: {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  isLoading?: boolean;
  error?: string | null;
  isEmpty?: boolean;
  emptyText?: string;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
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
        ) : error ? (
          <div
            role="alert"
            className="flex h-full items-center justify-center rounded-lg bg-red-50 p-3 text-center text-sm text-red-700 dark:bg-red-900/30 dark:text-red-200"
          >
            {error}
          </div>
        ) : isEmpty ? (
          <div className="flex h-full items-center justify-center text-sm text-stone-500 dark:text-stone-400">
            {emptyText ?? t("analytics.empty", "No data")}
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

interface DonutBlockProps {
  /** Top entries (already sorted by value desc; sliced to 5 internally). */
  data: ByLabelItem[];
  /**
   * Number rendered in the donut hole. Must come from the same accessor as
   * the matching KPI card (consistency contract, see analyticsKpi.ts).
   */
  centerValue: number | null;
  /** Small caption under the center number (e.g. 会话数 / 总 token). */
  centerCaption?: string;
  unitFormatter?: (value: number) => string;
  onSliceClick?: (entry: ByLabelItem) => void;
}

/** Top5 donut: ring slices for the top five entries, KPI field in the hole. */
export function DonutBlock({
  data,
  centerValue,
  centerCaption,
  unitFormatter,
  onSliceClick,
}: DonutBlockProps) {
  const top = useMemo(() => (data ?? []).slice(0, 5), [data]);
  const renderLabel = useCallback(
    // recharts passes (name, entry, index); `name` is the nameKey string.
    (name: unknown) => formatDonutLegendLabel(name, top, unitFormatter),
    [top, unitFormatter],
  );
  const format = (value: number) =>
    unitFormatter ? unitFormatter(value) : formatNumber(value);

  if (top.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-stone-500 dark:text-stone-400">
        —
      </div>
    );
  }

  return (
    <div
      className="relative h-48"
      role="img"
      aria-label={
        centerCaption
          ? `${centerCaption}: ${centerValue === null ? "—" : format(centerValue)}`
          : undefined
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={top}
            dataKey="value"
            nameKey="label"
            cx="50%"
            cy="50%"
            outerRadius={70}
            innerRadius={46}
            paddingAngle={1}
            onClick={
              onSliceClick ? (entry) => onSliceClick(entry as ByLabelItem) : undefined
            }
          >
            {top.map((entry, index) => (
              <Cell
                key={entry.label}
                fill={PIE_COLORS[index % PIE_COLORS.length]}
                cursor={onSliceClick ? "pointer" : undefined}
              />
            ))}
          </Pie>
          <Tooltip
            formatter={(value: number, _name, props) => [
              format(value),
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
      {/* Center number: the shared KPI field (pointer-events off so slices stay clickable). */}
      <div className="pointer-events-none absolute inset-y-0 left-0 flex w-1/2 flex-col items-center justify-center">
        <span className="text-lg font-bold text-stone-900 dark:text-stone-100">
          {centerValue === null ? "—" : format(centerValue)}
        </span>
        {centerCaption ? (
          <span className="text-[10px] text-stone-500 dark:text-stone-400">
            {centerCaption}
          </span>
        ) : null}
      </div>
    </div>
  );
}

interface FeedbackByPresetBarProps {
  data: ByPresetFeedbackItem[];
  onSliceClick?: (entry: ByPresetFeedbackItem) => void;
}

export function FeedbackByPresetBar({ data, onSliceClick }: FeedbackByPresetBarProps) {
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
    <div
      className="h-56"
      role="img"
      aria-label={t("analytics.feedback.byPreset", "按角色分反馈")}
    >
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

export function ReasonBar({ data, onSliceClick }: ReasonBarProps) {
  const { t } = useTranslation();
  if ((data?.length ?? 0) === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-stone-500 dark:text-stone-400">
        {t("analytics.feedback.noReasons", "暂无点踩原因数据")}
      </div>
    );
  }
  return (
    <div
      className="h-56"
      role="img"
      aria-label={t("analytics.feedback.reasonDistribution", "点踩原因分布")}
    >
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

export interface LineTrendProps {
  series: Array<{
    key: string;
    color: string;
    label: string;
    data: TrendDataPoint[];
    /** Plot against the right-hand axis (default left). */
    yAxis?: "left" | "right";
    /** Per-series tooltip formatting (falls back to the shared formatter). */
    valueFormatter?: (value: number) => string;
  }>;
  valueFormatter?: (value: number) => string;
}

export function LineTrend({ series, valueFormatter }: LineTrendProps) {
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
  const hasRightAxis = (series ?? []).some((s) => s.yAxis === "right");
  const formatFor = (s: (typeof series)[number]) =>
    s.valueFormatter ?? valueFormatter ?? ((value: number) => formatNumber(value));

  return (
    <div
      className="h-56"
      role="img"
      aria-label={series.map((item) => item.label).join(", ")}
    >
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
            yAxisId="left"
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={(value: number) => formatNumber(Number(value))}
            width={40}
          />
          {hasRightAxis ? (
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 11, fill: "currentColor" }}
              tickFormatter={(value: number) => formatNumber(Number(value))}
              width={44}
            />
          ) : null}
          <Tooltip
            formatter={(value: number, name: string) => {
              const match = (series ?? []).find((s) => s.label === name);
              const format = match ? formatFor(match) : formatNumber;
              return [format(value), name];
            }}
            labelFormatter={(label) => String(label ?? "")}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {series.map((s) => (
            <Line
              key={s.key}
              yAxisId={s.yAxis === "right" ? "right" : "left"}
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
                      const text = formatFor(s)(v);
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
