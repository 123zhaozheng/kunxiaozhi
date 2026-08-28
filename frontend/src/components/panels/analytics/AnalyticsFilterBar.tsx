/**
 * Analytics Filter Bar — date range + preset buttons + Persona/agent filters.
 *
 * The preset button group (`AnalyticsRangePresetPicker`) is shared with
 * `PresetAnalyticsModal` so the two surfaces no longer keep duplicate
 * implementations. All dates are pure `YYYY-MM-DD` strings; day boundaries
 * are owned by the backend (UTC+8 half-open intervals).
 */

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Calendar, Check, ChevronDown, Clock } from "lucide-react";
import type { AnalyticsRangePreset } from "../../../types/analytics";
import {
  formatRangeLabel,
  normalizeRangeInput,
  type AnalyticsDateRange,
  type FixedRangePreset,
} from "./analyticsDates";

const FIXED_PRESETS: FixedRangePreset[] = ["1d", "7d", "30d"];

interface PresetButtonProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function PresetButton({ label, active, onClick }: PresetButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
        active
          ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
          : "text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
      }`}
    >
      {label}
    </button>
  );
}

interface CustomRangePickerProps {
  start: string;
  end: string;
  onStartChange: (value: string) => void;
  onEndChange: (value: string) => void;
  onApply: () => void;
  onCancel: () => void;
}

function CustomRangePicker({
  start,
  end,
  onStartChange,
  onEndChange,
  onApply,
  onCancel,
}: CustomRangePickerProps) {
  const { t } = useTranslation();
  return (
    <div className="absolute right-0 top-full z-40 mt-2 w-72 rounded-xl border border-[var(--glass-border)] bg-[var(--theme-bg-card)] p-3 shadow-xl">
      <label className="block text-xs text-stone-500 dark:text-stone-400">
        {t("analytics.timeRange.start")}
      </label>
      <input
        type="date"
        value={start}
        onChange={(e) => onStartChange(e.target.value)}
        className="glass-input mt-1 w-full px-2 py-1.5 text-sm"
      />
      <label className="mt-2 block text-xs text-stone-500 dark:text-stone-400">
        {t("analytics.timeRange.end")}
      </label>
      <input
        type="date"
        value={end}
        onChange={(e) => onEndChange(e.target.value)}
        className="glass-input mt-1 w-full px-2 py-1.5 text-sm"
      />
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-3 py-1.5 text-sm text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
        >
          {t("common.cancel", "Cancel")}
        </button>
        <button
          type="button"
          onClick={onApply}
          className="rounded-lg bg-[var(--theme-primary)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          <span className="inline-flex items-center gap-1">
            <Check size={14} />
            {t("analytics.timeRange.apply", "Apply")}
          </span>
        </button>
      </div>
    </div>
  );
}

export interface AnalyticsRangePresetPickerProps {
  /** Currently active preset (drives the highlighted button). */
  preset: AnalyticsRangePreset;
  /** Label shown on the custom button while a custom range is active. */
  customLabel: string;
  onPresetChange: (preset: FixedRangePreset) => void;
  onCustomRangeApply: (range: AnalyticsDateRange) => void;
}

/**
 * Time range button group: [1 day][7 days][30 days][custom].
 * Owns the custom-range popover; the parent owns the resulting filter state.
 */
export function AnalyticsRangePresetPicker({
  preset,
  customLabel,
  onPresetChange,
  onCustomRangeApply,
}: AnalyticsRangePresetPickerProps) {
  const { t } = useTranslation();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [draftStart, setDraftStart] = useState("");
  const [draftEnd, setDraftEnd] = useState("");
  const pickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!pickerOpen) return;
    const handler = (event: MouseEvent) => {
      if (
        pickerRef.current &&
        !pickerRef.current.contains(event.target as Node)
      ) {
        setPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pickerOpen]);

  const handlePresetClick = (next: AnalyticsRangePreset) => {
    if (next === "custom") {
      setPickerOpen((open) => !open);
      return;
    }
    setPickerOpen(false);
    onPresetChange(next);
  };

  const applyCustomRange = () => {
    const range = normalizeRangeInput(draftStart, draftEnd);
    if (!range) return;
    onCustomRangeApply(range);
    setPickerOpen(false);
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5" role="tablist">
        {FIXED_PRESETS.map((key) => (
          <PresetButton
            key={key}
            label={t(`analytics.timeRange.${key}`)}
            active={preset === key}
            onClick={() => handlePresetClick(key)}
          />
        ))}
      </div>
      <div ref={pickerRef} className="relative ml-auto">
        <button
          type="button"
          onClick={() => handlePresetClick("custom")}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
            preset === "custom"
              ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
              : "text-stone-600 hover:bg-[var(--glass-bg-subtle)] dark:text-stone-300"
          }`}
        >
          <Calendar size={14} aria-hidden />
          <span>
            {preset === "custom"
              ? customLabel
              : t("analytics.timeRange.custom")}
          </span>
          <ChevronDown size={14} aria-hidden />
        </button>
        {pickerOpen ? (
          <CustomRangePicker
            start={draftStart}
            end={draftEnd}
            onStartChange={setDraftStart}
            onEndChange={setDraftEnd}
            onApply={applyCustomRange}
            onCancel={() => setPickerOpen(false)}
          />
        ) : null}
      </div>
    </>
  );
}

export interface AnalyticsFilterBarProps {
  preset: AnalyticsRangePreset;
  /** Effective range shown next to the preset buttons. */
  range: AnalyticsDateRange;
  personaPresetId: string;
  agentId: string;
  personaOptions: Array<{ id: string; name: string }>;
  agentOptions: string[];
  onPresetChange: (preset: FixedRangePreset) => void;
  onCustomRangeApply: (range: AnalyticsDateRange) => void;
  onPersonaChange: (personaPresetId: string) => void;
  onAgentChange: (agentId: string) => void;
}

/**
 * Dashboard filter bar: date range display + preset buttons + Persona/agent
 * selects. Fully controlled — the parent panel owns the filter state and
 * every data request derives from it.
 */
export function AnalyticsFilterBar({
  preset,
  range,
  personaPresetId,
  agentId,
  personaOptions,
  agentOptions,
  onPresetChange,
  onCustomRangeApply,
  onPersonaChange,
  onAgentChange,
}: AnalyticsFilterBarProps) {
  const { t } = useTranslation();
  return (
    <div className="px-4 pb-3 pt-1 sm:px-6">
      <div className="glass-card flex flex-wrap items-center gap-2 rounded-xl p-3">
        <div className="flex items-center gap-2 text-sm text-stone-600 dark:text-stone-300">
          <Clock size={16} aria-hidden />
          <span className="font-medium">{t("analytics.timeRange.label")}</span>
        </div>
        <AnalyticsRangePresetPicker
          preset={preset}
          customLabel={formatRangeLabel(range)}
          onPresetChange={onPresetChange}
          onCustomRangeApply={onCustomRangeApply}
        />
        <span className="text-xs font-medium text-stone-500 dark:text-stone-400">
          {formatRangeLabel(range)}（UTC+8）
        </span>
      </div>
      {/* Persona and agent filters */}
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-stone-500 dark:text-stone-400">
          {t("analytics.filters.persona")}
          <select
            className="glass-input min-w-[10rem] px-2 py-1.5 text-sm"
            value={personaPresetId}
            onChange={(event) => onPersonaChange(event.target.value)}
          >
            <option value="">{t("analytics.filters.all")}</option>
            {personaOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-stone-500 dark:text-stone-400">
          {t("analytics.filters.agent")}
          <select
            className="glass-input min-w-[10rem] px-2 py-1.5 text-sm"
            value={agentId}
            onChange={(event) => onAgentChange(event.target.value)}
          >
            <option value="">{t("analytics.filters.all")}</option>
            {agentOptions.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}
