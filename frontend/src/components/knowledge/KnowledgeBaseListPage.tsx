import { useAuth } from "../../hooks/useAuth";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { Globe2, LibraryBig, Plus, Users } from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
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
import { PRIMARY_BTN } from "./knowledgeStyles";
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
  icon: typeof LibraryBig;
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
    { key: "mine", label: t("knowledge.tabs.mine", "我的知识库"), icon: LibraryBig },
    { key: "shared", label: t("knowledge.tabs.shared", "共享知识库"), icon: Users },
    { key: "plaza", label: t("knowledge.tabs.plaza", "知识广场"), icon: Globe2 },
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

  const changeTab = (next: KbListTab) =>
    updateParams((params) => {
      if (next === "mine") params.delete("tab");
      else params.set("tab", next);
      params.delete("sub");
    });

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

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PanelHeader
        title={t("knowledge.title", "知识库")}
        subtitle={t("knowledge.subtitle", "把文档整理成知识库，让昆小智基于你的资料回答问题")}
        icon={<LibraryBig />}
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder={t("knowledge.searchPlaceholder", "搜索知识库名称或描述")}
        actions={
          <div className="flex items-center gap-2">
            <KnowledgeNotificationsBell onOpenKb={(id) => navigate(`/knowledge/${id}`)} />
            <button
              type="button"
              onClick={openCreate}
              disabled={quotaReached}
              title={
                quotaReached
                  ? t("knowledge.quota.countReached", "知识库数量已达上限")
                  : undefined
              }
              className={PRIMARY_BTN}
            >
              <Plus size={16} />
              {t("knowledge.create", "新建知识库")}
            </button>
          </div>
        }
      />

      {quota && <KnowledgeQuotaSummary quota={quota} className="mx-4 mb-3 sm:mx-6" />}

      {config && !config.ready && <KnowledgeNotReadyNotice className="mx-4 mb-3 sm:mx-6" />}

      <div className="flex shrink-0 items-center border-b border-[var(--theme-border)] px-4 py-2">
        <div
          className="inline-flex items-center gap-1 rounded-lg bg-[var(--theme-bg-subtle)] p-1"
          role="tablist"
          aria-label={t("knowledge.title", "知识库")}
        >
          {tabEntries.map(({ key, label, icon: Icon }) => {
            const isActive = activeTab === key;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => changeTab(key)}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "is-active bg-[var(--theme-sidebar-active)] text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)] hover:text-[var(--theme-text)]"
                }`}
              >
                <Icon size={16} />
                <span>{label}</span>
              </button>
            );
          })}
        </div>
        {activeTab === "shared" && (
          <div className="ml-3 inline-flex items-center gap-1" role="group">
            {KB_SHARED_FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={sharedFilter === f}
                onClick={() => changeSharedFilter(f)}
                className={`rounded-full px-2.5 py-1 text-xs ${
                  sharedFilter === f
                    ? "bg-[var(--theme-sidebar-active)] font-medium text-[var(--theme-text)]"
                    : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)]"
                }`}
              >
                {t(`knowledge.sharedFilter.${f}`, f)}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-6">
        {loading ? (
          <PanelLoadingState text={t("common.loading", "加载中...")} />
        ) : filtered.length === 0 ? (
          <div className="mx-auto mt-6 max-w-xl rounded-3xl border border-stone-200 p-6 text-center dark:border-stone-800">
            <LibraryBig className="mx-auto mb-3 text-stone-400" size={36} />
            <h3 className="text-lg font-semibold text-stone-900 dark:text-stone-100">
              {items.length === 0
                ? activeTab === "mine"
                  ? t("knowledge.emptyTitle", "还没有知识库")
                  : activeTab === "shared"
                    ? t("knowledge.emptyShared", "还没有共享或订阅的知识库")
                    : t("knowledge.emptyPlaza", "知识广场暂无内容")
                : t("knowledge.noMatchTitle", "没有找到匹配的知识库")}
            </h3>
            <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
              {items.length === 0 && activeTab !== "mine"
                ? t("knowledge.emptyOtherDesc", "有人分享或公开知识库后会出现在这里。")
                : items.length === 0
                ? t(
                    "knowledge.emptyDesc",
                    "新建一个知识库并上传文档，系统会自动解析、切分并建立检索索引。",
                  )
                : t("knowledge.noMatchDesc", "换个关键词试试。")}
            </p>
            {items.length === 0 && activeTab === "mine" && (
              <button
                type="button"
                onClick={openCreate}
                disabled={quotaReached}
                className={`${PRIMARY_BTN} mt-4`}
              >
                <Plus size={16} />
                {t("knowledge.create", "新建知识库")}
              </button>
            )}
          </div>
        ) : (
          <div className="auto-grid-cols grid gap-4 sm:gap-5">
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
