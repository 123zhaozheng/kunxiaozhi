import type {
  RunStatus,
  ScheduleKind,
  ScheduleSpec,
} from "../../../types/scheduledTask";

export const DEFAULT_TIMEZONE = "Asia/Shanghai";

export const KIND_ORDER: ScheduleKind[] = [
  "weekdays",
  "daily",
  "weekly",
  "monthly",
  "once",
  "interval",
];

export const WEEKDAY_NUMBERS = [1, 2, 3, 4, 5, 6, 7];

export const QUICK_TIMES = ["08:30", "09:00", "12:00", "17:00", "18:00"];

/** Default spec when the user switches frequency kind; keeps the chosen time. */
export function specForKind(kind: ScheduleKind, prev?: ScheduleSpec): ScheduleSpec {
  const time = prev?.time || "09:00";
  switch (kind) {
    case "daily":
    case "weekdays":
      return { kind, time };
    case "weekly":
      return {
        kind,
        time,
        weekdays: prev?.weekdays && prev.weekdays.length ? prev.weekdays : [1],
      };
    case "monthly":
      return { kind, time, month_day: prev?.month_day ?? 1 };
    case "once":
      return { kind, run_at: prev?.run_at || defaultOnceValue() };
    case "interval":
      return { kind, every_hours: prev?.every_hours || 2 };
  }
}

/** Tomorrow 09:00 local, formatted for <input type="datetime-local">. */
export function defaultOnceValue(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(9, 0, 0, 0);
  return toLocalInput(d);
}

export function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

/** Normalise backend run_at (may include seconds / offset) for the input. */
export function runAtForInput(value?: string | null): string {
  if (!value) return defaultOnceValue();
  return value.slice(0, 16);
}

/** Strip empty fields so the backend receives a clean spec. */
export function cleanSpec(spec: ScheduleSpec): ScheduleSpec {
  const out: ScheduleSpec = { kind: spec.kind };
  if (spec.kind === "once") {
    out.run_at = spec.run_at ? `${spec.run_at.slice(0, 16)}:00` : null;
    return out;
  }
  if (spec.kind === "interval") {
    out.every_hours = spec.every_hours ?? 2;
    return out;
  }
  out.time = spec.time;
  if (spec.kind === "weekly") out.weekdays = spec.weekdays ?? [];
  if (spec.kind === "monthly") out.month_day = spec.month_day ?? 1;
  return out;
}

export function formatFireTime(iso: string, locale: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(locale, {
    timeZone: DEFAULT_TIMEZONE,
    month: "long",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function relativeFromNow(iso: string, locale: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(diffMs)) return "";
  const minutes = Math.round(diffMs / 60000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(minutes) < 60) return rtf.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 48) return rtf.format(hours, "hour");
  return rtf.format(Math.round(hours / 24), "day");
}

export function formatDuration(ms?: number | null): string {
  if (ms == null) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${String(s % 60).padStart(2, "0")}s`;
}

export const STATUS_STYLES: Record<RunStatus, string> = {
  pending:
    "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300",
  running: "bg-sky-50 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  success:
    "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  failed: "bg-rose-50 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300",
  timeout:
    "bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  skipped:
    "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400",
};
