import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { Loader2, X } from "lucide-react";
import {
  knowledgeBaseApi,
  type KnowledgeBase,
} from "../../services/api/knowledgeBase";
import { dispatchKnowledgeBaseRefresh } from "../../utils/knowledgeBaseEvents";
import { GHOST_BTN, PRIMARY_BTN } from "./knowledgeStyles";
import { reportImportResult } from "./reportImportResult";

interface ImportToKnowledgeBaseDialogProps {
  file: { id: string; file_name: string };
  onClose: () => void;
}

export function ImportToKnowledgeBaseDialog({
  file,
  onClose,
}: ImportToKnowledgeBaseDialogProps) {
  const { t } = useTranslation();
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    knowledgeBaseApi
      .list("writable")
      .then((res) => {
        if (cancelled) return;
        setKbs(res.items);
        setSelectedId(res.items[0]?.id ?? null);
      })
      .catch((e) => toast.error(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = async () => {
    if (!selectedId) return;
    setBusy(true);
    try {
      const result = await knowledgeBaseApi.importFiles(selectedId, [file.id]);
      reportImportResult(t, result);
      dispatchKnowledgeBaseRefresh({ kbId: selectedId });
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[1000] flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("knowledge.import.dialogTitle", "转到知识库")}
        className="flex max-h-[85vh] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl dark:bg-stone-900 sm:max-w-md sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-stone-100 px-5 py-4 dark:border-stone-800">
          <h2 className="text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("knowledge.import.dialogTitle", "转到知识库")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
            aria-label={t("common.close", "关闭")}
          >
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
          <p className="truncate text-sm text-stone-600 dark:text-stone-300" title={file.file_name}>
            {t("knowledge.import.fileLabel", {
              name: file.file_name,
              defaultValue: "文件：{{name}}",
            })}
          </p>
          {loading ? (
            <div className="flex justify-center py-6 text-stone-400">
              <Loader2 className="animate-spin" size={20} />
            </div>
          ) : kbs.length === 0 ? (
            <p className="py-6 text-center text-sm text-stone-500 dark:text-stone-400">
              {t("knowledge.import.noWritable", "没有可写入的知识库，请先新建一个。")}
            </p>
          ) : (
            <ul className="space-y-1" role="radiogroup">
              {kbs.map((kb) => (
                <li key={kb.id}>
                  <label className="flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2 hover:bg-stone-50 dark:hover:bg-stone-800">
                    <input
                      type="radio"
                      name="kb-import-target"
                      checked={selectedId === kb.id}
                      onChange={() => setSelectedId(kb.id)}
                    />
                    <span className="min-w-0 flex-1 truncate text-sm text-stone-900 dark:text-stone-100">
                      {kb.name}
                    </span>
                    {kb.my_role !== "owner" && (
                      <span className="scb__status-pill scb__status-pill--inactive">
                        {t(`knowledge.role.${kb.my_role}`, kb.my_role)}
                      </span>
                    )}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-stone-100 px-5 py-4 dark:border-stone-800">
          <button type="button" onClick={onClose} className={GHOST_BTN}>
            {t("common.cancel", "取消")}
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={busy || !selectedId}
            className={PRIMARY_BTN}
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            {t("knowledge.import.confirm", "导入")}
          </button>
        </div>
      </div>
    </div>
  );
}
