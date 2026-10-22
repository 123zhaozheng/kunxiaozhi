import { useCallback, useEffect, useState } from "react";
import { knowledgeBaseApi } from "../../../services/api/knowledgeBase";
import { subscribeKbNotificationsRefresh } from "../../../utils/knowledgeBaseEvents";
import { KB_NOTIFICATION_POLL_MS } from "../knowledgeUtils";

export function useKbUnreadCount(enabled: boolean) {
  const [unread, setUnread] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const res = await knowledgeBaseApi.getUnreadCount();
      setUnread(res.count);
    } catch {
      // Best effort: keep the previous count.
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setUnread(0);
      return;
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), KB_NOTIFICATION_POLL_MS);
    const unsubscribe = subscribeKbNotificationsRefresh(() => void refresh());
    return () => {
      window.clearInterval(timer);
      unsubscribe();
    };
  }, [enabled, refresh]);

  return { unread, refresh };
}
