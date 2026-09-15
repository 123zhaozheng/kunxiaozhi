/**
 * Shared thinking-effort scale.
 *
 * Values mirror the backend contract in `src/agents/core/thinking.py`
 * (`off` plus SUPPORTED_THINKING_LEVELS). The slider and the legacy dropdown
 * must agree on both the value set and the ordering, so the scale lives here
 * rather than in either component.
 */

export const THINKING_LEVELS = ["off", "low", "medium", "high", "max"] as const;
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];

export const DEFAULT_THINKING_LEVEL: ThinkingLevel = "off";

/** i18n keys — reuse the backend-declared option label keys so the slider
 *  and the legacy dropdown always render identical wording. */
export const THINKING_LEVEL_LABEL_KEY: Record<ThinkingLevel, string> = {
  off: "agentOptions.enableThinking.options.off",
  low: "agentOptions.enableThinking.options.low",
  medium: "agentOptions.enableThinking.options.medium",
  high: "agentOptions.enableThinking.options.high",
  max: "agentOptions.enableThinking.options.max",
};

export function normalizeThinkingLevel(
  value: boolean | string | number | undefined | null,
): ThinkingLevel {
  // Legacy sessions stored a boolean before the 5-step scale existed.
  if (typeof value === "boolean") return value ? "medium" : "off";
  if (typeof value !== "string") return DEFAULT_THINKING_LEVEL;

  const normalized = value.trim().toLowerCase();
  if ((THINKING_LEVELS as readonly string[]).includes(normalized)) {
    return normalized as ThinkingLevel;
  }
  if (["enabled", "enable", "on", "true"].includes(normalized)) return "medium";
  if (["disabled", "disable", "false", "none"].includes(normalized)) {
    return "off";
  }
  return DEFAULT_THINKING_LEVEL;
}

export function thinkingLevelToIndex(level: ThinkingLevel): number {
  const index = THINKING_LEVELS.indexOf(level);
  return index < 0 ? 0 : index;
}

export function thinkingLevelFromIndex(index: number): ThinkingLevel {
  const clamped = Math.min(
    THINKING_LEVELS.length - 1,
    Math.max(0, Math.round(index)),
  );
  return THINKING_LEVELS[clamped];
}
