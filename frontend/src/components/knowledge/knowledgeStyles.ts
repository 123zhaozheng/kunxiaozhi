const FOCUS_RING =
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]";
const BTN_BASE = `inline-flex items-center justify-center gap-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`;

const PRIMARY_TONE =
  "bg-[var(--theme-text)] font-medium text-[var(--theme-bg-card)] hover:opacity-90";
const OUTLINE_TONE =
  "border border-[var(--theme-border)] bg-[var(--theme-bg-card)] text-[var(--theme-text)] hover:bg-[var(--theme-sidebar-hover)]";

export const PRIMARY_BTN = `${BTN_BASE} ${PRIMARY_TONE} rounded-xl px-4 py-2 text-sm`;
export const PRIMARY_BTN_SM = `${BTN_BASE} ${PRIMARY_TONE} h-9 rounded-lg px-3 text-sm`;

export const GHOST_BTN = `${BTN_BASE} rounded-xl px-4 py-2 text-sm text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)] hover:text-[var(--theme-text)]`;

export const OUTLINE_BTN = `${BTN_BASE} ${OUTLINE_TONE} rounded-xl px-3 py-2 text-sm`;
export const OUTLINE_BTN_SM = `${BTN_BASE} ${OUTLINE_TONE} h-7 rounded-lg px-2.5 text-xs`;

export const ICON_OUTLINE_BTN = `${BTN_BASE} ${OUTLINE_TONE} h-9 w-9 shrink-0 rounded-xl`;

export const DANGER_BTN = `${BTN_BASE} rounded-xl border border-red-200 bg-[var(--theme-bg-card)] px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-900/60 dark:text-red-400 dark:hover:bg-red-900/20`;

export const ROW_ACTION_BTN = `inline-flex h-8 w-8 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-sidebar-hover)] hover:text-[var(--theme-text)] disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`;

export const INPUT_CLS =
  "w-full rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2.5 text-sm text-[var(--theme-text)] placeholder:text-[var(--theme-text-tertiary)] focus:outline-none focus:ring-2 focus:ring-[var(--theme-ring)]";

export const NOTICE_WARNING =
  "flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-900/30 dark:text-amber-200";

export const NOTICE_BAR =
  "flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-amber-50 px-3 py-2 text-[13px] text-amber-800 dark:bg-amber-900/30 dark:text-amber-200";
