/**
 * Analytics date range helpers — pure `YYYY-MM-DD` string arithmetic.
 *
 * The backend owns day boundaries (it expands a pure date into a UTC+8
 * half-open interval). The frontend must never compute local day edges with
 * `setHours` / `toISOString`; it only produces calendar date strings.
 */

import type {
  AnalyticsDate,
  AnalyticsRangePreset,
} from "../../../types/analytics";

export interface AnalyticsDateRange {
  /** Inclusive start date, YYYY-MM-DD */
  start: AnalyticsDate;
  /** Inclusive end date, YYYY-MM-DD */
  end: AnalyticsDate;
}

/** Presets with a fixed span (everything except user-picked custom ranges). */
export type FixedRangePreset = Exclude<AnalyticsRangePreset, "custom">;

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function toDateString(year: number, month: number, day: number): AnalyticsDate {
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(
    day,
  ).padStart(2, "0")}`;
}

/** True when the value is a real calendar date shaped `YYYY-MM-DD`. */
export function isValidDateString(value: string): boolean {
  if (!DATE_PATTERN.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (month < 1 || month > 12 || day < 1 || day > 31) return false;
  const roundTrip = new Date(Date.UTC(year, month - 1, day));
  return (
    roundTrip.getUTCFullYear() === year &&
    roundTrip.getUTCMonth() === month - 1 &&
    roundTrip.getUTCDate() === day
  );
}

/** Browser-local calendar date of `now` as `YYYY-MM-DD` (no time component). */
export function todayString(now: Date = new Date()): AnalyticsDate {
  return toDateString(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

/** Add (or subtract) whole days to a `YYYY-MM-DD` date string. */
export function addDaysString(
  value: AnalyticsDate,
  days: number,
): AnalyticsDate {
  const [year, month, day] = value.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  return toDateString(
    shifted.getUTCFullYear(),
    shifted.getUTCMonth() + 1,
    shifted.getUTCDate(),
  );
}

/** Range covered by a fixed preset, ending today (inclusive). */
export function rangeForPreset(
  preset: FixedRangePreset,
  now: Date = new Date(),
): AnalyticsDateRange {
  const end = todayString(now);
  const daysBack = preset === "1d" ? 0 : preset === "7d" ? 6 : 29;
  return { start: addDaysString(end, -daysBack), end };
}

/**
 * Effective range for the current filter state.
 * Custom ranges fall back to the 7-day window until one is applied.
 */
export function effectiveRangeFor(
  preset: AnalyticsRangePreset,
  customRange: AnalyticsDateRange | null,
  now: Date = new Date(),
): AnalyticsDateRange {
  if (preset === "custom" && customRange) return customRange;
  return rangeForPreset(preset === "custom" ? "7d" : preset, now);
}

/** Validate two free-form date inputs; returns a normalized (start ≤ end) range or null. */
export function normalizeRangeInput(
  start: string,
  end: string,
): AnalyticsDateRange | null {
  if (!isValidDateString(start) || !isValidDateString(end)) return null;
  return start <= end ? { start, end } : { start: end, end: start };
}

/** `2026-08-22 → 2026-08-28` display label for a range. */
export function formatRangeLabel(range: AnalyticsDateRange): string {
  return `${range.start} → ${range.end}`;
}
