import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Pause } from "lucide-react";
import { LoadingSpinner } from "../../common/LoadingSpinner";

const PAUSE_REASON_MAX = 200;

/** 去掉首尾空白后的原因；长度 1..200 才算有效。 */
function normalizePauseReason(value: string): string {
  return value.trim();
}

function isPauseReasonValid(value: string): boolean {
  const len = normalizePauseReason(value).length;
  return len >= 1 && len <= PAUSE_REASON_MAX;
}

interface PauseReasonDialogProps {
  taskName: string;
  loading?: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

/** 管理员暂停任务时填写原因的对话框；原因必填，未填写时确认按钮禁用。 */
export function PauseReasonDialog({
  taskName,
  loading = false,
  onConfirm,
  onCancel,
}: PauseReasonDialogProps) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const valid = isPauseReasonValid(reason);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !loading) onCancel();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [loading, onCancel]);

  const submit = () => {
    if (!valid || loading) return;
    onConfirm(normalizePauseReason(reason));
  };

  return createPortal(
    <div
      data-yields-sidebar
      className="fixed inset-0 z-[300] flex items-center justify-center"
    >
      <div
        className="absolute inset-0 bg-black/50"
        onClick={loading ? undefined : onCancel}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("scheduledTaskAdmin.pauseDialog.title", "暂停定时任务")}
        className="relative z-10 mx-4 w-full max-w-md overflow-hidden rounded-xl border border-stone-200 bg-white shadow-xl dark:border-stone-700 dark:bg-stone-800"
      >
        <div className="p-5">
          <h3 className="text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("scheduledTaskAdmin.pauseDialog.title", "暂停定时任务")}
          </h3>
          <p className="mt-1.5 text-sm text-stone-600 dark:text-stone-400">
            {t("scheduledTaskAdmin.pauseDialog.desc", {
              name: taskName,
              defaultValue:
                "将暂停“{{name}}”，创建人无法自行恢复，原因会展示给创建人。",
            })}
          </p>
          <label
            htmlFor="scheduled-admin-pause-reason"
            className="mt-4 block text-sm font-medium text-stone-700 dark:text-stone-200"
          >
            {t("scheduledTaskAdmin.pauseDialog.label", "暂停原因（必填）")}
          </label>
          <textarea
            id="scheduled-admin-pause-reason"
            ref={inputRef}
            value={reason}
            maxLength={PAUSE_REASON_MAX}
            rows={3}
            disabled={loading}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
            }}
            placeholder={t(
              "scheduledTaskAdmin.pauseDialog.placeholder",
              "例如：任务内容有风险，待核实后再恢复",
            )}
            className="mt-1.5 w-full resize-none rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-900 outline-none focus:border-stone-400 dark:border-stone-600 dark:bg-stone-900 dark:text-stone-100"
          />
          <div className="mt-1 text-right text-xs text-stone-400">
            {reason.trim().length} / {PAUSE_REASON_MAX}
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-stone-100 bg-stone-50 px-5 py-3 dark:border-stone-700 dark:bg-stone-900/50">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="rounded-lg border border-stone-200 bg-white px-4 py-2 text-sm font-medium text-stone-700 transition-colors hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-300 dark:hover:bg-stone-700"
          >
            {t("common.cancel", "取消")}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!valid || loading}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-amber-600 dark:hover:bg-amber-700"
          >
            {loading ? (
              <LoadingSpinner size="sm" color="text-current" />
            ) : (
              <Pause size={16} className="shrink-0" />
            )}
            {t("scheduledTaskAdmin.pauseDialog.confirm", "确认暂停")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
