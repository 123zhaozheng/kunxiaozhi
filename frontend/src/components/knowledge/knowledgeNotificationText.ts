import type { TFunction } from "i18next";
import type { KBNotification } from "../../services/api/knowledgeBase";

const KNOWN_TYPES = new Set([
  "kb_shared",
  "kb_role_changed",
  "kb_removed",
  "kb_member_left",
  "kb_subscribed",
  "kb_publish_pending",
  "kb_approved",
  "kb_rejected",
  "kb_delisted",
  "kb_unpublished",
]);

export function getNotificationText(t: TFunction, n: KBNotification): string {
  const values = { name: n.kb_name, actor: n.actor_username };
  if (!KNOWN_TYPES.has(n.type)) {
    return (
      n.message ||
      t("knowledge.notifications.type.generic", {
        ...values,
        defaultValue: "知识库「{{name}}」有新动态",
      })
    );
  }
  return t(`knowledge.notifications.type.${n.type}`, values);
}

/** Backend appends the admin's reason to `message` after a full-width colon. */
export function getNotificationReason(n: KBNotification): string {
  if (n.type !== "kb_rejected" && n.type !== "kb_delisted") return "";
  const idx = n.message.indexOf("：");
  return idx >= 0 ? n.message.slice(idx + 1).trim() : "";
}
