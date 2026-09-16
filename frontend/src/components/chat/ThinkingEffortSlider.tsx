import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { THINKING_LEVEL_COLOR } from "./chatInputConstants";
import {
  THINKING_LEVELS,
  THINKING_LEVEL_LABEL_KEY,
  normalizeThinkingLevel,
  thinkingLevelFromIndex,
  thinkingLevelToIndex,
  type ThinkingLevel,
} from "./thinkingLevels";

interface ThinkingEffortSliderProps {
  value: boolean | string | number | null | undefined;
  onChange: (level: ThinkingLevel) => void;
  label?: string;
}

export function ThinkingEffortSlider({
  value,
  onChange,
  label,
}: ThinkingEffortSliderProps) {
  const { t } = useTranslation();
  const level = normalizeThinkingLevel(value);
  const index = thinkingLevelToIndex(level);
  const selectedLabel = t(THINKING_LEVEL_LABEL_KEY[level]);
  const levelColor = THINKING_LEVEL_COLOR[level] ?? THINKING_LEVEL_COLOR.off;
  const progress = `${(index / (THINKING_LEVELS.length - 1)) * 100}%`;
  const sliderStyle = {
    "--thinking-slider-progress": progress,
    "--thinking-slider-color": levelColor.text,
  } as CSSProperties;

  return (
    <div className="thinking-effort-slider" style={sliderStyle}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-[var(--theme-text-secondary)]">
          {label ?? t("chat.thinkingIntensity", "思考强度")}
        </span>
        <span className="text-xs font-medium text-[var(--thinking-slider-color)]">
          {selectedLabel}
        </span>
      </div>

      <div className="thinking-effort-slider__control" data-level={level}>
        <div className="thinking-effort-slider__track" aria-hidden="true" />
        <div className="thinking-effort-slider__progress" aria-hidden="true" />
        {THINKING_LEVELS.map((step, stepIndex) => (
          <span
            key={step}
            className="thinking-effort-slider__dot"
            style={{ left: `${(stepIndex / (THINKING_LEVELS.length - 1)) * 100}%` }}
            data-active={stepIndex <= index ? "true" : undefined}
            aria-hidden="true"
          />
        ))}
        <span
          className="thinking-effort-slider__knob"
          style={{ left: progress }}
          aria-hidden="true"
        />
        <input
          type="range"
          min={0}
          max={THINKING_LEVELS.length - 1}
          step={1}
          value={index}
          onChange={(event) =>
            onChange(thinkingLevelFromIndex(Number(event.target.value)))
          }
          aria-label={label ?? t("chat.thinkingIntensity", "思考强度")}
          aria-valuenow={index}
          aria-valuetext={selectedLabel}
          className="thinking-effort-slider__input"
        />
      </div>

      <div className="thinking-effort-slider__labels" aria-hidden="true">
        {THINKING_LEVELS.map((step) => (
          <span key={step}>{t(THINKING_LEVEL_LABEL_KEY[step])}</span>
        ))}
      </div>
    </div>
  );
}
