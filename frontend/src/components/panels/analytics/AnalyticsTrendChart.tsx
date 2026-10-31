/**
 * Analytics Trend Chart — one metric at a time, one axis.
 *
 * Messages, sessions, users and tokens differ by orders of magnitude, so they
 * are never plotted against two y-scales; a segmented switcher picks the
 * series instead. A single-day range plots the 24 UTC+8 hours (only messages
 * and tokens have an hourly breakdown); longer ranges plot days.
 */

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import type {
  TrendDataPoint,
  UsageHourPoint,
  UsageTrendPoint,
} from "../../../types/analytics";
import { ChartCard, TrendArea, type TrendPoint } from "./analyticsPrimitives";
import { daysInRange, type AnalyticsDateRange } from "./analyticsDates";

type TrendMetric = "messages" | "sessions" | "users" | "tokens";

const HOURLY_METRICS: TrendMetric[] = ["messages", "tokens"];
const DAILY_METRICS: TrendMetric[] = ["messages", "sessions", "users", "tokens"];

export interface AnalyticsTrendChartProps {
  usageTrend: UsageTrendPoint[];
  activeTrend: TrendDataPoint[];
  /** 24 hourly points when the range is a single day; empty otherwise. */
  hourly: UsageHourPoint[];
  /** Requested range; days without activity are plotted as zero. */
  range: AnalyticsDateRange;
  isLoading: boolean;
  error?: string | null;
}

function formatDayTick(key: string): string {
  const parts = key.split("-");
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : key;
}

/** Token counts keep full precision on tooltips (no K/M rounding). */
function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return Math.round(value).toLocaleString();
}

export function AnalyticsTrendChart({
  usageTrend,
  activeTrend,
  hourly,
  range,
  isLoading,
  error,
}: AnalyticsTrendChartProps) {
  const { t } = useTranslation();
  const [metric, setMetric] = useState<TrendMetric>("messages");
  const isHourly = hourly.length > 0;
  const metrics = isHourly ? HOURLY_METRICS : DAILY_METRICS;
  const activeMetric = metrics.includes(metric) ? metric : "messages";

  const labels: Record<TrendMetric, string> = {
    messages: t("analytics.overview.userMessages"),
    sessions: t("analytics.sessions.sessions", "会话数"),
    // `/users/active` counts users who sent messages, filtered or not.
    users: t("analytics.overview.usingUsers", "使用用户"),
    tokens: t("analytics.tokens.total", "总 token"),
  };

  const points = useMemo<TrendPoint[]>(() => {
    if (isHourly) {
      return hourly.map((point) => ({
        key: `${String(point.hour).padStart(2, "0")}:00`,
        value: activeMetric === "tokens" ? point.total_tokens : point.user_messages,
      }));
    }
    const byDay = new Map<string, number>(
      activeMetric === "users"
        ? activeTrend.map((point) => [point.date, point.value])
        : usageTrend.map((point) => [
            point.date,
            activeMetric === "tokens"
              ? point.total_tokens
              : activeMetric === "sessions"
                ? point.active_sessions
                : point.user_messages,
          ]),
    );
    return daysInRange(range).map((day) => ({ key: day, value: byDay.get(day) ?? 0 }));
  }, [isHourly, hourly, activeTrend, usageTrend, activeMetric, range]);

  const isEmpty =
    !isLoading && (points.length === 0 || points.every((point) => point.value === 0));
  const tokensUnit = t("analytics.tokens.unit", "tokens");

  const switcher = (
    <div
      className="inline-flex items-center gap-0.5 rounded-lg bg-[var(--glass-bg-subtle)] p-0.5"
      role="tablist"
      aria-label={t("analytics.trend.title", "核心趋势")}
    >
      {metrics.map((key) => {
        const active = key === activeMetric;
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => setMetric(key)}
            className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
              active
                ? "bg-[var(--theme-bg-card)] text-stone-900 shadow-sm dark:text-stone-100"
                : "text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-stone-200"
            }`}
          >
            {labels[key]}
          </button>
        );
      })}
    </div>
  );

  return (
    <ChartCard
      title={t("analytics.trend.title", "核心趋势")}
      subtitle={
        isHourly
          ? t("analytics.trend.hourlyHint", "按小时（UTC+8）")
          : t("analytics.trend.dailyHint", "按天（UTC+8）")
      }
      icon={<TrendingUp size={16} aria-hidden />}
      action={switcher}
      isLoading={isLoading}
      error={error}
      isEmpty={isEmpty}
      className="h-full min-h-[320px]"
    >
      <TrendArea
        points={points}
        label={labels[activeMetric]}
        formatTick={isHourly ? (key) => key : formatDayTick}
        formatValue={
          activeMetric === "tokens"
            ? (value) => `${formatTokens(value)} ${tokensUnit}`
            : undefined
        }
      />
    </ChartCard>
  );
}
