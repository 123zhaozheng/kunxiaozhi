/**
 * Analytics Trend Chart — core multi-metric trend (daily granularity).
 *
 * Sessions / using users / user messages share the left axis; tokens use
 * the right axis. Sessions and tokens come from `/usage/trend`, using users
 * from `/users/active` — all requested with the identical filter
 * batch by AnalyticsPanel.
 */

import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import type { TrendDataPoint, UsageTrendPoint } from "../../../types/analytics";
import { PIE_COLORS } from "./analyticsFormat";
import { ChartCard, LineTrend } from "./analyticsPrimitives";

export interface AnalyticsTrendChartProps {
  usageTrend: UsageTrendPoint[];
  activeTrend: TrendDataPoint[];
  isLoading: boolean;
  error?: string | null;
}

export function AnalyticsTrendChart({
  usageTrend,
  activeTrend,
  isLoading,
  error,
}: AnalyticsTrendChartProps) {
  const { t } = useTranslation();
  const tokensUnit = t("analytics.tokens.unit", "tokens");
  const isEmpty =
    !isLoading && usageTrend.length === 0 && activeTrend.length === 0;

  return (
    <ChartCard
      title={t("analytics.trend.title", "核心趋势")}
      subtitle={t(
        "analytics.trend.hint",
        "会话、活跃用户、用户消息按天对比，Token 见右轴",
      )}
      icon={<TrendingUp size={16} aria-hidden />}
      isLoading={isLoading}
      error={error}
      isEmpty={isEmpty}
    >
      <LineTrend
        series={[
          {
            key: "sessions",
            color: PIE_COLORS[4],
            label: t("analytics.sessions.sessions", "会话数"),
            data: usageTrend.map((point) => ({
              date: point.date,
              value: point.active_sessions,
            })),
          },
          {
            key: "activeUsers",
            color: PIE_COLORS[0],
            label: t("analytics.overview.usingUsers", "使用用户"),
            data: activeTrend,
          },
          {
            key: "messages",
            color: PIE_COLORS[1],
            label: t("analytics.sessions.userMessages"),
            data: usageTrend.map((point) => ({
              date: point.date,
              value: point.user_messages,
            })),
          },
          {
            key: "tokens",
            color: PIE_COLORS[2],
            label: t("analytics.tokens.total", "总 token"),
            yAxis: "right",
            valueFormatter: (value) => `${formatTokens(value)} ${tokensUnit}`,
            data: usageTrend.map((point) => ({
              date: point.date,
              value: point.total_tokens,
            })),
          },
        ]}
      />
    </ChartCard>
  );
}

/** Token counts keep full precision on tooltips (no K/M rounding). */
function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return Math.round(value).toLocaleString();
}
