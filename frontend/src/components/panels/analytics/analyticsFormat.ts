/**
 * Analytics formatting helpers and palette constants (non-component module).
 *
 * Kept separate from `analyticsPrimitives.tsx` so that file only exports
 * React components (react-refresh friendly).
 */

export const PIE_COLORS = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#3b82f6",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#84cc16",
];

export const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "0";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString();
}

/** Maximum legend label length before ellipsis. */
const LEGEND_LABEL_MAX = 12;

/**
 * Build one donut legend row (`label (value)`).
 *
 * recharts calls `<Legend formatter>` with `(name, entry, index)` where `name`
 * is the `nameKey` **string** — not the data row. The numeric value therefore
 * has to be resolved from the chart's own data by label; reading `.value` off
 * the first argument silently yields `undefined` and renders `— (0)` for every
 * slice.
 */
export function formatDonutLegendLabel(
  name: unknown,
  items: ReadonlyArray<{ label: string; value: number }>,
  unitFormatter?: (value: number) => string,
): string {
  const label = typeof name === "string" && name.length > 0 ? name : "";
  const match = items.find((item) => item.label === label);
  const numeric = Number(match?.value ?? 0);
  const display = label.length > 0 ? label : "—";
  const truncated =
    display.length > LEGEND_LABEL_MAX
      ? `${display.slice(0, LEGEND_LABEL_MAX)}…`
      : display;
  const formatted = unitFormatter
    ? unitFormatter(numeric)
    : formatNumber(numeric);
  return `${truncated} (${formatted})`;
}
