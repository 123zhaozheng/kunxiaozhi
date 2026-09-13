/**
 * Analytics Top Row — agent/persona/model Top5 donuts + feedback overview.
 *
 * Consistency contract (PRD R3.1): each donut's center number is read from
 * the very same accessor as its KPI card in `analyticsKpi.ts`:
 *   - 智能体 Top5 / Persona Top5 centers == sessions KPI (active_sessions)
 *   - 模型 Token Top5 center            == total tokens KPI (total_tokens)
 */

import { useTranslation } from "react-i18next";
import {
  Cpu,
  ThumbsDown,
  ThumbsUp,
  User as UserIcon,
} from "lucide-react";
import type {
  ByLabelItem,
  ByPresetFeedbackItem,
  FeedbackSummaryResponse,
  UsageSummaryResponse,
} from "../../../types/analytics";
import {
  kpiSessionsValue,
  kpiTotalTokensValue,
} from "./analyticsKpi";
import {
  ChartCard,
  DonutBlock,
  FeedbackByPresetBar,
  ReasonBar,
  StatsCard,
} from "./analyticsPrimitives";
import { formatNumber } from "./analyticsFormat";

export interface AnalyticsTopRowProps {
  /** Summary supplies the donut center numbers (same fields as KPI cards). */
  summary: UsageSummaryResponse | null;
  sessionsByAgent: ByLabelItem[];
  sessionsByPersona: ByLabelItem[];
  tokensByModel: ByLabelItem[];
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
  // The session donut centers reuse the sessions KPI accessor verbatim.
  const sessionsCenter = kpiSessionsValue(summary);
  const tokensCenter = kpiTotalTokensValue(summary);

  return (
    <>
      {/* Dimension donuts: agent Top5 / persona Top5 / model token Top5 */}
      <section className="mt-4">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          <ChartCard
            title={t("analytics.dimensions.agentTop5", "按智能体 Top5")}
            subtitle={t(
              "analytics.dimensions.byAgentHint",
              "会话数按 agent_id 聚合",
            )}
            icon={<Cpu size={16} aria-hidden />}
            isLoading={isLoading}
            error={errors.sessionsByAgent}
            isEmpty={!isLoading && (sessionsByAgent?.length ?? 0) === 0}
          >
            <DonutBlock
              data={sessionsByAgent}
              centerValue={sessionsCenter}
              centerCaption={t("analytics.sessions.sessions", "会话数")}
              onSliceClick={onAgentSliceClick}
            />
          </ChartCard>
          <ChartCard
            title={t("analytics.dimensions.personaTop5", "按 Persona Top5")}
            subtitle={t(
              "analytics.dimensions.byPersonaHint",
              "会话数按 Persona 聚合",
            )}
            icon={<UserIcon size={16} aria-hidden />}
            isLoading={isLoading}
            error={errors.sessionsByPersona}
            isEmpty={!isLoading && (sessionsByPersona?.length ?? 0) === 0}
          >
            <DonutBlock
              data={sessionsByPersona}
              centerValue={sessionsCenter}
              centerCaption={t("analytics.sessions.sessions", "会话数")}
              onSliceClick={onPersonaSliceClick}
            />
          </ChartCard>
          <ChartCard
            title={t("analytics.dimensions.modelTokenTop5", "模型 Token Top5")}
            subtitle={t(
              "analytics.tokens.byModelHint",
              "每个模型的总 token 数",
            )}
            icon={<Cpu size={16} aria-hidden />}
            isLoading={isLoading}
            error={errors.tokensByModel}
            isEmpty={!isLoading && (tokensByModel?.length ?? 0) === 0}
          >
            <DonutBlock
              data={tokensByModel}
              centerValue={tokensCenter}
              centerCaption={t("analytics.tokens.total", "总 token")}
              onSliceClick={onModelSliceClick}
            />
          </ChartCard>
        </div>
      </section>

      {/* Feedback overview */}
      <section className="mt-4">
        <h2 className="mb-2 text-sm font-semibold tracking-wide text-stone-600 uppercase dark:text-stone-400">
          {t("analytics.feedback.title", "反馈")}
        </h2>
        {errors.feedbackSummary ? (
          <p
            role="alert"
            className="mb-2 rounded-lg bg-red-50 p-2 text-xs text-red-700 dark:bg-red-900/30 dark:text-red-200"
          >
            {errors.feedbackSummary}
          </p>
        ) : null}
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
            error={errors.feedbackByPreset}
            isEmpty={!isLoading && (feedbackByPreset?.length ?? 0) === 0}
          >
            <FeedbackByPresetBar data={feedbackByPreset} onSliceClick={onFeedbackPresetClick} />
          </ChartCard>
          <ChartCard
            title={t("analytics.feedback.reasonDistribution", "点踩原因分布")}
            subtitle={t(
              "analytics.feedback.reasonDistributionHint",
              "差评的 reason 分布",
            )}
            icon={<ThumbsDown size={16} aria-hidden />}
            isLoading={isLoading}
            error={errors.feedbackSummary}
            isEmpty={
              !isLoading &&
              (feedbackSummary?.reason_distribution.length ?? 0) === 0
            }
          >
            <ReasonBar
              data={feedbackSummary?.reason_distribution ?? []}
              onSliceClick={onReasonClick}
            />
          </ChartCard>
        </div>
      </section>
    </>
  );
}
