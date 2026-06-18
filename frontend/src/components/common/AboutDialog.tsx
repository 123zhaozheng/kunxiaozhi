import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useVersion } from "../../hooks/useVersion";
import { APP_NAME } from "../../constants";
import { SkeletonLine } from "../skeletons";

interface AboutDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

export function AboutDialog({ isOpen, onClose }: AboutDialogProps) {
  const { t } = useTranslation();
  const { versionInfo, isLoading, error } = useVersion();

  if (!isOpen) return null;

  return createPortal(
    <div
      data-yields-sidebar
      className="fixed inset-0 z-[300] flex items-center justify-center bg-black/50 p-4"
    >
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl dark:bg-stone-800">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-stone-900 dark:text-stone-100 font-serif">
              {t("about.title", APP_NAME)}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-stone-400 hover:bg-stone-100 hover:text-stone-600 dark:hover:bg-stone-700 dark:hover:text-stone-300"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="space-y-3">
          {isLoading ? (
            <div className="space-y-3 py-2">
              <div className="flex items-center justify-between rounded-lg bg-stone-50 p-4 dark:bg-stone-700/50">
                <div className="space-y-2">
                  <SkeletonLine width="w-24" className="!h-2" />
                  <SkeletonLine width="w-20" className="!h-7" />
                </div>
              </div>
            </div>
          ) : error ? (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-400">
              {error}
            </div>
          ) : versionInfo ? (
            <div className="flex items-center justify-between rounded-lg bg-stone-50 p-4 dark:bg-stone-700/50">
              <div>
                <div className="text-xs text-stone-500 dark:text-stone-400">
                  {t("about.currentVersion", "Current Version")}
                </div>
                <div className="font-mono text-2xl font-bold text-stone-900 dark:text-stone-100">
                  {versionInfo.app_version}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {/* Footer */}
        <div className="mt-6 flex justify-end">
          <button
            onClick={onClose}
            className="rounded-lg bg-stone-100 px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-200 dark:bg-stone-700 dark:text-stone-300 dark:hover:bg-stone-600"
          >
            {t("common.close", "Close")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
