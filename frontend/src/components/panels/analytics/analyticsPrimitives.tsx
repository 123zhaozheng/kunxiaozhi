/**
 * Analytics shared primitives — card shell, ranked bars, feedback bars, trend.
 *
 * Forms follow the dataviz method: breakdowns are ranked horizontal bars in
 * one hue (long model / persona names stay readable, close values stay
 * comparable), trends are one series on one axis with a crosshair tooltip.
 */

import { useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { PanelLoadingState } from "../../common/PanelLoadingState";
import type { ByLabelItem, ByPresetFeedbackItem } from "../../../types/analytics";
import {
  STATUS_COLOR,
  formatNumber,
  formatPercent,
  rankRows,
} from "./analyticsFormat";
import { useSeriesColor } from "./useSeriesColor";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]";

export function ChartCard({
  title,
  subtitle,
  icon,
  action,
  isLoading,
  error,
  isEmpty,
  emptyText,
  className = "min-h-[240px]",
  children,
}: {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  /** Right-aligned header slot (metric switcher, total). */
  action?: React.ReactNode;
  isLoading?: boolean;
  error?: string | null;
  isEmpty?: boolean;
  emptyText?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className={`glass-card flex flex-col rounded-xl p-4 sm:p-5 ${className}`}>
      <div className="mb-3 flex items-start gap-2">
        {icon ? (
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-[var(--glass-bg-subtle)] text-stone-600 dark:text-stone-300">
            {icon}
          </div>
        ) : null}
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-stone-900 dark:text-stone-100">
            {title}
          </h3>
          {subtitle ? (
            <p className="truncate text-xs text-stone-500 dark:text-stone-400">
              {subtitle}
            </p>
          ) : null}
        </div>
        {action ? <div className="flex-shrink-0">{action}</div> : null}
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
          <div className="flex h-full min-h-[120px] items-center justify-center text-sm text-stone-500 dark:text-stone-400">
            {emptyText ?? t("analytics.empty", "No data")}
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

interface RankedBarListProps {
  items: ByLabelItem[];
  /**
   * Denominator for the share column. Must come from the same accessor as
   * the matching KPI card (consistency contract, see analyticsKpi.ts).
   */
  total: number | null;
  limit?: number;
  formatValue?: (value: number) => string;
  /** Display label override (e.g. translating reason codes). */
  labelFor?: (item: ByLabelItem) => string;
  onSelect?: (item: ByLabelItem) => void;
}

/** Ranked horizontal bars: label + value + share, one hue, largest first. */
export function RankedBarList({
  items,
  total,
  limit = 8,
  formatValue = formatNumber,
  labelFor,
  onSelect,
}: RankedBarListProps) {
  const { t } = useTranslation();
  const color = useSeriesColor();
  const { rows, rest } = useMemo(() => rankRows(items, total, limit), [items, total, limit]);

  return (
    <div>
      <ul className="flex flex-col gap-1">
        {rows.map(({ item, share, widthPct }) => {
          const label = labelFor ? labelFor(item) : item.label;
          const valueText = formatValue(item.value);
          const shareText = share === null ? null : formatPercent(share);
          const content = (
            <>
              <div className="flex items-baseline justify-between gap-3 text-xs">
                <span className="min-w-0 truncate text-stone-700 dark:text-stone-200">
                  {label}
                </span>
                <span className="flex-shrink-0 font-medium tabular-nums text-stone-900 dark:text-stone-100">
                  {valueText}
                  {shareText ? (
                    <span className="ml-1.5 inline-block w-11 text-right font-normal text-stone-500 dark:text-stone-400">
                      {shareText}
                    </span>
                  ) : null}
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-[var(--glass-bg-subtle)]">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${widthPct}%`, backgroundColor: color }}
                />
              </div>
            </>
          );
          const tooltip = [label, valueText, shareText].filter(Boolean).join(" · ");
          return (
            <li key={item.id ?? item.label}>
              {onSelect ? (
                <button
                  type="button"
                  onClick={() => onSelect(item)}
                  title={tooltip}
                  className={`w-full rounded-md px-1.5 py-1.5 text-left transition-colors hover:bg-[var(--glass-bg-subtle)] ${FOCUS_RING}`}
                >
                  {content}
                </button>
              ) : (
                <div title={tooltip} className="px-1.5 py-1.5">
                  {content}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {rest ? (
        <p className="mt-1.5 px-1.5 text-[11px] text-stone-500 dark:text-stone-400">
          {t("analytics.dimensions.more", { count: rest.count, defaultValue: "{{count}} more" })}
          {" · "}
          {formatValue(rest.value)}
        </p>
      ) : null}
    </div>
  );
}

interface FeedbackPresetBarsProps {
  data: ByPresetFeedbackItem[];
  limit?: number;
  onSelect?: (entry: ByPresetFeedbackItem) => void;
}

/** Per-persona up/down split: one stacked bar per persona, labelled counts. */
export function FeedbackPresetBars({ data, limit = 6, onSelect }: FeedbackPresetBarsProps) {
  const { t } = useTranslation();
  const rows = useMemo(
    () => [...(data ?? [])].sort((a, b) => b.total - a.total).slice(0, limit),
    [data, limit],
  );
  const max = rows[0]?.total ?? 0;

  return (
    <ul className="flex flex-col gap-1">
      {rows.map((entry) => {
        const name = entry.preset_name || entry.preset_id;
        const width = max > 0 ? Math.max((entry.total / max) * 100, 2) : 0;
        const content = (
          <>
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="min-w-0 truncate text-stone-700 dark:text-stone-200">{name}</span>
              <span className="flex flex-shrink-0 items-center gap-2 tabular-nums text-stone-600 dark:text-stone-300">
                <span className="inline-flex items-center gap-0.5">
                  <ThumbsUp size={11} aria-hidden />
                  {formatNumber(entry.up_count)}
                </span>
                <span className="inline-flex items-center gap-0.5">
                  <ThumbsDown size={11} aria-hidden />
                  {formatNumber(entry.down_count)}
                </span>
              </span>
            </div>
            <div className="mt-1 flex h-1.5 w-full overflow-hidden rounded-full bg-[var(--glass-bg-subtle)]">
              <div className="flex h-full gap-0.5" style={{ width: `${width}%` }}>
                {entry.up_count > 0 ? (
                  <div
                    className="h-full rounded-full"
                    style={{ flexGrow: entry.up_count, backgroundColor: STATUS_COLOR.good }}
                  />
                ) : null}
                {entry.down_count > 0 ? (
                  <div
                    className="h-full rounded-full"
                    style={{ flexGrow: entry.down_count, backgroundColor: STATUS_COLOR.critical }}
                  />
                ) : null}
              </div>
            </div>
          </>
        );
        const tooltip = `${name} · ${t("analytics.feedback.up", "好评")} ${entry.up_count} · ${t(
          "analytics.feedback.down",
          "差评",
        )} ${entry.down_count}`;
        return (
          <li key={entry.preset_id}>
            {onSelect ? (
              <button
                type="button"
                onClick={() => onSelect(entry)}
                title={tooltip}
                className={`w-full rounded-md px-1.5 py-1.5 text-left transition-colors hover:bg-[var(--glass-bg-subtle)] ${FOCUS_RING}`}
              >
                {content}
              </button>
            ) : (
              <div title={tooltip} className="px-1.5 py-1.5">
                {content}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export interface TrendPoint {
  /** X-axis key: `YYYY-MM-DD` or `HH:00`. */
  key: string;
  value: number;
}

interface TrendAreaProps {
  points: TrendPoint[];
  label: string;
  formatValue?: (value: number) => string;
  formatTick?: (key: string) => string;
}

/** One series on one axis, crosshair tooltip, light area fill. */
export function TrendArea({
  points,
  label,
  formatValue = formatNumber,
  formatTick = (key) => key,
}: TrendAreaProps) {
  const color = useSeriesColor();
  const gradientId = useId().replace(/:/g, "");
  if (points.length === 0) return null;

  return (
    <div className="h-60" role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.22} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="rgba(120,113,108,0.18)" />
          <XAxis
            dataKey="key"
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={formatTick}
            tickLine={false}
            axisLine={false}
            minTickGap={16}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={(value: number) => formatNumber(Number(value))}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
            width={44}
          />
          <Tooltip
            cursor={{ stroke: "rgba(120,113,108,0.45)", strokeWidth: 1 }}
            formatter={(value: number) => [formatValue(Number(value)), label]}
            labelFormatter={(key) => formatTick(String(key ?? ""))}
          />
          <Area
            type="monotone"
            dataKey="value"
            name={label}
            stroke={color}
            strokeWidth={2}
            fill={`url(#${gradientId})`}
            dot={points.length === 1 ? { r: 4, fill: color, strokeWidth: 0 } : false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--theme-bg-card, #fff)" }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
