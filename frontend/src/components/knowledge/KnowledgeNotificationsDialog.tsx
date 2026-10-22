import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { Bell, CheckCheck, Loader2, X } from "lucide-react";
import {
  knowledgeBaseApi,
  type KBNotification,
} from "../../services/api/knowledgeBase";
import { Pagination } from "../common/Pagination";
import { formatTimeAgo } from "../../utils/datetime";
import {
  dispatchKbNotificationsRefresh,
  subscribeKbNotificationsRefresh,
} from "../../utils/knowledgeBaseEvents";
import { useKbUnreadCount } from "./hooks/useKbUnreadCount";
import { GHOST_BTN, OUTLINE_BTN } from "./knowledgeStyles";
import { getNotificationReason, getNotificationText } from "./knowledgeNotificationText";

const PAGE_SIZE = 10;

/** Bell button for the page header; the unread badge comes from the shared poller. */
export function KnowledgeNotificationsBell({
  onOpenKb,
}: {
  onOpenKb: (kbId: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { unread } = useKbUnreadCount(true);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`${OUTLINE_BTN} relative`}
        aria-label={t("knowledge.notifications.title", "知识库通知")}
        title={t("knowledge.notifications.title", "知识库通知")}
      >
        <Bell size={16} />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <KnowledgeNotificationsDialog
          onClose={() => setOpen(false)}
          onOpenKb={(id) => {
            setOpen(false);
            onOpenKb(id);
          }}
        />
      )}
    </>
  );
}

function KnowledgeNotificationsDialog({
  onClose,
  onOpenKb,
}: {
  onClose: () => void;
  onOpenKb: (kbId: string) => void;
}) {
  const { t } = useTranslation();
  const [items, setItems] = useState<KBNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [unread, setUnread] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await knowledgeBaseApi.listNotifications({ page, page_size: PAGE_SIZE });
      setItems(res.items);
      setTotal(res.total);
      setUnread(res.unread);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
    return subscribeKbNotificationsRefresh(() => void load());
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const markRead = async (ids: string[] | "all") => {
    try {
      await knowledgeBaseApi.markNotificationsRead(ids);
      dispatchKbNotificationsRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const open = (n: KBNotification) => {
    if (!n.read) void markRead([n.id]);
    if (n.kb_id && n.type !== "share_removed" && n.type !== "public_unpublished") {
      onOpenKb(n.kb_id);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("knowledge.notifications.title", "知识库通知")}
        className="flex max-h-[85vh] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl dark:bg-stone-900 sm:max-w-lg sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-stone-100 px-5 py-4 dark:border-stone-800">
          <h2 className="text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("knowledge.notifications.title", "知识库通知")}
          </h2>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => void markRead("all")}
              disabled={unread === 0}
              className={`${GHOST_BTN} !px-2 !py-1 text-xs`}
            >
              <CheckCheck size={14} />
              {t("knowledge.notifications.markAllRead", "全部已读")}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
              aria-label={t("common.close", "关闭")}
            >
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-3 py-3">
          {loading ? (
            <div className="flex justify-center py-10 text-stone-400">
              <Loader2 className="animate-spin" size={20} />
            </div>
          ) : items.length === 0 ? (
            <p className="py-10 text-center text-sm text-stone-500 dark:text-stone-400">
              {t("knowledge.notifications.empty", "暂无通知")}
            </p>
          ) : (
            <ul className="space-y-1">
              {items.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    onClick={() => open(n)}
                    className="flex w-full items-start gap-2 rounded-xl px-3 py-2.5 text-left hover:bg-stone-50 dark:hover:bg-stone-800"
                  >
                    <span
                      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                        n.read ? "bg-transparent" : "bg-red-500"
                      }`}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm text-stone-900 dark:text-stone-100">
                        {getNotificationText(t, n)}
                      </span>
                      {getNotificationReason(n) && (
                        <span className="mt-0.5 block text-xs text-stone-500 dark:text-stone-400">
                          {t("knowledge.notifications.reason", {
                            reason: getNotificationReason(n),
                            defaultValue: "原因：{{reason}}",
                          })}
                        </span>
                      )}
                      <span className="mt-0.5 block text-[11px] text-stone-400">
                        {n.created_at ? formatTimeAgo(t, n.created_at) : ""}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {total > PAGE_SIZE && (
          <div className="border-t border-stone-100 px-4 py-2 dark:border-stone-800">
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
          </div>
        )}
      </div>
    </div>
  );
}
