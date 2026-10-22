import toast from "react-hot-toast";
import type { KBImportResult } from "../../services/api/knowledgeBase";

export function reportImportResult(
  t: (key: string, options?: Record<string, unknown>) => string,
  result: KBImportResult,
): void {
  const failed = result.failed + result.skipped;
  const text = t("knowledge.import.result", {
    imported: result.created,
    duplicates: result.duplicate,
    failed,
    defaultValue: "已导入 {{imported}} 个，重复 {{duplicates}} 个，失败 {{failed}} 个",
  });
  if (failed > 0 && result.created === 0) {
    const first = result.results.find((r) => r.status === "failed" || r.status === "skipped");
    toast.error(first?.message || text);
  } else toast.success(text);
}
