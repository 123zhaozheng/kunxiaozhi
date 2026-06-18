import { useTranslation } from "react-i18next";

function ArrowUpIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
      <path
        fillRule="evenodd"
        d="M10 17a.75.75 0 01-.75-.75V5.612L5.29 9.77a.75.75 0 01-1.08-1.04l5.25-5.5a.75.75 0 011.08 0l5.25 5.5a.75.75 0 11-1.08 1.04l-3.96-4.158V16.25A.75.75 0 0110 17z"
        clipRule="evenodd"
      />
    </svg>
  );
}

function ArrowDownIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
      <path
        fillRule="evenodd"
        d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 011.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z"
        clipRule="evenodd"
      />
    </svg>
  );
}

interface ScrollButtonsProps {
  showTop: boolean;
  showBottom: boolean;
  onScrollToTop: () => void;
  onScrollToBottom: () => void;
}

export function ScrollButtons({
  showTop,
  showBottom,
  onScrollToTop,
  onScrollToBottom,
}: ScrollButtonsProps) {
  const { t } = useTranslation();

  return (
    <div className="fixed bottom-5 right-5 sm:bottom-6 sm:right-6 z-40 flex flex-col gap-2">
      <button
        onClick={onScrollToTop}
        className={`landing-scroll-btn w-10 h-10 rounded-xl bg-white/90 dark:bg-stone-800/90 border border-stone-200/60 dark:border-stone-700/40 shadow-lg shadow-stone-200/30 dark:shadow-stone-900/40 flex items-center justify-center text-stone-400 dark:text-stone-500 hover:text-stone-700 dark:hover:text-stone-200 hover:bg-white dark:hover:bg-stone-700 hover:shadow-xl hover:-translate-y-0.5 transition-all duration-300 ${
          showTop
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none"
        }`}
        aria-label={t("common.scrollToTop")}
      >
        <ArrowUpIcon />
      </button>
      <button
        onClick={onScrollToBottom}
        className={`landing-scroll-btn w-10 h-10 rounded-xl bg-white/90 dark:bg-stone-800/90 border border-stone-200/60 dark:border-stone-700/40 shadow-lg shadow-stone-200/30 dark:shadow-stone-900/40 flex items-center justify-center text-stone-400 dark:text-stone-500 hover:text-stone-700 dark:hover:text-stone-200 hover:bg-white dark:hover:bg-stone-700 hover:shadow-xl hover:-translate-y-0.5 transition-all duration-300 ${
          showBottom
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none"
        }`}
        aria-label={t("common.scrollToBottom")}
      >
        <ArrowDownIcon />
      </button>
    </div>
  );
}

export default ScrollButtons;
