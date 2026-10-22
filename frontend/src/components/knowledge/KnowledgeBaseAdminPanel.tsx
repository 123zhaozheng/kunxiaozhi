import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { Eye, LibraryBig, Loader2, X } from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { Pagination } from "../common/Pagination";
import { formatDateTimeShort } from "../../utils/datetime";
import { formatFileSize } from "../documents/utils/fileSize";
import {
  knowledgeBaseAdminApi,
  type KBAdminItem,
  type KBAdminStatus,
} from "../../services/api/knowledgeBase";
import {
  DANGER_BTN,
  GHOST_BTN,
  INPUT_CLS,
  OUTLINE_BTN,
  PRIMARY_BTN,
} from "./knowledgeStyles";
import { ADMIN_PAGE_SIZE } from "./knowledgeUtils";

const STATUS_TABS: KBAdminStatus[] = ["pending", "approved", "rejected", "delisted"];

type ReasonAction = { kind: "reject" | "delist"; item: KBAdminItem };

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="flex max-h-[85vh] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl dark:bg-stone-900 sm:max-w-md sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-stone-100 px-5 py-4 dark:border-stone-800">
          <h2 className="text-base font-semibold text-stone-900 dark:text-stone-100">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
            aria-label={t("common.close", "关闭")}
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function KnowledgeBaseAdminPanel() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<KBAdminStatus>("pending");
  const [searchInput, setSearchInput] = useState("");
  const q = useDebounced(searchInput.trim(), 300);
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<KBAdminItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reasonAction, setReasonAction] = useState<ReasonAction | null>(null);
  const [reason, setReason] = useState("");
  const [viewing, setViewing] = useState<KBAdminItem | null>(null);

  // Reset to the first page whenever the filter changes.
  useEffect(() => setPage(1), [status, q]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await knowledgeBaseAdminApi.list({
        status,
        q,
        page,
        page_size: ADMIN_PAGE_SIZE,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [status, q, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const approve = async (item: KBAdminItem) => {
    setBusyId(item.id);
    try {
      await knowledgeBaseAdminApi.approve(item.id);
      toast.success(t("knowledge.admin.approved", "已通过"));
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  const submitReason = async () => {
    if (!reasonAction || reason.trim() === "") return;
    const { kind, item } = reasonAction;
    setBusyId(item.id);
    try {
      if (kind === "reject") await knowledgeBaseAdminApi.reject(item.id, reason.trim());
      else await knowledgeBaseAdminApi.delist(item.id, reason.trim());
      toast.success(
        kind === "reject"
          ? t("knowledge.admin.rejectedToast", "已驳回")
          : t("knowledge.admin.delistedToast", "已下架"),
      );
      setReasonAction(null);
      setReason("");
      await load();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  const openReason = (kind: ReasonAction["kind"], item: KBAdminItem) => {
    setReason("");
    setReasonAction({ kind, item });
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PanelHeader
        title={t("knowledge.admin.title", "知识库管理")}
        subtitle={t("knowledge.admin.subtitle", "审核用户公开的知识库，或下架已公开的知识库")}
        icon={<LibraryBig />}
        searchValue={searchInput}
        onSearchChange={setSearchInput}
        searchPlaceholder={t("knowledge.admin.searchPlaceholder", "搜索知识库或所有者")}
      />

      <div className="flex shrink-0 items-center border-b border-[var(--theme-border)] px-4 py-2">
        <div
          className="inline-flex items-center gap-1 rounded-lg bg-[var(--theme-bg-subtle)] p-1"
          role="tablist"
          aria-label={t("knowledge.admin.title", "知识库管理")}
        >
          {STATUS_TABS.map((key) => {
            const isActive = status === key;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setStatus(key)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "is-active bg-[var(--theme-sidebar-active)] text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)] hover:text-[var(--theme-text)]"
                }`}
              >
                {t(`knowledge.admin.status.${key}`, key)}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {loading ? (
          <PanelLoadingState text={t("common.loading", "加载中...")} />
        ) : items.length === 0 ? (
          <p className="py-16 text-center text-sm text-stone-500 dark:text-stone-400">
            {t("knowledge.admin.empty", "没有符合条件的知识库")}
          </p>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)]">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[40rem] text-left text-sm">
                <thead className="bg-[var(--theme-bg-subtle)] text-xs text-[var(--theme-text-secondary)]">
                  <tr>
                    <th className="px-3 py-2.5 font-medium">{t("knowledge.admin.colName", "知识库")}</th>
                    <th className="px-3 py-2.5 font-medium">{t("knowledge.admin.colOwner", "所有者")}</th>
                    <th className="hidden px-3 py-2.5 font-medium md:table-cell">
                      {t("knowledge.admin.colDocs", "文档")}
                    </th>
                    <th className="hidden px-3 py-2.5 font-medium lg:table-cell">
                      {t("knowledge.admin.colPublished", "发布时间")}
                    </th>
                    <th className="px-3 py-2.5 text-right font-medium">
                      {t("knowledge.docs.colActions", "操作")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => {
                    const busy = busyId === item.id;
                    return (
                      <tr key={item.id} className="border-t border-[var(--theme-border)]">
                        <td className="max-w-[16rem] px-3 py-2.5">
                          <div className="truncate font-medium text-[var(--theme-text)]" title={item.name}>
                            {item.name}
                          </div>
                          {item.review?.reason && (
                            <div className="truncate text-[11px] text-[var(--theme-text-tertiary)]">
                              {item.review.reason}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-[var(--theme-text-secondary)]">
                          {item.owner_username}
                        </td>
                        <td className="hidden px-3 py-2.5 text-[var(--theme-text-secondary)] md:table-cell">
                          {item.stats.doc_count}
                        </td>
                        <td className="hidden px-3 py-2.5 text-[var(--theme-text-secondary)] lg:table-cell">
                          {item.published_at ? formatDateTimeShort(item.published_at) : "–"}
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => setViewing(item)}
                              className={`${OUTLINE_BTN} !px-2 !py-1 text-xs`}
                            >
                              <Eye size={14} />
                              {t("knowledge.admin.view", "查看")}
                            </button>
                            {(status === "pending" || status === "delisted") && (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void approve(item)}
                                className={`${PRIMARY_BTN} !px-2 !py-1 text-xs`}
                              >
                                {busy && <Loader2 size={12} className="animate-spin" />}
                                {t("knowledge.admin.approve", "通过")}
                              </button>
                            )}
                            {status === "pending" && (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => openReason("reject", item)}
                                className={`${DANGER_BTN} !px-2 !py-1 text-xs`}
                              >
                                {t("knowledge.admin.reject", "驳回")}
                              </button>
                            )}
                            {status === "approved" && (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => openReason("delist", item)}
                                className={`${DANGER_BTN} !px-2 !py-1 text-xs`}
                              >
                                {t("knowledge.admin.delist", "下架")}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {total > ADMIN_PAGE_SIZE && (
          <div className="mt-3">
            <Pagination page={page} pageSize={ADMIN_PAGE_SIZE} total={total} onChange={setPage} />
          </div>
        )}
      </div>

      {reasonAction && (
        <ModalShell
          title={
            reasonAction.kind === "reject"
              ? t("knowledge.admin.rejectTitle", "驳回公开申请")
              : t("knowledge.admin.delistTitle", "下架知识库")
          }
          onClose={() => setReasonAction(null)}
        >
          <div className="space-y-3 px-5 py-4">
            <p className="text-sm text-stone-600 dark:text-stone-300">
              {reasonAction.item.name}
            </p>
            <label className="text-sm font-medium text-stone-800 dark:text-stone-100">
              {t("knowledge.admin.reasonLabel", "原因（必填，会通知所有者）")}
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              className={INPUT_CLS}
            />
          </div>
          <div className="flex justify-end gap-2 border-t border-stone-100 px-5 py-4 dark:border-stone-800">
            <button type="button" onClick={() => setReasonAction(null)} className={GHOST_BTN}>
              {t("common.cancel", "取消")}
            </button>
            <button
              type="button"
              onClick={() => void submitReason()}
              disabled={reason.trim() === "" || busyId === reasonAction.item.id}
              className={PRIMARY_BTN}
            >
              {reasonAction.kind === "reject"
                ? t("knowledge.admin.reject", "驳回")
                : t("knowledge.admin.delist", "下架")}
            </button>
          </div>
        </ModalShell>
      )}

      {viewing && (
        <ModalShell title={viewing.name} onClose={() => setViewing(null)}>
          <dl className="space-y-2 overflow-y-auto px-5 py-4 text-sm">
            {[
              [t("knowledge.admin.colOwner", "所有者"), viewing.owner_username],
              [
                t("knowledge.admin.colDocs", "文档"),
                `${viewing.stats.doc_count} / ${formatFileSize(viewing.stats.size_bytes)}`,
              ],
              [
                t("knowledge.admin.colPublished", "发布时间"),
                viewing.published_at ? formatDateTimeShort(viewing.published_at) : "–",
              ],
              [
                t("knowledge.admin.colStatus", "状态"),
                t(`knowledge.publicStatus.${viewing.public_status}`, viewing.public_status),
              ],
              ...(viewing.review?.reason
                ? [[t("knowledge.admin.colReason", "审核原因"), viewing.review.reason]]
                : []),
            ].map(([label, value]) => (
              <div key={label} className="flex gap-3">
                <dt className="w-20 shrink-0 text-stone-500 dark:text-stone-400">{label}</dt>
                <dd className="min-w-0 flex-1 break-words text-stone-900 dark:text-stone-100">
                  {value}
                </dd>
              </div>
            ))}
            <div>
              <dt className="text-stone-500 dark:text-stone-400">
                {t("knowledge.admin.colDescription", "描述")}
              </dt>
              <dd className="mt-1 whitespace-pre-wrap break-words text-stone-900 dark:text-stone-100">
                {viewing.description || t("knowledge.card.noDescription", "暂无描述")}
              </dd>
            </div>
          </dl>
        </ModalShell>
      )}
    </div>
  );
}
