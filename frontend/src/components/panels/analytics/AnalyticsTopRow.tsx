/**
 * Analytics Top Row — agent / persona / model ranked bars + feedback overview.
 *
 * Consistency contract (PRD R3.1): each list's share column is taken against
 * the very same accessor as its KPI card in `analyticsKpi.ts`:
 *   - 智能体 / Persona 会话 shares == sessions KPI (active_sessions)
 *   - 模型 Token shares            == total tokens KPI (total_tokens)
 */

import { useTranslation } from "react-i18next";
import { Bot, Cpu, MessageSquareWarning, ThumbsDown, ThumbsUp, User as UserIcon } from "lucide-react";
import type {
  ByLabelItem,
  ByPresetFeedbackItem,
  FeedbackSummaryResponse,
  UsageSummaryResponse,
} from "../../../types/analytics";
import { kpiSessionsValue, kpiTotalTokensValue } from "./analyticsKpi";
import { ChartCard, FeedbackPresetBars, RankedBarList } from "./analyticsPrimitives";
import { STATUS_COLOR, formatNumber } from "./analyticsFormat";

export interface AnalyticsTopRowProps {
  /** Summary supplies the share denominators (same fields as KPI cards). */
  summary: UsageSummaryResponse | null;
  sessionsByAgent: ByLabelItem[];
  sessionsByPersona: ByLabelItem[];
  tokensByModel: ByLabelItem[];
  /** Set while some dates' per-model breakdown is still being filled. */
  tokensByModelPartial?: { partial: boolean; since: string | null };
  feedbackSummary: FeedbackSummaryResponse | null;
  feedbackByPreset: ByPresetFeedbackItem[];
  isLoading: boolean;
  errors?: {
    sessionsByAgent?: string | null;
    sessionsByPersona?: string | null;
    tokensByModel?: string | null;
    feedbackSummary?: string | null;
    feedbackByPreset?: string | null;
  };
  onAgentSliceClick: (entry: ByLabelItem) => void;
  onPersonaSliceClick: (entry: ByLabelItem) => void;
  onModelSliceClick: (entry: ByLabelItem) => void;
  onFeedbackPresetClick: (entry: ByPresetFeedbackItem) => void;
  onReasonClick: () => void;
}

export function AnalyticsTopRow({
  summary,
  sessionsByAgent,
  sessionsByPersona,
  tokensByModel,
  tokensByModelPartial,
  feedbackSummary,
  feedbackByPreset,
  isLoading,
  errors = {},
  onAgentSliceClick,
  onPersonaSliceClick,
  onModelSliceClick,
  onFeedbackPresetClick,
  onReasonClick,
}: AnalyticsTopRowProps) {
  const { t } = useTranslation();
  // Both session lists share the sessions KPI accessor as their denominator.
  const sessionsTotal = kpiSessionsValue(summary);
  const tokensTotal = kpiTotalTokensValue(summary);
  const totalAction = (value: number | null) =>
    value === null ? null : (
      <span className="text-xs tabular-nums text-stone-500 dark:text-stone-400">
        {t("analytics.dimensions.totalLabel", {
          value: formatNumber(value),
          defaultValue: "Total {{value}}",
        })}
      </span>
    );

  return (
    <>
      <section className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
        <ChartCard
          title={t("analytics.dimensions.agentTop5", "智能体会话")}
          subtitle={t("analytics.dimensions.byAgentHint", "活跃会话数，按智能体")}
          icon={<Bot size={16} aria-hidden />}
          action={totalAction(sessionsTotal)}
          isLoading={isLoading}
          error={errors.sessionsByAgent}
          isEmpty={!isLoading && (sessionsByAgent?.length ?? 0) === 0}
        >
          <RankedBarList
            items={sessionsByAgent}
            total={sessionsTotal}
            onSelect={onAgentSliceClick}
          />
        </ChartCard>
        <ChartCard
          title={t("analytics.dimensions.personaTop5", "Persona 会话")}
          subtitle={t("analytics.dimensions.byPersonaHint", "活跃会话数，按 Persona")}
          icon={<UserIcon size={16} aria-hidden />}
          action={totalAction(sessionsTotal)}
          isLoading={isLoading}
          error={errors.sessionsByPersona}
          isEmpty={!isLoading && (sessionsByPersona?.length ?? 0) === 0}
        >
          <RankedBarList
            items={sessionsByPersona}
            total={sessionsTotal}
            onSelect={onPersonaSliceClick}
          />
        </ChartCard>
        <ChartCard
          title={t("analytics.dimensions.modelTokenTop5", "模型 Token")}
          subtitle={t("analytics.tokens.byModelHint", "每个模型的总 token 数")}
          icon={<Cpu size={16} aria-hidden />}
          action={totalAction(tokensTotal)}
          isLoading={isLoading}
          error={errors.tokensByModel}
          isEmpty={!isLoading && (tokensByModel?.length ?? 0) === 0}
        >
          {tokensByModelPartial?.partial ? (
            <p className="mb-2 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-700 dark:bg-amber-900/30 dark:text-amber-200">
              {t(
                "analytics.tokens.partial",
                "部分日期的模型明细正在后台补齐，稍后刷新即可完整显示。",
              )}
            </p>
          ) : null}
          <RankedBarList
            items={tokensByModel}
            total={tokensTotal}
            onSelect={onModelSliceClick}
          />
        </ChartCard>
      </section>

      {/* Feedback overview */}
      <section className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
        <ChartCard
          title={t("analytics.feedback.title", "反馈")}
          icon={<ThumbsUp size={16} aria-hidden />}
          isLoading={isLoading}
          error={errors.feedbackSummary}
          isEmpty={!isLoading && !feedbackSummary}
        >
          {feedbackSummary ? (
            <div className="flex h-full flex-col justify-center gap-4">
              <div>
                <p className="text-xs text-stone-500 dark:text-stone-400">
                  {t("analytics.feedback.upRate", "好评率")}
                </p>
                <p className="text-3xl font-bold tabular-nums text-stone-900 dark:text-stone-100">
                  {feedbackSummary.total > 0
                    ? `${feedbackSummary.up_percentage.toFixed(1)}%`
                    : "—"}
                </p>
              </div>
              {feedbackSummary.total > 0 ? (
                <div className="flex h-1.5 w-full gap-0.5 overflow-hidden rounded-full">
                  {feedbackSummary.up_count > 0 ? (
                    <div
                      className="h-full rounded-full"
                      style={{ flexGrow: feedbackSummary.up_count, backgroundColor: STATUS_COLOR.good }}
                    />
                  ) : null}
                  {feedbackSummary.down_count > 0 ? (
                    <div
                      className="h-full rounded-full"
                      style={{ flexGrow: feedbackSummary.down_count, backgroundColor: STATUS_COLOR.critical }}
                    />
                  ) : null}
                </div>
              ) : null}
              <dl className="grid grid-cols-3 gap-2 text-xs">
                <div>
                  <dt className="text-stone-500 dark:text-stone-400">
                    {t("analytics.feedback.total", "反馈总数")}
                  </dt>
                  <dd className="mt-0.5 text-sm font-semibold tabular-nums text-stone-900 dark:text-stone-100">
                    {formatNumber(feedbackSummary.total)}
                  </dd>
                </div>
                <div>
                  <dt className="flex items-center gap-1 text-stone-500 dark:text-stone-400">
                    <ThumbsUp size={11} aria-hidden />
                    {t("analytics.feedback.upCount", "好评数")}
                  </dt>
                  <dd className="mt-0.5 text-sm font-semibold tabular-nums text-stone-900 dark:text-stone-100">
                    {formatNumber(feedbackSummary.up_count)}
                  </dd>
                </div>
                <div>
                  <dt className="flex items-center gap-1 text-stone-500 dark:text-stone-400">
                    <ThumbsDown size={11} aria-hidden />
                    {t("analytics.feedback.downCount", "差评数")}
                  </dt>
                  <dd className="mt-0.5 text-sm font-semibold tabular-nums text-stone-900 dark:text-stone-100">
                    {formatNumber(feedbackSummary.down_count)}
                  </dd>
                </div>
              </dl>
            </div>
          ) : null}
        </ChartCard>
        <ChartCard
          title={t("analytics.feedback.byPreset", "按 Persona 分反馈")}
          subtitle={t("analytics.feedback.byPresetHint", "按 Persona 统计好评/差评")}
          icon={<UserIcon size={16} aria-hidden />}
          isLoading={isLoading}
          error={errors.feedbackByPreset}
          isEmpty={!isLoading && (feedbackByPreset?.length ?? 0) === 0}
        >
          <FeedbackPresetBars data={feedbackByPreset} onSelect={onFeedbackPresetClick} />
        </ChartCard>
        <ChartCard
          title={t("analytics.feedback.reasonDistribution", "点踩原因分布")}
          subtitle={t("analytics.feedback.reasonDistributionHint", "差评的 reason 分布")}
          icon={<MessageSquareWarning size={16} aria-hidden />}
          isLoading={isLoading}
          error={errors.feedbackSummary}
          isEmpty={
            !isLoading && (feedbackSummary?.reason_distribution.length ?? 0) === 0
          }
          emptyText={t("analytics.feedback.noReasons", "暂无点踩原因数据")}
        >
          <RankedBarList
            items={feedbackSummary?.reason_distribution ?? []}
            total={feedbackSummary?.down_count ?? null}
            limit={6}
            labelFor={(item) => t(`feedback.reason.${item.label}`, item.label)}
            onSelect={onReasonClick}
          />
        </ChartCard>
      </section>
    </>
  );
}
