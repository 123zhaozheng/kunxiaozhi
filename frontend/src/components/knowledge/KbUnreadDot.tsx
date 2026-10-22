import { useTranslation } from "react-i18next";
import { useSettingsContext } from "../../contexts/SettingsContext";
import { useKbUnreadCount } from "./hooks/useKbUnreadCount";

/** Small red dot over the sidebar knowledge entry while notifications are unread. */
export function KbUnreadDot() {
  const { t } = useTranslation();
  const { enableKnowledgeBase } = useSettingsContext();
  const { unread } = useKbUnreadCount(enableKnowledgeBase);
  if (unread <= 0) return null;
  return (
    <span
      role="status"
      aria-label={t("knowledge.notifications.unreadDot", "有未读知识库通知")}
      className="absolute right-1.5 top-1 h-2 w-2 rounded-full bg-red-500 ring-1 ring-white/30"
    />
  );
}
