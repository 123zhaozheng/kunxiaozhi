/**
 * Explains how to sign in via OA portal (no token on login page).
 */

import { useTranslation } from "react-i18next";
import { Building2, X } from "lucide-react";

interface OaSsoHelpDialogProps {
  open: boolean;
  onClose: () => void;
}

export function OaSsoHelpDialog({ open, onClose }: OaSsoHelpDialogProps) {
  const { t } = useTranslation();

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-stone-900/40 p-4 backdrop-blur-sm dark:bg-black/50"
      role="dialog"
      aria-modal
      aria-labelledby="oa-sso-help-title"
    >
      <div className="auth-panel relative w-full max-w-md rounded-2xl p-6 shadow-xl">
        <button
          type="button"
          onClick={onClose}
          className="absolute right-4 top-4 rounded-lg p-1 text-stone-400 hover:bg-stone-100 dark:hover:bg-stone-800"
          aria-label={t("common.close", "关闭")}
        >
          <X size={18} />
        </button>
        <div className="mb-4 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400">
            <Building2 size={22} />
          </span>
          <h2
            id="oa-sso-help-title"
            className="text-lg font-semibold text-stone-900 dark:text-stone-50"
          >
            {t("auth.oaSso.howToTitle")}
          </h2>
        </div>
        <ol className="list-decimal space-y-2 pl-5 text-sm text-stone-600 dark:text-stone-400">
          <li>{t("auth.oaSso.howToStep1")}</li>
          <li>{t("auth.oaSso.howToStep2")}</li>
          <li>{t("auth.oaSso.howToStep3")}</li>
        </ol>
        <button
          type="button"
          onClick={onClose}
          className="mt-6 w-full rounded-xl bg-stone-900 py-2.5 text-sm font-medium text-white dark:bg-stone-100 dark:text-stone-900"
        >
          {t("common.confirm", "知道了")}
        </button>
      </div>
    </div>
  );
}