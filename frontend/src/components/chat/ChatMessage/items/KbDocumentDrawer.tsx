import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  knowledgeBaseApi,
  type KBDocument,
} from "../../../../services/api/knowledgeBase";
import { DocumentDetailSidebar } from "../../../knowledge/DocumentDetailSidebar";

export interface KbDocumentTarget {
  kbId: string;
  docId: string;
  startLine: number | null;
  endLine: number | null;
}

/** 按需取文档元数据后复用 PR1 文档查看抽屉，定位到 start_line。 */
export function KbDocumentDrawer({
  target,
  onClose,
}: {
  target: KbDocumentTarget;
  onClose: () => void;
}) {
  const [doc, setDoc] = useState<KBDocument | null>(null);

  useEffect(() => {
    let cancelled = false;
    knowledgeBaseApi
      .getDocument(target.kbId, target.docId)
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch((e) => {
        if (cancelled) return;
        toast.error(e instanceof Error ? e.message : String(e));
        onClose();
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅随目标文档重取
  }, [target.kbId, target.docId]);

  if (!doc) return null;
  return (
    <DocumentDetailSidebar
      key={`${doc.id}:${target.startLine ?? 0}`}
      kbId={target.kbId}
      doc={doc}
      focus={
        target.startLine !== null
          ? {
              startLine: target.startLine,
              endLine: target.endLine ?? target.startLine,
            }
          : null
      }
      initialTab="content"
      onClose={onClose}
    />
  );
}
