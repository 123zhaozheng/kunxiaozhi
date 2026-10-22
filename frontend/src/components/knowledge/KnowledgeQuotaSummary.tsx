import { useTranslation } from "react-i18next";
import type { KBQuota } from "../../services/api/knowledgeBase";
import { formatFileSize } from "../documents/utils/fileSize";
import { isKbCountQuotaReached, quotaPercent } from "./knowledgeUtils";

interface KnowledgeQuotaSummaryProps {
  quota: KBQuota;
  className?: string;
}

export function KnowledgeQuotaSummary({ quota, className = "" }: KnowledgeQuotaSummaryProps) {
  const { t } = useTranslation();
  const storageLimit = quota.limits.kb_storage_quota_mb * 1024 * 1024;
  const countReached = isKbCountQuotaReached(quota);

  return (
    <div
      className={`flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-[var(--theme-text-secondary)] ${className}`}
    >
      <span className={countReached ? "font-medium text-red-600 dark:text-red-400" : undefined}>
        {t("knowledge.quota.count", {
          used: quota.usage.kb_count,
          max: quota.limits.kb_max_count,
          defaultValue: "知识库 {{used}}/{{max}}",
        })}
      </span>
      <span className="inline-flex items-center gap-2">
        {t("knowledge.quota.storage", {
          used: formatFileSize(quota.usage.storage_bytes),
          max: formatFileSize(storageLimit),
          defaultValue: "存储 {{used}} / {{max}}",
        })}
        <span
          className="h-1.5 w-24 overflow-hidden rounded-full bg-[var(--theme-bg-subtle)]"
          role="progressbar"
          aria-valuenow={quotaPercent(quota.usage.storage_bytes, storageLimit)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span
            className="block h-full rounded-full bg-[var(--theme-primary)]"
            style={{ width: `${quotaPercent(quota.usage.storage_bytes, storageLimit)}%` }}
          />
        </span>
      </span>
      <span>
        {t("knowledge.quota.docsPerKb", {
          max: quota.limits.kb_max_docs_per_kb,
          defaultValue: "单库最多 {{max}} 个文档",
        })}
      </span>
    </div>
  );
}
