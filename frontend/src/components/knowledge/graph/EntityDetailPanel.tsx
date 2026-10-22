import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";
import {
  knowledgeBaseApi,
  type KBGraphEntityDetail,
  type KBGraphMention,
} from "../../../services/api/knowledgeBase";
import { labelColor } from "./graphMapping";

interface EntityDetailPanelProps {
  kbId: string;
  entityId: string;
  onClose: () => void;
  onSelectEntity: (id: string) => void;
  onOpenMention: (mention: KBGraphMention) => void;
}

export function EntityDetailPanel({
  kbId,
  entityId,
  onClose,
  onSelectEntity,
  onOpenMention,
}: EntityDetailPanelProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<KBGraphEntityDetail | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(false);
    knowledgeBaseApi
      .getGraphEntity(kbId, entityId)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [kbId, entityId]);

  const entity = detail?.entity;
  const attributes = entity ? Object.entries(entity.attributes) : [];
  const sectionCls = "mt-4 text-xs font-semibold text-[var(--theme-text-secondary)]";

  return (
    <aside
      className="flex h-full w-80 shrink-0 flex-col overflow-y-auto border-l border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-4"
      aria-label={t("knowledge.graph.entity.title", "实体详情")}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-base font-semibold text-[var(--theme-text)]">
            {entity?.name ?? "…"}
          </div>
          {entity && (
            <span
              className="mt-1 inline-block rounded-full px-2 py-0.5 text-[11px] font-medium text-white"
              style={{ backgroundColor: labelColor(entity.label) }}
            >
              {entity.label}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("knowledge.graph.entity.close", "关闭详情")}
          className="rounded-lg p-1 text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)]"
        >
          <X size={16} />
        </button>
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-600">
          {t("knowledge.graph.entity.loadFailed", "实体详情加载失败")}
        </p>
      )}
      {!detail && !error && <Loader2 size={16} className="mt-4 animate-spin" />}

      {detail && entity && (
        <>
          <div className="mt-2 text-xs text-[var(--theme-text-tertiary)]">
            {t("knowledge.graph.entity.mentionCount", {
              count: entity.mention_count,
              defaultValue: "出现在 {{count}} 个分块中",
            })}
          </div>

          {attributes.length > 0 && (
            <>
              <div className={sectionCls}>{t("knowledge.graph.entity.attributes", "属性")}</div>
              <dl className="mt-1 space-y-1 text-sm">
                {attributes.map(([key, value]) => (
                  <div key={key} className="flex gap-2">
                    <dt className="shrink-0 text-[var(--theme-text-secondary)]">{key}</dt>
                    <dd className="min-w-0 break-words text-[var(--theme-text)]">{value}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}

          <div className={sectionCls}>
            {t("knowledge.graph.entity.relations", "关系")} ({detail.relations.length}/
            {detail.relations_total})
          </div>
          <ul className="mt-1 space-y-1">
            {detail.relations.map((rel) => {
              const outgoing = rel.source_id === entity.id;
              const otherId = outgoing ? rel.target_id : rel.source_id;
              const otherName = outgoing ? rel.target_name : rel.source_name;
              return (
                <li key={rel.id} className="text-sm">
                  <span className="text-[var(--theme-text-secondary)]">
                    {outgoing ? "→" : "←"} {rel.label}
                  </span>{" "}
                  <button
                    type="button"
                    onClick={() => onSelectEntity(otherId)}
                    className="text-[var(--theme-primary)] hover:underline"
                  >
                    {otherName || otherId}
                  </button>
                </li>
              );
            })}
          </ul>

          <div className={sectionCls}>
            {t("knowledge.graph.entity.mentions", "提及片段")} ({detail.mentions.length}/
            {detail.mentions_total})
          </div>
          <ul className="mt-1 space-y-2">
            {detail.mentions.map((mention) => (
              <li key={mention.chunk_id}>
                <button
                  type="button"
                  onClick={() => onOpenMention(mention)}
                  className="block w-full rounded-lg border border-[var(--theme-border)] p-2 text-left text-xs hover:bg-[var(--theme-sidebar-hover)]"
                >
                  <div className="truncate font-medium text-[var(--theme-text)]">
                    {mention.filename}
                  </div>
                  <p className="mt-1 line-clamp-3 break-words text-[var(--theme-text-secondary)]">
                    {mention.snippet}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}
