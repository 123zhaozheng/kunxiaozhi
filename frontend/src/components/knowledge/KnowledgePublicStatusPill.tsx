import { useTranslation } from "react-i18next";
import type { KBPublicStatus } from "../../services/api/knowledgeBase";
import { KnowledgeStatusPill } from "./KnowledgeStatusPill";
import { getPublicStatusMeta } from "./knowledgeUtils";

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
    <KnowledgeStatusPill tone={meta.tone}>
      {t(`knowledge.publicStatus.${key}`, key)}
    </KnowledgeStatusPill>
  );
}
