/**
 * Plaza "分析" entry contract.
 *
 * Product rule: open single-persona analytics surface only.
 * Global /analytics dual-dimension dashboard is for operators, not plaza analyze.
 */

export type PersonaAnalyzeSurface = "scoped_modal";

export function resolvePersonaAnalyzeSurface(): PersonaAnalyzeSurface {
  return "scoped_modal";
}

/** True when a path would open the global analytics operator dashboard. */
export function isGlobalAnalyticsPath(path: string | null | undefined): boolean {
  if (!path) return false;
  const normalized = path.split("?")[0]?.trim() ?? "";
  return normalized === "/analytics";
}
