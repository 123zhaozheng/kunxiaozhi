import { useTranslation } from "react-i18next";
import type { KBPublicStatus } from "../../services/api/knowledgeBase";
import { getPublicStatusMeta, type StatusTone } from "./knowledgeUtils";

const TONE_CLASS: Record<StatusTone, string> = {
  neutral: "scb__status-pill--inactive",
  info: "scb__status-pill--inactive",
  warning: "scb__status-pill--published",
  success: "scb__status-pill--installed",
  danger: "scb__status-pill--danger",
};

export function KnowledgePublicStatusPill({
  status,
  alwaysShow = false,
}: {
  status: KBPublicStatus | undefined;
  alwaysShow?: boolean;
}) {
  const { t } = useTranslation();
  const meta = getPublicStatusMeta(status);
  if (!meta.visible && !alwaysShow) return null;
  const key = status ?? "none";
  return (
    <span className={`scb__status-pill ${TONE_CLASS[meta.tone]}`}>
      {t(`knowledge.publicStatus.${key}`, key)}
    </span>
  );
}
