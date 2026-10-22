import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp, LibraryBig } from "lucide-react";
import type { KbSearchResult } from "./knowledgeBaseResult";
import { KbDocumentDrawer, type KbDocumentTarget } from "./KbDocumentDrawer";
import { formatLineRange, formatScorePercent } from "../../../knowledge/knowledgeUtils";

export function KnowledgeBaseSearchCard({ result }: { result: KbSearchResult }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [target, setTarget] = useState<KbDocumentTarget | null>(null);

  const toggle = (i: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  return (
    <div className="my-2 rounded-2xl border border-stone-200 bg-white p-3 dark:border-stone-700 dark:bg-stone-900">
      <div className="flex items-center gap-2 text-sm text-stone-700 dark:text-stone-200">
        <LibraryBig size={15} className="shrink-0 text-stone-500" />
        <span className="min-w-0 flex-1 truncate">
          {t("knowledge.tools.searchTitle", {
            query: result.query,
            defaultValue: "检索知识库：{{query}}",
          })}
        </span>
        <span className="shrink-0 text-xs text-stone-400">
          {t("knowledge.tools.resultCount", {
            count: result.results.length,
            defaultValue: "{{count}} 条来源",
          })}
        </span>
      </div>
      {result.results.length === 0 && (
        <p className="mt-2 text-xs text-stone-500">
          {t("knowledge.tools.noResults", "没有找到相关内容")}
        </p>
      )}
      <ul className="mt-2 space-y-1.5">
        {result.results.map((hit, i) => {
          const open = expanded.has(i);
          return (
            <li
              key={`${hit.doc_id}:${hit.start_line}:${i}`}
              className="rounded-xl bg-stone-50 px-2.5 py-2 dark:bg-stone-800/60"
            >
              <div className="flex items-center gap-2 text-xs">
                <span className="shrink-0 rounded bg-stone-200 px-1.5 text-stone-600 dark:bg-stone-700 dark:text-stone-300">
                  {hit.ref ?? i + 1}
                </span>
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate text-left font-medium text-blue-600 hover:underline dark:text-blue-400"
                  onClick={() =>
                    setTarget({
                      kbId: hit.kb_id,
                      docId: hit.doc_id,
                      startLine: hit.start_line,
                      endLine: hit.end_line,
                    })
                  }
                >
                  {hit.filename}
                </button>
                <span className="hidden shrink-0 truncate text-stone-400 sm:inline">
                  {hit.kb_name}
                </span>
                {hit.start_line !== null && (
                  <span className="shrink-0 text-stone-400">
                    {t("knowledge.citations.lines", {
                      range: formatLineRange(
                        hit.start_line,
                        hit.end_line ?? hit.start_line,
                      ),
                      defaultValue: "第 {{range}} 行",
                    })}
                  </span>
                )}
                {hit.score !== null && (
                  <span className="shrink-0 text-stone-400">
                    {formatScorePercent(hit.score)}
                  </span>
                )}
                <button
                  type="button"
                  className="shrink-0 text-stone-400 hover:text-stone-600"
                  aria-label={t("knowledge.tools.toggleSnippet", "展开片段")}
                  onClick={() => toggle(i)}
                >
                  {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </button>
              </div>
              {hit.content && (
                <p
                  className={`mt-1 whitespace-pre-wrap break-words text-xs text-stone-600 dark:text-stone-300 ${
                    open ? "" : "line-clamp-2"
                  }`}
                >
                  {hit.content}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {target && (
        <KbDocumentDrawer target={target} onClose={() => setTarget(null)} />
      )}
    </div>
  );
}
