/**
 * Analytics KPI Row — the six overview cards.
 *
 * Cards: 使用用户 · 会话数 · 用户消息 · 总 Token · 人均消息 · 会话均 Token.
 * Every card carries a sparkline (recharts LineChart with no axes / grid)
 * and a "vs previous period" badge sourced from `summary.previous` — the
 * badge is hidden (never "0%") when the previous period is unavailable.
 *
 * Consistency contract (PRD R3.1): card values are read from the accessors
 * in `analyticsKpi.ts`, the same functions the donut centers use.
 */

import { useTranslation } from "react-i18next";
import { Line, LineChart, ResponsiveContainer } from "recharts";
import { Cpu, Hash, MessageSquare, PieChart, Users } from "lucide-react";
import type {
  TrendDataPoint,
  UsageSummaryResponse,
  UsageTrendPoint,
} from "../../../types/analytics";
import { PIE_COLORS, formatNumber } from "./analyticsFormat";
import {
  formatDeltaPct,
  formatRatio,
  kpiDeltaPct,
  kpiSessionsValue,
  kpiTotalTokensValue,
  kpiUserMessagesValue,
  kpiUsersValue,
  messagesPerUser,
  tokensPerSession,
  type KpiMetric,
} from "./analyticsKpi";

export interface AnalyticsKpiRowProps {
  summary: UsageSummaryResponse | null;
  /** Daily usage trend (sessions / messages / tokens) for the sparklines. */
  usageTrend: UsageTrendPoint[];
  /** Daily using-user counts from `/users/active` for the first card's sparkline. */
  activeTrend: TrendDataPoint[];
  /** A persona/agent filter switches the first card to the using-user metric. */
  isFiltered: boolean;
  /** Shows a skeleton for KPI values during the initial or filter load. */
  isLoading: boolean;
  error?: string | null;
  onUsersDrilldown: () => void;
  onSessionsDrilldown: () => void;
}

interface KpiSparklineProps {
  values: number[];
  color: string;
}

/** Sparkline: recharts LineChart with axes and grid removed (PRD R6). */
function KpiSparkline({ values, color }: KpiSparklineProps) {
  if (values.length === 0) {
    return <div className="h-7" aria-hidden />;
  }
  const data = values.map((value, index) => ({ index, value }));
  return (
    <div className="h-7 w-full" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <Line
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={1.5}
            dot={data.length === 1 ? { r: 3, strokeWidth: 0 } : false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface KpiCardProps {
  icon: React.ElementType;
  label: string;
  value: string;
  /** Delta vs previous period; null hides the badge entirely. */
  delta: number | null;
  /** Interpolated "vs previous period" text (i18n). */
  deltaText: (signedPct: string) => string;
  sparkValues: number[];
  sparkColor: string;
  isLoading?: boolean;
  hint?: string;
  tooltip?: string;
  onClick?: () => void;
}

function KpiCard({
  icon: Icon,
  label,
  value,
  delta,
  deltaText,
  sparkValues,
  sparkColor,
  isLoading = false,
  hint,
  tooltip,
  onClick,
}: KpiCardProps) {
  const className = [
    "glass-card flex w-full flex-col gap-1 rounded-xl p-3 text-left sm:p-4",
    onClick
      ? "cursor-pointer transition-colors hover:bg-[var(--glass-bg-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  const content = (
    <>
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-[var(--glass-bg-subtle)]">
          <Icon size={15} className="text-stone-600 dark:text-stone-400" aria-hidden />
        </div>
        <p className="min-w-0 flex-1 truncate text-xs text-stone-500 dark:text-stone-400">
          {label}
        </p>
      </div>
      <div className="flex items-baseline justify-between gap-2">
        <p
          className="truncate text-lg font-bold text-stone-900 dark:text-stone-100 sm:text-xl"
          aria-busy={isLoading}
        >
          {isLoading ? (
            <span
              className="inline-block h-6 w-20 animate-pulse rounded bg-stone-200 dark:bg-stone-700"
              aria-hidden
            />
          ) : (
            value
          )}
        </p>
        {delta !== null ? (
          <span
            className={`flex-shrink-0 text-[10px] font-medium ${
              delta >= 0
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-500 dark:text-red-400"
            }`}
          >
            {deltaText(formatDeltaPct(delta))}
          </span>
        ) : null}
      </div>
      {hint ? (
        <p className="truncate text-[11px] text-stone-500 dark:text-stone-400">{hint}</p>
      ) : null}
      <KpiSparkline values={sparkValues} color={sparkColor} />
    </>
  );

  if (onClick) {
    return (
      <button type="button" onClick={onClick} title={tooltip} className={className}>
        {content}
      </button>
    );
  }
  return (
    <div title={tooltip} className={className}>
      {content}
    </div>
  );
}

/** Per-day tokens-per-session for the sixth card's sparkline. */
function tokensPerSessionSeries(trend: UsageTrendPoint[]): number[] {
  const values: number[] = [];
  for (const point of trend) {
    if (point.active_sessions > 0) {
      values.push(point.total_tokens / point.active_sessions);
    }
  }
  return values;
}

export function AnalyticsKpiRow({
  summary,
  usageTrend,
  activeTrend,
  isFiltered,
  isLoading,
  error,
  onUsersDrilldown,
  onSessionsDrilldown,
}: AnalyticsKpiRowProps) {
  const { t } = useTranslation();
  // Unfiltered the headline counts logins, so the message-based `/users/active`
  // series would trend a different metric than the number above it.
  const usersSparkline = isFiltered ? activeTrend.map((point) => point.value) : [];

  const valueFor = (metric: KpiMetric): string => {
    switch (metric) {
      case "users": {
        const value = kpiUsersValue(summary, isFiltered);
        return value === null ? "—" : formatNumber(value);
      }
      case "sessions": {
        const value = kpiSessionsValue(summary);
        return value === null ? "—" : formatNumber(value);
      }
      case "userMessages": {
        const value = kpiUserMessagesValue(summary);
        return value === null ? "—" : formatNumber(value);
      }
      case "totalTokens": {
        const value = kpiTotalTokensValue(summary);
        return value === null ? "—" : formatNumber(value);
      }
      case "messagesPerUser":
        return formatRatio(messagesPerUser(summary));
      case "tokensPerSession":
        return formatRatio(tokensPerSession(summary));
    }
  };

  const deltaFor = (metric: KpiMetric): number | null =>
    kpiDeltaPct(summary, metric, metric === "users" ? isFiltered : false);

  const deltaText = (pct: string) =>
    t("analytics.overview.vsPrev", { pct, defaultValue: "较上一区间 {{pct}}" });

  const sparkFor = (metric: KpiMetric): number[] => {
    switch (metric) {
      case "users":
        return usersSparkline;
      case "sessions":
        return usageTrend.map((point) => point.active_sessions);
      case "userMessages":
        return usageTrend.map((point) => point.user_messages);
      case "totalTokens":
        return usageTrend.map((point) => point.total_tokens);
      // Derived cards trend their numerator (per-day denominators are not
      // available for 人均消息; tokens-per-session is derived per day above).
      case "messagesPerUser":
        return usageTrend.map((point) => point.user_messages);
      case "tokensPerSession":
        return tokensPerSessionSeries(usageTrend);
    }
  };

  return (
    <>
      {error ? (
        <p
          role="alert"
          className="mb-2 rounded-lg bg-red-50 p-2 text-xs text-red-700 dark:bg-red-900/30 dark:text-red-200"
        >
          {error}
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      <KpiCard
        icon={Users}
        label={
          isFiltered
            ? t("analytics.overview.usingUsers", "使用用户")
            : t("analytics.overview.activeUsers", "活跃用户")
        }
        value={valueFor("users")}
        delta={deltaFor("users")}
        deltaText={deltaText}
        sparkValues={sparkFor("users")}
        sparkColor={PIE_COLORS[0]}
        isLoading={isLoading}
        hint={
          !isFiltered && summary
            ? t("analytics.overview.usingHint", {
                count: summary.using_users ?? 0,
                defaultValue: "其中使用 {{count}} 人",
              })
            : undefined
        }
        onClick={onUsersDrilldown}
      />
      <KpiCard
        icon={MessageSquare}
        label={t("analytics.sessions.sessions")}
        value={valueFor("sessions")}
        delta={deltaFor("sessions")}
        deltaText={deltaText}
        sparkValues={sparkFor("sessions")}
        sparkColor={PIE_COLORS[4]}
        hint={
          summary
            ? t("analytics.overview.newSessions", {
                count: formatNumber(summary.new_sessions),
                defaultValue: "新建 {{count}}",
              })
            : undefined
        }
        isLoading={isLoading}
        onClick={onSessionsDrilldown}
      />
      <KpiCard
        icon={Hash}
        label={t("analytics.overview.userMessages")}
        value={valueFor("userMessages")}
        delta={deltaFor("userMessages")}
        deltaText={deltaText}
        sparkValues={sparkFor("userMessages")}
        sparkColor={PIE_COLORS[1]}
        isLoading={isLoading}
      />
      <KpiCard
        icon={Cpu}
        label={t("analytics.overview.totalTokens")}
        value={valueFor("totalTokens")}
        delta={deltaFor("totalTokens")}
        deltaText={deltaText}
        sparkValues={sparkFor("totalTokens")}
        sparkColor={PIE_COLORS[3]}
        isLoading={isLoading}
      />
      <KpiCard
        icon={PieChart}
        label={t("analytics.overview.messagesPerUser", "人均消息")}
        value={valueFor("messagesPerUser")}
        delta={deltaFor("messagesPerUser")}
        deltaText={deltaText}
        sparkValues={sparkFor("messagesPerUser")}
        sparkColor={PIE_COLORS[2]}
        isLoading={isLoading}
        tooltip={t(
          "analytics.overview.messagesPerUserHint",
          "分母为发过消息的人数",
        )}
      />
      <KpiCard
        icon={Cpu}
        label={t("analytics.overview.tokensPerSession", "会话均 Token")}
        value={valueFor("tokensPerSession")}
        delta={deltaFor("tokensPerSession")}
        deltaText={deltaText}
        sparkValues={sparkFor("tokensPerSession")}
        sparkColor={PIE_COLORS[5]}
        isLoading={isLoading}
      />
      </div>
    </>
  );
}
