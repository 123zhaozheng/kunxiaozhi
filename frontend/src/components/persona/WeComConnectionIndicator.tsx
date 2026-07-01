import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import type { PersonaWeComStatus } from "../../types/personaPreset";
import {
  getWeComIndicatorTone,
  getWeComReasonI18nKey,
  getWeComStateI18nKey,
  shouldShowWeComReconnect,
} from "./wecomConnectionPresentation";

interface WeComConnectionIndicatorProps {
  status: PersonaWeComStatus | undefined;
  reconnecting?: boolean;
  onReconnect?: () => void;
}

export function WeComConnectionIndicator({
  status,
  reconnecting,
  onReconnect,
}: WeComConnectionIndicatorProps) {
  const { t } = useTranslation();
  const state = status?.state;
  const tone = getWeComIndicatorTone(state);
  const showReconnect =
    shouldShowWeComReconnect(state) && Boolean(onReconnect);

  const stateLabel = t(getWeComStateI18nKey(state), state ?? "unknown");
  const reasonKey = getWeComReasonI18nKey(status?.reason_code);
  const tooltipParts = [stateLabel];
  if (reasonKey) {
    tooltipParts.push(
      t(reasonKey, status?.reason_code ?? ""),
    );
  }
  if (status?.reason_detail) {
    tooltipParts.push(status.reason_detail);
  }
  const title = tooltipParts.join(" — ");

  return (
    <span
      className="pps-wecom-live inline-flex items-center gap-1.5"
      title={title}
    >
      <span
        className={`pps-wecom-live__dot pps-wecom-live__dot--${tone}`}
        aria-hidden
      />
      {showReconnect && (
        <button
          type="button"
          className="pps-wecom-live__reconnect"
          disabled={reconnecting}
          title={t(
            "personaPresets.wecom.connection.reconnect",
            "Reconnect WeCom",
          )}
          onClick={(e) => {
            e.stopPropagation();
            onReconnect?.();
          }}
        >
          <RefreshCw
            size={12}
            className={reconnecting ? "animate-spin" : undefined}
          />
        </button>
      )}
    </span>
  );
}