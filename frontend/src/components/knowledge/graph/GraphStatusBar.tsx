import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2, Play, Power, RefreshCw } from "lucide-react";
import type {
  KBGraphFailedChunk,
  KBGraphStatus,
} from "../../../services/api/knowledgeBase";
import { knowledgeBaseApi } from "../../../services/api/knowledgeBase";
import { DANGER_BTN, NOTICE_WARNING, OUTLINE_BTN } from "../knowledgeStyles";
import { graphCanBuild, graphPhase, graphProgressPercent } from "./graphUtils";

interface GraphStatusBarProps {
  kbId: string;
  status: KBGraphStatus;
  canManage: boolean;
  busy: boolean;
  onBuild: () => void;
  onDisable: () => void;
}

function Stat({ label, value, danger }: { label: string; value: number; danger?: boolean }) {
  return (
    <span
      className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${
        danger
          ? "bg-red-50 text-red-700 dark:bg-red-900/40 dark:text-red-300"
          : "bg-[var(--theme-bg-subtle)] text-[var(--theme-text-secondary)]"
      }`}
    >
      {label} {value}
    </span>
  );
}

export function GraphStatusBar({
  kbId,
  status,
  canManage,
  busy,
  onBuild,
  onDisable,
}: GraphStatusBarProps) {
  const { t } = useTranslation();
  const phase = graphPhase(status);
  const percent = graphProgressPercent(status);
  const [samples, setSamples] = useState<KBGraphFailedChunk[] | null>(null);
  const [samplesLoading, setSamplesLoading] = useState(false);
  const canBuild = graphCanBuild(status);

  const toggleSamples = async () => {
    if (samples) {
      setSamples(null);
      return;
    }
    setSamplesLoading(true);
    try {
      const res = await knowledgeBaseApi.listGraphFailed(kbId, 10);
      setSamples(res.items);
    } catch {
      setSamples([]);
    } finally {
      setSamplesLoading(false);
    }
  };

  const task = status.active_task;
  const buildLabel =
    status.failed > 0
      ? t("knowledge.graph.retryFailed", "重试失败")
      : t("knowledge.graph.continueBuild", "继续构建");

  return (
    <div className="border-b border-[var(--theme-border)] px-4 py-3 sm:px-6">
      <div className="flex flex-wrap items-center gap-2">
        <Stat label={t("knowledge.graph.status.pending", "待构建")} value={status.pending} />
        <Stat label={t("knowledge.graph.status.done", "已完成")} value={status.done} />
        {status.failed > 0 && (
          <button
            type="button"
            onClick={() => void toggleSamples()}
            aria-expanded={samples !== null}
            className="rounded-full bg-red-50 px-2.5 py-0.5 text-[11px] font-medium text-red-700 hover:bg-red-100 dark:bg-red-900/40 dark:text-red-300"
          >
            {t("knowledge.graph.status.failed", "失败")} {status.failed}
          </button>
        )}
        <Stat label={t("knowledge.graph.status.entities", "实体")} value={status.entity_count} />
        <Stat label={t("knowledge.graph.status.relations", "关系")} value={status.relation_count} />
        {phase === "building" && (
          <span className="inline-flex items-center gap-1 text-xs text-[var(--theme-text-secondary)]">
            <Loader2 size={14} className="animate-spin" />
            {t("knowledge.graph.building", "构建中")}
            {task && task.progress.total > 0
              ? ` ${task.progress.done}/${task.progress.total}`
              : ` ${percent}%`}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {canManage && canBuild && (
            <button type="button" disabled={busy} onClick={onBuild} className={OUTLINE_BTN}>
              {status.failed > 0 ? <RefreshCw size={16} /> : <Play size={16} />}
              {buildLabel}
            </button>
          )}
          {canManage && (
            <button type="button" disabled={busy} onClick={onDisable} className={DANGER_BTN}>
              <Power size={16} />
              {t("knowledge.graph.disable", "关闭图谱")}
            </button>
          )}
        </div>
      </div>

      {phase === "building" && (
        <div
          className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--theme-bg-subtle)]"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-[var(--theme-primary)] transition-all"
            style={{ width: `${percent}%` }}
          />
        </div>
      )}

      {status.last_error && (
        <div role="alert" className={`${NOTICE_WARNING} mt-2`}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          {t("knowledge.graph.lastError", {
            message: status.last_error,
            defaultValue: "构建失败：{{message}}",
          })}
        </div>
      )}
      {status.excluded > 0 && (
        <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">
          {t("knowledge.graph.excludedHint", {
            count: status.excluded,
            defaultValue: "{{count}} 个分块超过上限，未参与图谱构建。",
          })}
        </p>
      )}

      {samplesLoading && <Loader2 size={14} className="mt-2 animate-spin" />}
      {samples && (
        <div className="mt-2 rounded-xl border border-red-200 bg-red-50/50 p-3 text-xs dark:border-red-900/60 dark:bg-red-900/10">
          <div className="mb-1 font-medium text-red-700 dark:text-red-300">
            {t("knowledge.graph.failedSamples", "失败分块样例")}
          </div>
          {samples.length === 0 ? (
            <div className="text-[var(--theme-text-secondary)]">
              {t("knowledge.graph.failedEmpty", "暂无样例")}
            </div>
          ) : (
            <ul className="space-y-1.5">
              {samples.map((item) => (
                <li key={item.chunk_id} className="text-[var(--theme-text-secondary)]">
                  <span className="font-medium text-[var(--theme-text)]">{item.filename}</span>
                  {" — "}
                  {item.error}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
