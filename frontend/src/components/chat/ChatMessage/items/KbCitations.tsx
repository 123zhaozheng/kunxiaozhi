import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { LibraryBig } from "lucide-react";
import type { MessagePart } from "../../../../types/message";
import {
  collectKbCitations,
  isKbSearchToolName,
  parseKbSearchResult,
  type KbCitation,
  type KbSearchResult,
} from "./knowledgeBaseResult";
import { KbDocumentDrawer, type KbDocumentTarget } from "./KbDocumentDrawer";
import { formatLineRange } from "../../../knowledge/knowledgeUtils";

export function KbCitations({ citations }: { citations: KbCitation[] }) {
  const { t } = useTranslation();
  const [target, setTarget] = useState<KbDocumentTarget | null>(null);
  if (citations.length === 0) return null;
  return (
    <div className="mt-3 border-t border-stone-200 pt-2 dark:border-stone-700">
      <div className="mb-1 flex items-center gap-1.5 text-xs text-stone-500 dark:text-stone-400">
        <LibraryBig size={13} />
        {t("knowledge.citations.title", "参考来源")}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {citations.map((c) => (
          <button
            key={`${c.kb_id}:${c.doc_id}:${c.start_line ?? ""}`}
            type="button"
            className="inline-flex max-w-full items-center gap-1 rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-700 hover:bg-stone-200 dark:bg-stone-800 dark:text-stone-200 dark:hover:bg-stone-700"
            title={c.kb_name}
            onClick={() =>
              setTarget({
                kbId: c.kb_id,
                docId: c.doc_id,
                startLine: c.start_line,
                endLine: c.end_line,
              })
            }
          >
            {c.ref !== null && (
              <span className="text-stone-400">[{c.ref}]</span>
            )}
            <span className="truncate">{c.filename}</span>
            {c.start_line !== null && (
              <span className="shrink-0 text-stone-400">
                {t("knowledge.citations.lines", {
                  range: formatLineRange(
                    c.start_line,
                    c.end_line ?? c.start_line,
                  ),
                  defaultValue: "第 {{range}} 行",
                })}
              </span>
            )}
          </button>
        ))}
      </div>
      {target && (
        <KbDocumentDrawer target={target} onClose={() => setTarget(null)} />
      )}
    </div>
  );
}

/** 消息级「参考来源」：汇总本条消息内所有 kb_search 结果。 */
export function MessageKbCitations({
  parts,
  isStreaming,
}: {
  parts: readonly MessagePart[] | undefined;
  isStreaming?: boolean;
}) {
  const citations = useMemo(() => {
    if (!parts || isStreaming) return [];
    const searches: KbSearchResult[] = [];
    let body = "";
    for (const part of parts) {
      if (part.type === "text") {
        body += `${part.content}\n`;
      } else if (
        part.type === "tool" &&
        isKbSearchToolName(part.name) &&
        !part.isPending &&
        part.success !== false
      ) {
        const parsed = parseKbSearchResult(
          part.result as string | Record<string, unknown> | undefined,
        );
        if (parsed) searches.push(parsed);
      }
    }
    return collectKbCitations(searches, body);
  }, [parts, isStreaming]);
  return <KbCitations citations={citations} />;
}
