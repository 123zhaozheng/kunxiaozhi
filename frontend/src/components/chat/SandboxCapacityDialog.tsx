import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { HelpCircle, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";

interface SandboxCapacityDialogProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  message: string;
  helpText: string;
}

export function SandboxCapacityDialog({
  isOpen,
  onClose,
  title,
  message,
  helpText,
}: SandboxCapacityDialogProps) {
  const { t } = useTranslation();
  const closeRef = useRef<HTMLButtonElement>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setHelpOpen(false);
      closeRef.current?.focus();
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (isOpen && e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return createPortal(
    <div
      data-yields-sidebar
      className="fixed inset-0 z-[300] flex items-center justify-center p-4"
    >
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="sandbox-capacity-title"
        aria-describedby="sandbox-capacity-message"
        className="relative z-10 w-full max-w-[400px] overflow-hidden rounded-2xl border border-stone-200/70 bg-white shadow-2xl shadow-stone-900/10 dark:border-stone-700/50 dark:bg-stone-900 dark:shadow-stone-950/50 animate-in fade-in zoom-in-95 duration-200"
      >
        <div className="relative overflow-hidden px-7 pb-6 pt-8">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(251,146,60,0.18),_transparent_58%)] dark:bg-[radial-gradient(ellipse_at_top,_rgba(251,146,60,0.12),_transparent_55%)]"
          />
          <div
            aria-hidden
            className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-orange-300/50 to-transparent dark:via-orange-500/30"
          />

          <div className="relative text-center">
            <h3
              id="sandbox-capacity-title"
              className="text-lg font-semibold tracking-tight text-stone-900 dark:text-stone-50"
            >
              {title}
            </h3>
            <p
              id="sandbox-capacity-message"
              className="mx-auto mt-2.5 max-w-[18rem] text-sm leading-relaxed text-stone-500 dark:text-stone-400"
            >
              {message}
            </p>
          </div>

          <div className="relative mt-5 flex justify-center">
            <div className="inline-flex items-center gap-2 rounded-full border border-amber-200/80 bg-amber-50/80 px-3 py-1.5 text-xs font-medium text-amber-800 dark:border-amber-800/50 dark:bg-amber-950/40 dark:text-amber-200">
              <Zap size={13} className="shrink-0" />
              {t("chat.sandboxCapacityHint", "试试切换到 Fast 模式")}
            </div>
          </div>
        </div>

        <div className="border-t border-stone-100 px-5 py-4 dark:border-stone-800">
          <button
            type="button"
            className="group flex w-full items-center gap-2 rounded-xl px-2 py-2 text-left text-sm text-stone-500 transition-colors hover:bg-stone-50 hover:text-stone-700 dark:text-stone-400 dark:hover:bg-stone-800/70 dark:hover:text-stone-200"
            aria-expanded={helpOpen}
            aria-label={t("chat.sandboxCapacityHelpLabel", "需要帮助？")}
            onClick={() => setHelpOpen((open) => !open)}
          >
            <span className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-400 transition-colors group-hover:border-stone-300 group-hover:text-stone-600 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-500 dark:group-hover:border-stone-600 dark:group-hover:text-stone-300">
              <HelpCircle size={14} />
            </span>
            <span className="flex-1 font-medium">
              {t("chat.sandboxCapacityHelpLabel", "需要帮助？")}
            </span>
            <span className="text-xs text-stone-400 dark:text-stone-500">
              {helpOpen ? "−" : "?"}
            </span>
          </button>
          {helpOpen && (
            <p className="mt-1 rounded-xl bg-stone-50 px-3.5 py-3 text-sm leading-relaxed text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
              {helpText}
            </p>
          )}
        </div>

        <div className="px-5 pb-5">
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="w-full rounded-xl bg-stone-900 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-stone-800 active:scale-[0.99] active:bg-stone-700 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-200 dark:active:bg-stone-300"
          >
            {t("common.close", "关闭")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
