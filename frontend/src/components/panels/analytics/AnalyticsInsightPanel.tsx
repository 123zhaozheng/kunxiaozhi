/**
 * Analytics Insight Panel — right-hand hard-data insight column.
 *
 * Four conclusions from `/usage/insights` with their click behaviors
 * (PRD 洞察栏四条):
 *   - 活跃高峰: plain text, no click
 *   - Token 大户 Top3: click a user → drill into the usage detail filtered
 *     by that user (parent sets the table search)
 *   - 增长最快 Persona: click → set the top-level persona filter
 *   - 本期新增使用者: click → open the user-list drilldown
 */

import { useTranslation } from "react-i18next";
import { Lightbulb } from "lucide-react";
import type {
  UsageInsightsFastestGrowingPersona,
  UsageInsightsResponse,
  UsageInsightsTopTokenUser,
} from "../../../types/analytics";
import { ChartCard } from "./analyticsPrimitives";
import { WEEKDAY_LABELS, formatNumber } from "./analyticsFormat";

interface FastestGrowingPersonaButtonProps {
  persona: UsageInsightsFastestGrowingPersona;
  linkClass: string;
  onSelect: (personaPresetId: string) => void;
}

function FastestGrowingPersonaButton({
  persona,
  linkClass,
  onSelect,
}: FastestGrowingPersonaButtonProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={() => onSelect(persona.persona_preset_id)}
      title={t("analytics.insights.personaFilterHint", "设为顶部 Persona 筛选")}
      className={`${linkClass} px-1 py-0.5 text-right font-medium text-stone-800 dark:text-stone-100`}
    >
      {`${persona.persona_preset_name || persona.persona_preset_id} ${formatGrowth(persona.growth_pct)}`}
    </button>
  );
}

export interface AnalyticsInsightPanelProps {
  insights: UsageInsightsResponse | null;
  isLoading: boolean;
  /** Set the top-level persona filter to the fastest-growing persona. */
  onPersonaSelect: (personaPresetId: string) => void;
  /** Drill into the usage detail filtered by a token top user. */
  onUserDrilldown: (user: UsageInsightsTopTokenUser) => void;
  /** Drill into the user list (new users of this period). */
  onNewUsersDrilldown: () => void;
}

export function AnalyticsInsightPanel({
  insights,
  isLoading,
  onPersonaSelect,
  onUserDrilldown,
  onNewUsersDrilldown,
}: AnalyticsInsightPanelProps) {
  const { t } = useTranslation();
  const isEmpty =
    !isLoading &&
    (!insights ||
      (!insights.peak &&
        insights.top_token_users.length === 0 &&
        !insights.fastest_growing_persona &&
        insights.new_users === 0));

  const linkClass =
    "rounded-md text-left transition-colors hover:bg-[var(--glass-bg-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]";

  return (
    <ChartCard
      title={t("analytics.insights.title", "洞察")}
      subtitle={t("analytics.insights.subtitle", "区间内的硬数据结论")}
      icon={<Lightbulb size={16} aria-hidden />}
      isLoading={isLoading}
      isEmpty={isEmpty}
      emptyText={t("analytics.insights.empty", "暂无洞察数据")}
    >
      {insights ? (
        <ul className="flex flex-col gap-3 text-sm">
          <li className="flex items-baseline justify-between gap-2">
            <span className="text-stone-500 dark:text-stone-400">
              {t("analytics.insights.peak", "活跃高峰")}
            </span>
            <span className="text-right font-medium text-stone-800 dark:text-stone-100">
              {insights.peak
                ? t("analytics.insights.peakValue", {
                    weekday: t(
                      `analytics.weekdays.${insights.peak.weekday % 7}`,
                      WEEKDAY_LABELS[insights.peak.weekday % 7],
                    ),
                    hour: insights.peak.hour,
                    count: formatNumber(insights.peak.user_messages),
                    defaultValue: "{{weekday}} {{hour}}:00 · {{count}}",
                  })
                : "—"}
            </span>
          </li>
          <li className="flex flex-col gap-1">
            <span className="text-stone-500 dark:text-stone-400">
              {t("analytics.insights.topTokenUsers", "Token 大户 Top3")}
            </span>
            {insights.top_token_users.length === 0 ? (
              <span className="text-stone-400 dark:text-stone-500">—</span>
            ) : (
              insights.top_token_users.map((user) => (
                <button
                  key={user.user_id}
                  type="button"
                  onClick={() => onUserDrilldown(user)}
                  title={t("analytics.insights.userDrillHint", "查看使用明细")}
                  className={`${linkClass} flex items-baseline justify-between gap-2 px-1 py-0.5 text-stone-800 dark:text-stone-100`}
                >
                  <span className="truncate">
                    {user.display_name || user.username || user.user_id}
                  </span>
                  <span className="font-medium">{formatNumber(user.tokens)}</span>
                </button>
              ))
            )}
          </li>
          <li className="flex items-baseline justify-between gap-2">
            <span className="text-stone-500 dark:text-stone-400">
              {t("analytics.insights.fastestGrowingPersona", "增长最快 Persona")}
            </span>
            {insights.fastest_growing_persona ? (
              <FastestGrowingPersonaButton
                persona={insights.fastest_growing_persona}
                linkClass={linkClass}
                onSelect={onPersonaSelect}
              />
            ) : (
              <span className="text-right font-medium text-stone-800 dark:text-stone-100">
                —
              </span>
            )}
          </li>
          <li className="flex items-baseline justify-between gap-2">
            <span className="text-stone-500 dark:text-stone-400">
              {t("analytics.insights.newUsers", "本期新增使用者")}
            </span>
            {insights.new_users > 0 ? (
              <button
                type="button"
                onClick={onNewUsersDrilldown}
                title={t("analytics.insights.newUsersDrillHint", "查看用户列表")}
                className={`${linkClass} px-1 py-0.5 text-right font-medium text-stone-800 dark:text-stone-100`}
              >
                {formatNumber(insights.new_users)}
              </button>
            ) : (
              <span className="text-right font-medium text-stone-800 dark:text-stone-100">
                {formatNumber(insights.new_users)}
              </span>
            )}
          </li>
        </ul>
      ) : null}
    </ChartCard>
  );
}

function formatGrowth(growthPct: number): string {
  const rounded = Math.round(growthPct * 10) / 10;
  return `${rounded >= 0 ? "+" : "-"}${Math.abs(rounded).toFixed(1)}%`;
}
