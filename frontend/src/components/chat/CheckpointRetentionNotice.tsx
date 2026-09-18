import { Moon } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Explains that a long-idle session's AI context was reclaimed.
 *
 * Rendered above the message list as a divider rather than a dismissible
 * banner: the boundary itself carries the meaning (readable history above,
 * a fresh context below) and stays correct as the conversation continues.
 */
export function CheckpointRetentionNotice() {
  const { t } = useTranslation();

  return (
    <div className="px-4 pt-3 pb-1" data-testid="checkpoint-retention-notice">
      <div className="flex items-start gap-3 px-4 py-3 rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20">
        <Moon
          size={16}
          className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-300"
        />
        <p className="text-xs leading-relaxed text-amber-900 dark:text-amber-100">
          {t("chat.retention.noticeLead")}
          <span className="font-semibold">
            {t("chat.retention.noticeKept")}
          </span>
          {t("chat.retention.noticeTail")}
        </p>
      </div>
    </div>
  );
}
