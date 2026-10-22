import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { Loader2, Search } from "lucide-react";
import {
  knowledgeBaseApi,
  type KBImportableFile,
} from "../../services/api/knowledgeBase";
import { Checkbox } from "../common/Checkbox";
import { Pagination } from "../common/Pagination";
import { formatFileSize } from "../documents/utils/fileSize";
import { INPUT_CLS, PRIMARY_BTN } from "./knowledgeStyles";
import { IMPORT_MAX_FILES, IMPORT_PAGE_SIZE } from "./knowledgeUtils";
import { reportImportResult } from "./reportImportResult";

interface LibraryImportPanelProps {
  kbId: string;
  onImported: () => void;
}

export function LibraryImportPanel({ kbId, onImported }: LibraryImportPanelProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState(1);
  const [files, setFiles] = useState<KBImportableFile[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Map<string, KBImportableFile>>(new Map());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebounced(query.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await knowledgeBaseApi.listImportableFiles({
        q: debounced,
        page,
        page_size: IMPORT_PAGE_SIZE,
      });
      setFiles(res.items);
      setTotal(res.total);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [debounced, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (file: KBImportableFile) => {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(file.id)) next.delete(file.id);
      else if (next.size < IMPORT_MAX_FILES) next.set(file.id, file);
      else
        toast.error(
          t("knowledge.import.tooMany", {
            max: IMPORT_MAX_FILES,
            defaultValue: "一次最多导入 {{max}} 个文件",
          }),
        );
      return next;
    });
  };

  const runImport = async () => {
    setBusy(true);
    try {
      const result = await knowledgeBaseApi.importFiles(kbId, [...selected.keys()]);
      reportImportResult(t, result);
      setSelected(new Map());
      onImported();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400"
        />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("knowledge.import.searchPlaceholder", "搜索文件库中的文件")}
          className={`${INPUT_CLS} pl-9`}
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-8 text-stone-400">
          <Loader2 className="animate-spin" size={20} />
        </div>
      ) : files.length === 0 ? (
        <p className="py-8 text-center text-sm text-stone-500 dark:text-stone-400">
          {t("knowledge.import.empty", "文件库中没有可导入的文件")}
        </p>
      ) : (
        <ul className="divide-y divide-[var(--theme-border)] rounded-xl border border-[var(--theme-border)]">
          {files.map((file) => (
            <li key={file.id}>
              <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-[var(--theme-bg-subtle)]">
                <Checkbox
                  size="sm"
                  checked={selected.has(file.id)}
                  onChange={() => toggle(file)}
                />
                <span className="min-w-0 flex-1">
                  <span
                    className="block truncate text-sm text-[var(--theme-text)]"
                    title={file.file_name}
                  >
                    {file.file_name}
                  </span>
                  <span className="block truncate text-[11px] text-[var(--theme-text-tertiary)]">
                    {formatFileSize(file.file_size)}
                  </span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}

      {total > IMPORT_PAGE_SIZE && (
        <Pagination page={page} pageSize={IMPORT_PAGE_SIZE} total={total} onChange={setPage} />
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-stone-500 dark:text-stone-400">
          {t("knowledge.import.selected", {
            count: selected.size,
            max: IMPORT_MAX_FILES,
            defaultValue: "已选 {{count}}/{{max}}",
          })}
        </span>
        <button
          type="button"
          onClick={() => void runImport()}
          disabled={busy || selected.size === 0}
          className={PRIMARY_BTN}
        >
          {busy && <Loader2 size={14} className="animate-spin" />}
          {t("knowledge.import.action", "导入所选文件")}
        </button>
      </div>
    </div>
  );
}
