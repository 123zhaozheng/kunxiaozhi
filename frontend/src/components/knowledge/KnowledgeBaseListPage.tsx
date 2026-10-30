import { useAuth } from "../../hooks/useAuth";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { LibraryBig, Plus, Search, X } from "lucide-react";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { Pagination } from "../common/Pagination";
import { knowledgeBaseApi, type KnowledgeBase } from "../../services/api/knowledgeBase";
import { dispatchKnowledgeBaseRefresh } from "../../utils/knowledgeBaseEvents";
import { useKbConfig } from "./hooks/useKbConfig";
import { useKbQuota } from "./hooks/useKbQuota";
import { useKnowledgeBases } from "./hooks/useKnowledgeBases";
import { KnowledgeBaseCard } from "./KnowledgeBaseCard";
import { KnowledgeBaseDialog } from "./KnowledgeBaseDialog";
import { KnowledgeDisabledState, KnowledgeNotReadyNotice } from "./KnowledgeNotices";
import { KnowledgeNotificationsBell } from "./KnowledgeNotificationsDialog";
import { KnowledgeQuotaSummary } from "./KnowledgeQuotaSummary";
import { KnowledgeShareDialog } from "./KnowledgeShareDialog";
import { PRIMARY_BTN, PRIMARY_BTN_SM } from "./knowledgeStyles";
import {
  KB_SHARED_FILTERS,
  PLAZA_PAGE_SIZE,
  isKbCountQuotaReached,
  resolveKbListTab,
  resolveKbSharedFilter,
  type KbListTab,
} from "./knowledgeUtils";

interface ListTabEntry {
  key: KbListTab;
  label: string;
}

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function KnowledgeBaseListPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const { config, error: configError } = useKbConfig();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = resolveKbListTab(searchParams.get("tab"));
  const sharedFilter = resolveKbSharedFilter(searchParams.get("sub"));
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const plazaQuery = useDebounced(search.trim(), 300);
  const { items, setItems, total, loading, error, reload } = useKnowledgeBases({
    tab: activeTab,
    sharedFilter,
    query: plazaQuery,
    page,
  });
  const { quota } = useKbQuota();
  useEffect(() => setPage(1), [plazaQuery]);
  const [dialog, setDialog] = useState<{ kb: KnowledgeBase | null } | null>(null);
  const [sharing, setSharing] = useState<KnowledgeBase | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeBase | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [leaving, setLeaving] = useState<KnowledgeBase | null>(null);
  const [busyKbId, setBusyKbId] = useState<string | null>(null);

  const tabEntries: ListTabEntry[] = [
    { key: "mine", label: t("knowledge.tabs.mine", "我的知识库") },
    { key: "shared", label: t("knowledge.tabs.shared", "共享知识库") },
    { key: "plaza", label: t("knowledge.tabs.plaza", "知识广场") },
  ];

  const updateParams = (mutate: (params: URLSearchParams) => void) => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        mutate(params);
        return params;
      },
      { replace: true },
    );
    setPage(1);
  };

  const changeTab = (next: KbListTab) => {
    setSearch("");
    updateParams((params) => {
      if (next === "mine") params.delete("tab");
      else params.set("tab", next);
      params.delete("sub");
    });
  };

  const changeSharedFilter = (next: (typeof KB_SHARED_FILTERS)[number]) =>
    updateParams((params) => {
      if (next === "all") params.delete("sub");
      else params.set("sub", next);
    });

  const quotaReached = isKbCountQuotaReached(quota);

  // Plaza is searched server-side; other tabs filter the loaded list locally.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q || activeTab === "plaza") return items;
    return items.filter(
      (kb) =>
        kb.name.toLowerCase().includes(q) || kb.description.toLowerCase().includes(q),
    );
  }, [items, search, activeTab]);

  const isDisabled =
    config?.enabled === false ||
    (error as { code?: string } | null)?.code === "kb_disabled" ||
    (configError as { code?: string } | null)?.code === "kb_disabled";
  if (isDisabled) return <KnowledgeDisabledState />;

  const confirmDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await knowledgeBaseApi.remove(deleting.id);
      setItems((prev) => prev.filter((kb) => kb.id !== deleting.id));
      dispatchKnowledgeBaseRefresh();
      toast.success(t("knowledge.toast.deleted", "知识库已删除"));
      setDeleting(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteBusy(false);
    }
  };

  const runSubscription = async (kb: KnowledgeBase, action: "subscribe" | "unsubscribe") => {
    setBusyKbId(kb.id);
    try {
      if (action === "subscribe") await knowledgeBaseApi.subscribe(kb.id);
      else await knowledgeBaseApi.unsubscribe(kb.id);
      toast.success(
        action === "subscribe"
          ? t("knowledge.subscribe.subscribed", "已订阅")
          : t("knowledge.subscribe.unsubscribed", "已取消订阅"),
      );
      void reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyKbId(null);
    }
  };

  const confirmLeave = async () => {
    if (!leaving) return;
    setBusyKbId(leaving.id);
    try {
      await knowledgeBaseApi.leave(leaving.id, user?.id ?? "");
      setItems((prev) => prev.filter((kb) => kb.id !== leaving.id));
      toast.success(t("knowledge.share.left", "已退出共享"));
      setLeaving(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyKbId(null);
    }
  };

  const openCreate = () => setDialog({ kb: null });
  const clearFilters = () => {
    setSearch("");
    if (sharedFilter !== "all") changeSharedFilter("all");
  };

  const query = search.trim();
  const hasFilter = query !== "" || (activeTab === "shared" && sharedFilter !== "all");
  // First-run (nothing at all, no filter): hide search so the empty state stays simple.
  const showSearch = query !== "" || items.length > 0;

  const empty = (() => {
    if (hasFilter) {
      return {
        title: query
          ? t("knowledge.noMatchQuery", {
              query,
              defaultValue: "没有找到与“{{query}}”相关的知识库",
            })
          : t("knowledge.noMatchTitle", "没有找到匹配的知识库"),
        desc: t("knowledge.noMatchDesc", "换个关键词，或清除筛选查看全部。"),
        clear: true,
        create: false,
      };
    }
    if (activeTab === "mine") {
      return {
        title: t("knowledge.emptyTitle", "还没有知识库"),
        desc: t("knowledge.emptyDesc", "新建一个知识库，开始整理你的第一份资料。"),
        clear: false,
        create: true,
      };
    }
    if (activeTab === "shared") {
      return {
        title: t("knowledge.emptyShared", "还没有共享给你的知识库"),
        desc: t("knowledge.emptyOtherDesc", "别人分享给你或你订阅的知识库会出现在这里。"),
        clear: false,
        create: false,
      };
    }
    return {
      title: t("knowledge.emptyPlaza", "知识广场暂无内容"),
      desc: t("knowledge.emptyPlazaDesc", "有人公开知识库后会出现在这里。"),
      clear: false,
      create: false,
    };
  })();

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b border-[var(--theme-border)] px-4 sm:px-6">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
          <div
            className="-mb-2 flex items-center gap-5 self-stretch"
            role="tablist"
            aria-label={t("knowledge.title", "知识库")}
          >
            {tabEntries.map(({ key, label }) => {
              const isActive = activeTab === key;
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => changeTab(key)}
                  className={`relative flex h-11 items-center text-sm transition-colors ${
                    isActive
                      ? "is-active font-semibold text-[var(--theme-text)] after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:rounded-full after:bg-[var(--theme-text)]"
                      : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <div className="ml-auto flex min-w-0 items-center gap-2">
            {showSearch && (
              <label className="relative block w-full min-w-0 sm:w-[280px]">
                <Search
                  size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-tertiary)]"
                />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={
                    activeTab === "shared"
                      ? t("knowledge.searchShared", "搜索共享知识库")
                      : t("knowledge.searchPlaceholder", "搜索知识库")
                  }
                  aria-label={t("knowledge.searchPlaceholder", "搜索知识库")}
                  className="h-9 w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] pl-8 pr-8 text-sm text-[var(--theme-text)] placeholder:text-[var(--theme-text-tertiary)] focus:outline-none focus:ring-2 focus:ring-[var(--theme-ring)] [&::-webkit-search-cancel-button]:hidden"
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => setSearch("")}
                    aria-label={t("knowledge.clearSearch", "清除搜索")}
                    className="absolute right-2 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-[var(--theme-text-tertiary)] hover:text-[var(--theme-text)]"
                  >
                    <X size={14} />
                  </button>
                )}
              </label>
            )}
            <KnowledgeNotificationsBell onOpenKb={(id) => navigate(`/knowledge/${id}`)} />
            {activeTab === "mine" && (
              <button
                type="button"
                onClick={openCreate}
                disabled={quotaReached}
                title={
                  quotaReached
                    ? t("knowledge.quota.countReached", "知识库数量已达上限")
                    : undefined
                }
                className={`${PRIMARY_BTN_SM} shrink-0`}
              >
                <Plus size={16} />
                {t("knowledge.create", "新建知识库")}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
        {activeTab === "mine" && config && !config.ready && (
          <KnowledgeNotReadyNotice className="mb-4" />
        )}

        {activeTab === "shared" && (
          <div
            className="mb-4 inline-flex flex-wrap items-center gap-1 rounded-lg bg-[var(--theme-bg-subtle)] p-1"
            role="group"
          >
            {KB_SHARED_FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={sharedFilter === f}
                onClick={() => changeSharedFilter(f)}
                className={`rounded-md px-3 py-1 text-[13px] font-medium transition-colors ${
                  sharedFilter === f
                    ? "bg-[var(--theme-sidebar-active)] text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)] hover:text-[var(--theme-text)]"
                }`}
              >
                {t(`knowledge.sharedFilter.${f}`, f)}
              </button>
            ))}
          </div>
        )}

        {loading ? (
          <PanelLoadingState text={t("common.loading", "加载中...")} />
        ) : filtered.length === 0 ? (
          <div className="mx-auto flex max-w-md flex-col items-center pt-16 text-center sm:pt-24">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)]">
              <LibraryBig className="text-[var(--theme-text-secondary)]" size={26} />
            </div>
            <h3 className="text-[15px] font-semibold text-[var(--theme-text)]">{empty.title}</h3>
            <p className="mt-1 text-[13px] text-[var(--theme-text-secondary)]">{empty.desc}</p>
            {empty.create && (
              <button
                type="button"
                onClick={openCreate}
                disabled={quotaReached}
                className={`${PRIMARY_BTN} mt-5`}
              >
                <Plus size={16} />
                {t("knowledge.create", "新建知识库")}
              </button>
            )}
            {empty.clear && (
              <button
                type="button"
                onClick={clearFilters}
                className="mt-3 text-[13px] font-medium text-[var(--theme-text)] underline-offset-4 hover:underline"
              >
                {t("knowledge.clearFilters", "清除筛选")}
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
            {filtered.map((kb) => (
              <KnowledgeBaseCard
                key={kb.id}
                kb={kb}
                onOpen={() => navigate(`/knowledge/${kb.id}`)}
                onEdit={() => setDialog({ kb })}
                onDelete={() => setDeleting(kb)}
                onShare={() => setSharing(kb)}
                onLeave={() => setLeaving(kb)}
                onSubscribe={() => void runSubscription(kb, "subscribe")}
                onUnsubscribe={() => void runSubscription(kb, "unsubscribe")}
                subscribeBusy={busyKbId === kb.id}
                onChatEnabledChange={(next) =>
                  setItems((prev) => prev.map((item) => (item.id === next.id ? next : item)))
                }
              />
            ))}
          </div>
        )}

        {activeTab === "plaza" && total > PLAZA_PAGE_SIZE && (
          <div className="mt-4">
            <Pagination
              page={page}
              pageSize={PLAZA_PAGE_SIZE}
              total={total}
              onChange={setPage}
            />
          </div>
        )}
      </div>

      {activeTab === "mine" && quota && (
        <KnowledgeQuotaSummary
          quota={quota}
          className="shrink-0 px-4 py-3 sm:px-6"
        />
      )}

      {dialog && (
        <KnowledgeBaseDialog
          kb={dialog.kb}
          config={config}
          onClose={() => setDialog(null)}
          onSaved={(saved) => {
            setDialog(null);
            if (dialog.kb) {
              setItems((prev) => prev.map((kb) => (kb.id === saved.id ? saved : kb)));
            } else {
              void reload();
              navigate(`/knowledge/${saved.id}`);
            }
          }}
        />
      )}

      {sharing && (
        <KnowledgeShareDialog
          kb={sharing}
          config={config}
          onClose={() => setSharing(null)}
          onKbChange={(next) => {
            setSharing(next);
            setItems((prev) => prev.map((kb) => (kb.id === next.id ? next : kb)));
          }}
        />
      )}

      <ConfirmDialog
        isOpen={!!leaving}
        title={t("knowledge.share.leaveTitle", "退出共享")}
        message={t("knowledge.share.leaveConfirm", {
          name: leaving?.name ?? "",
          defaultValue: "确定退出“{{name}}”吗？退出后将无法再访问这个知识库。",
        })}
        confirmText={t("knowledge.share.leave", "退出共享")}
        variant="danger"
        loading={busyKbId === leaving?.id}
        onConfirm={() => void confirmLeave()}
        onCancel={() => setLeaving(null)}
      />

      <ConfirmDialog
        isOpen={!!deleting}
        title={t("knowledge.deleteTitle", "删除知识库")}
        message={t("knowledge.deleteConfirm", {
          name: deleting?.name ?? "",
          defaultValue:
            "确定删除“{{name}}”吗？库内所有文档、分块和检索索引都会被永久删除，此操作无法撤销。",
        })}
        confirmText={t("common.delete", "删除")}
        variant="danger"
        loading={deleteBusy}
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
