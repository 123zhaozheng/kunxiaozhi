import { useTranslation } from "react-i18next";
import type { KBQuota } from "../../services/api/knowledgeBase";
import { formatFileSize } from "../documents/utils/fileSize";
import { isKbCountQuotaReached } from "./knowledgeUtils";

interface KnowledgeQuotaSummaryProps {
  quota: KBQuota;
  className?: string;
}

/** One low-contrast line: "2 / 10 个知识库 · 12 MB / 2 GB". */
export function KnowledgeQuotaSummary({ quota, className = "" }: KnowledgeQuotaSummaryProps) {
  const { t } = useTranslation();
  const storageLimit = quota.limits.kb_storage_quota_mb * 1024 * 1024;
  const countReached = isKbCountQuotaReached(quota);
  const docsPerKb = t("knowledge.quota.docsPerKb", {
    max: quota.limits.kb_max_docs_per_kb,
    defaultValue: "单库最多 {{max}} 个文档",
  });

  return (
    <p
      className={`text-xs text-[var(--theme-text-tertiary)] ${className}`}
      title={docsPerKb}
    >
      {t("knowledge.quota.footerCount", {
        used: quota.usage.kb_count,
        max: quota.limits.kb_max_count,
        defaultValue: "{{used}} / {{max}} 个知识库",
      })}
      {" · "}
      {formatFileSize(quota.usage.storage_bytes)} / {formatFileSize(storageLimit)}
      {countReached && (
        <>
          {" · "}
          <span className="text-[var(--theme-text-secondary)]">
            {t("knowledge.quota.countReached", "知识库数量已达上限")}
          </span>
        </>
      )}
    </p>
  );
}
