import type {
  PersonaWeComConnectionState,
  PersonaWeComReasonCode,
  PersonaWeComStatus,
} from "../../types/personaPreset";

export type WeComIndicatorTone = "success" | "warning" | "danger" | "muted";

export function getWeComIndicatorTone(
  state: PersonaWeComConnectionState | undefined,
): WeComIndicatorTone {
  if (!state || state === "unknown") return "muted";
  if (state === "connected") return "success";
  if (state === "connecting" || state === "reconnecting") return "warning";
  return "danger";
}

export function shouldShowWeComReconnect(
  state: PersonaWeComConnectionState | undefined,
): boolean {
  if (!state) return false;
  return (
    state === "disconnected" ||
    state === "failed" ||
    state === "unknown"
  );
}

export function getWeComReasonI18nKey(
  reasonCode: PersonaWeComReasonCode | null | undefined,
): string | null {
  if (!reasonCode) return null;
  const known = [
    "replaced",
    "reconnect_exhausted",
    "auth_failed",
    "lease_lost",
    "disconnected",
  ] as const;
  if ((known as readonly string[]).includes(reasonCode)) {
    return `personaPresets.wecom.connection.reason.${reasonCode}`;
  }
  return "personaPresets.wecom.connection.reason.disconnected";
}

export function getWeComStateI18nKey(
  state: PersonaWeComConnectionState | undefined,
): string {
  const key = state ?? "unknown";
  return `personaPresets.wecom.connection.state.${key}`;
}

export function mergeWeComStatusMaps(
  prev: Record<string, PersonaWeComStatus | undefined>,
  next: Record<string, PersonaWeComStatus | null | undefined>,
): Record<string, PersonaWeComStatus | undefined> {
  const merged = { ...prev };
  for (const [id, status] of Object.entries(next)) {
    if (status) merged[id] = status;
    else delete merged[id];
  }
  return merged;
}