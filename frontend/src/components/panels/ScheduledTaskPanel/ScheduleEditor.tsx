import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { CalendarClock, Info, Loader2 } from "lucide-react";
import { scheduledTaskApi } from "../../../services/api/scheduledTask";
import type {
  SchedulePreview,
  ScheduleKind,
  ScheduleSpec,
} from "../../../types/scheduledTask";
import {
  DEFAULT_TIMEZONE,
  KIND_ORDER,
  QUICK_TIMES,
  WEEKDAY_NUMBERS,
  cleanSpec,
  formatFireTime,
  runAtForInput,
  specForKind,
} from "./scheduleUtils";

interface ScheduleEditorProps {
  value: ScheduleSpec;
  onChange: (spec: ScheduleSpec) => void;
  onValidityChange?: (valid: boolean) => void;
}

const chip =
  "rounded-xl border px-3 py-2 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-stone-400";
const chipOn =
  "border-stone-900 bg-stone-900 text-white dark:border-stone-100 dark:bg-stone-100 dark:text-stone-900";
const chipOff =
  "border-stone-200 bg-white text-stone-700 hover:border-stone-400 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-200";

/**
 * Friendly frequency picker: big buttons + time chips + live preview.
 * No cron syntax is ever shown to the user.
 */
export function ScheduleEditor({
  value,
  onChange,
  onValidityChange,
}: ScheduleEditorProps) {
  const { t, i18n } = useTranslation();
  const [preview, setPreview] = useState<SchedulePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const payload = useMemo(() => JSON.stringify(cleanSpec(value)), [value]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const handle = window.setTimeout(() => {
      scheduledTaskApi
        .preview(JSON.parse(payload) as ScheduleSpec, DEFAULT_TIMEZONE, 3)
        .then((res) => {
          if (cancelled) return;
          setPreview(res);
          onValidityChange?.(res.valid);
        })
        .catch(() => {
          if (cancelled) return;
          setPreview(null);
          onValidityChange?.(false);
        })
        .finally(() => !cancelled && setLoading(false));
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
    // onValidityChange is intentionally excluded to avoid re-fetch loops
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  const setKind = (kind: ScheduleKind) => onChange(specForKind(kind, value));
  const needsTime = !["once", "interval"].includes(value.kind);

  return (
    <div className="space-y-5">
      <div>
        <div className="mb-2 text-sm font-medium text-stone-800 dark:text-stone-100">
          {t("scheduledTask.editor.howOften", "多久执行一次？")}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {KIND_ORDER.map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => setKind(kind)}
              className={`${chip} text-left ${value.kind === kind ? chipOn : chipOff}`}
            >
              <div className="font-medium">
                {t(`scheduledTask.kind.${kind}`)}
              </div>
              <div
                className={`mt-0.5 text-xs ${
                  value.kind === kind ? "opacity-80" : "text-stone-500 dark:text-stone-400"
                }`}
              >
                {t(`scheduledTask.kindHint.${kind}`)}
              </div>
            </button>
          ))}
        </div>
      </div>

      {value.kind === "weekly" && (
        <div>
          <div className="mb-2 text-sm font-medium text-stone-800 dark:text-stone-100">
            {t("scheduledTask.editor.whichDays", "每周哪几天？（可多选）")}
          </div>
          <div className="flex flex-wrap gap-2">
            {WEEKDAY_NUMBERS.map((d) => {
              const on = (value.weekdays || []).includes(d);
              return (
                <button
                  key={d}
                  type="button"
                  onClick={() => {
                    const cur = new Set(value.weekdays || []);
                    if (on) cur.delete(d);
                    else cur.add(d);
                    onChange({ ...value, weekdays: [...cur].sort() });
                  }}
                  className={`${chip} min-w-[3.5rem] ${on ? chipOn : chipOff}`}
                >
                  {t(`scheduledTask.weekday.${d}`)}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {value.kind === "monthly" && (
        <div>
          <div className="mb-2 text-sm font-medium text-stone-800 dark:text-stone-100">
            {t("scheduledTask.editor.whichDay", "每月哪一天？")}
          </div>
          <select
            value={value.month_day ?? 1}
            onChange={(e) => onChange({ ...value, month_day: Number(e.target.value) })}
            className="w-full rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-sm dark:border-stone-700 dark:bg-stone-900 sm:w-56"
          >
            {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => (
              <option key={d} value={d}>
                {t("scheduledTask.editor.dayOfMonth", { day: d, defaultValue: "{{day}} 号" })}
              </option>
            ))}
            <option value={-1}>{t("scheduledTask.editor.lastDay", "每月最后一天")}</option>
          </select>
          {(value.month_day ?? 1) > 28 && (
            <p className="mt-1.5 text-xs text-amber-600 dark:text-amber-400">
              {t(
                "scheduledTask.editor.shortMonthHint",
                "提示：没有这一天的月份会跳过，如需每月都执行请选“每月最后一天”。",
              )}
            </p>
          )}
        </div>
      )}

      {needsTime && (
        <div>
          <div className="mb-2 text-sm font-medium text-stone-800 dark:text-stone-100">
            {t("scheduledTask.editor.whatTime", "几点执行？（北京时间）")}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {QUICK_TIMES.map((tm) => (
              <button
                key={tm}
                type="button"
                onClick={() => onChange({ ...value, time: tm })}
                className={`${chip} ${value.time === tm ? chipOn : chipOff}`}
              >
                {tm}
              </button>
            ))}
            <input
              type="time"
              value={value.time || ""}
              onChange={(e) => onChange({ ...value, time: e.target.value })}
              aria-label={t("scheduledTask.editor.customTime", "自定义时间")}
              className="rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm dark:border-stone-700 dark:bg-stone-900"
            />
          </div>
        </div>
      )}

      {value.kind === "once" && (
        <div>
          <div className="mb-2 text-sm font-medium text-stone-800 dark:text-stone-100">
            {t("scheduledTask.editor.whenOnce", "在什么时候执行？（北京时间）")}
          </div>
          <input
            type="datetime-local"
            value={runAtForInput(value.run_at)}
            onChange={(e) => onChange({ ...value, run_at: e.target.value })}
            className="rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-sm dark:border-stone-700 dark:bg-stone-900"
          />
        </div>
      )}

      {value.kind === "interval" && (
        <div>
          <div className="mb-2 text-sm font-medium text-stone-800 dark:text-stone-100">
            {t("scheduledTask.editor.everyHours", "每隔几小时？")}
          </div>
          <div className="flex flex-wrap gap-2">
            {[1, 2, 3, 4, 6, 8, 12, 24].map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => onChange({ ...value, every_hours: h })}
                className={`${chip} ${value.every_hours === h ? chipOn : chipOff}`}
              >
                {t("scheduledTask.editor.hours", { count: h, defaultValue: "{{count}} 小时" })}
              </button>
            ))}
          </div>
        </div>
      )}

      {value.kind === "weekdays" && (
        <p className="flex items-start gap-1.5 text-xs text-stone-500 dark:text-stone-400">
          <Info size={14} className="mt-0.5 shrink-0" />
          {t(
            "scheduledTask.editor.holidayHint",
            "工作日指周一至周五，暂不识别法定节假日和调休。",
          )}
        </p>
      )}

      <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4 dark:border-stone-700 dark:bg-stone-800/50">
        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-stone-800 dark:text-stone-100">
          <CalendarClock size={16} />
          {t("scheduledTask.editor.previewTitle", "接下来会在这些时间执行")}
          {loading && <Loader2 size={14} className="animate-spin text-stone-400" />}
        </div>
        {preview?.valid ? (
          <>
            <div className="mb-2 text-sm text-stone-600 dark:text-stone-300">
              {preview.schedule_text}
            </div>
            <ul className="space-y-1 text-sm text-stone-700 dark:text-stone-200">
              {preview.next_runs.map((r) => (
                <li key={r} className="flex items-center gap-2">
                  <span className="size-1.5 rounded-full bg-emerald-500" />
                  {formatFireTime(r, i18n.language)}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <div className="text-sm text-rose-600 dark:text-rose-400">
            {preview?.error ||
              (!loading && t("scheduledTask.editor.previewFailed", "暂时无法预览，请检查设置"))}
          </div>
        )}
      </div>
    </div>
  );
}
