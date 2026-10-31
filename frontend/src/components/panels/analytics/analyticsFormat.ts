/**
 * Analytics formatting helpers and palette constants (non-component module).
 *
 * Kept separate from `analyticsPrimitives.tsx` so that file only exports
 * React components (react-refresh friendly).
 *
 * Colors follow the dataviz reference palette: every chart on the board is a
 * single series or a ranking, so one hue carries magnitude (light/dark steps
 * selected for each surface). Status colors are reserved for the feedback
 * polarity and always ship with an icon + label.
 */

export const SERIES_COLOR = { light: "#2a78d6", dark: "#3987e5" } as const;

export const STATUS_COLOR = { good: "#0ca30c", critical: "#d03b3b" } as const;

export const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function seriesColor(isDark: boolean): string {
  return isDark ? SERIES_COLOR.dark : SERIES_COLOR.light;
}

export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString();
}

export function formatPercent(ratio: number): string {
  if (!Number.isFinite(ratio) || ratio <= 0) return "0%";
  const pct = ratio * 100;
  return pct < 0.1 ? "<0.1%" : `${pct.toFixed(1)}%`;
}

export interface RankedRow<T> {
  item: T;
  /** Share of the KPI total (0-1); null when the total is unknown. */
  share: number | null;
  /** Bar length relative to the largest row (0-100). */
  widthPct: number;
}

export interface RankedRows<T> {
  rows: RankedRow<T>[];
  /** Rows folded past the limit ("其余 N 项"). */
  rest: { count: number; value: number } | null;
}

/**
 * Rank rows largest first and fold the tail past `limit`.
 *
 * Shares are taken against `total` — the same KPI accessor the matching card
 * shows — never against the sum of the visible rows, so a truncated list can
 * not inflate its percentages.
 */
export function rankRows<T extends { label: string; value: number }>(
  items: ReadonlyArray<T>,
  total: number | null,
  limit: number,
): RankedRows<T> {
  const sorted = [...(items ?? [])]
    .filter((item) => Number.isFinite(item.value) && item.value > 0)
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
  const visible = sorted.slice(0, limit);
  const max = visible[0]?.value ?? 0;
  const tail = sorted.slice(limit);
  return {
    rows: visible.map((item) => ({
      item,
      share: total && total > 0 ? Math.min(item.value / total, 1) : null,
      widthPct: max > 0 ? Math.max((item.value / max) * 100, 2) : 0,
    })),
    rest: tail.length
      ? { count: tail.length, value: tail.reduce((sum, item) => sum + item.value, 0) }
      : null,
  };
}
