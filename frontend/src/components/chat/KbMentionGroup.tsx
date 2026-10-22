import { LibraryBig } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { KnowledgeBase } from "../../services/api/knowledgeBase";

interface KbMentionGroupProps {
  knowledgeBases: KnowledgeBase[];
  highlightedIndex: number;
  /** 分组之后还有其他候选时，渲染「专家/团队」小标题。 */
  trailingLabel?: string | null;
  onSelect: (kb: KnowledgeBase) => void;
  onHover: (index: number) => void;
  registerItem: (index: number, el: HTMLButtonElement | null) => void;
}

export function KbMentionGroup({
  knowledgeBases,
  highlightedIndex,
  trailingLabel,
  onSelect,
  onHover,
  registerItem,
}: KbMentionGroupProps) {
  const { t } = useTranslation();
  if (knowledgeBases.length === 0) return null;
  return (
    <>
      <div className="px-3 pt-1 pb-0.5 text-[11px] font-medium text-stone-500 dark:text-stone-400">
        {t("knowledge.chat.mentionGroup", "知识库")}
      </div>
      {knowledgeBases.map((kb, index) => {
        const isActive = index === highlightedIndex;
        return (
          <button
            key={kb.id}
            ref={(el) => registerItem(index, el)}
            type="button"
            role="option"
            aria-selected={isActive}
            className={`mention-popup-item ${
              isActive ? "mention-popup-item--active" : ""
            }`}
            onClick={() => onSelect(kb)}
            onMouseEnter={() => onHover(index)}
          >
            <span className="mention-popup-avatar flex items-center justify-center">
              <LibraryBig size={14} />
            </span>
            <div className="mention-popup-text">
              <span className="mention-popup-name">{kb.name}</span>
              <span className="mention-popup-desc">
                {kb.description ||
                  t("knowledge.chat.docCount", {
                    count: kb.stats?.doc_count ?? 0,
                    defaultValue: "{{count}} 个文档",
                  })}
              </span>
            </div>
          </button>
        );
      })}
      {trailingLabel && (
        <div className="px-3 pt-2 pb-0.5 text-[11px] font-medium text-stone-500 dark:text-stone-400">
          {trailingLabel}
        </div>
      )}
    </>
  );
}
