import { useAuth } from "../../hooks/useAuth";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import {
  AlertTriangle,
  ArrowLeft,
  BellOff,
  CheckCircle2,
  FileText,
  HardDrive,
  Layers,
  LibraryBig,
  Loader2,
  LogOut,
  Network,
  Search,
  Settings2,
  Share2,
  type LucideIcon,
} from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { formatFileSize } from "../documents/utils/fileSize";
import { useWebSocket, type KbEventNotification } from "../../hooks/useWebSocket";
import {
  knowledgeBaseApi,
  type KBDocument,
  type KBGraphMention,
  type KBSearchHit,
} from "../../services/api/knowledgeBase";
import { subscribeKnowledgeBaseRefresh } from "../../utils/knowledgeBaseEvents";
import { KbChatToggle } from "./KbChatToggle";
import { KnowledgeActionMenu, type ActionMenuItem } from "./KnowledgeActionMenu";
import {
  DocumentDetailSidebar,
  type DocumentFocus,
  type DocumentSidebarTab,
} from "./DocumentDetailSidebar";
import { DocumentsTab } from "./DocumentsTab";
import { useKbConfig } from "./hooks/useKbConfig";
import { useKbDetail } from "./hooks/useKbDetail";
import { KnowledgeDisabledState, KnowledgeNotFoundState, KnowledgeNotReadyNotice } from "./KnowledgeNotices";
import { GraphTab } from "./graph/GraphTab";
import { RetrievalTestTab } from "./RetrievalTestTab";
import { SettingsTab } from "./SettingsTab";
import { KnowledgePublicStatusPill } from "./KnowledgePublicStatusPill";
import { KnowledgeStatusPill } from "./KnowledgeStatusPill";
import { KnowledgeShareDialog } from "./KnowledgeShareDialog";
import { ICON_OUTLINE_BTN, OUTLINE_BTN, PRIMARY_BTN } from "./knowledgeStyles";
import {
  POST_ACTION_REFRESH_MS,
  getKbCapabilities,
  resolveKbDetailTab,
  type KbDetailTab,
} from "./knowledgeUtils";

interface ViewingState {
  doc: KBDocument;
  focus: DocumentFocus | null;
  tab: DocumentSidebarTab;
}

interface StatItem {
  key: string;
  text: string;
  icon: LucideIcon;
  tone?: "info" | "danger";
}

const STAT_TONE_CLASS = {
  info: "font-medium text-sky-700 dark:text-sky-300",
  danger: "font-medium text-red-600 dark:text-red-400",
} as const;

interface KnowledgeBaseDetailProps {
  kbId: string;
}

export function KnowledgeBaseDetail({ kbId }: KnowledgeBaseDetailProps) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = resolveKbDetailTab(searchParams.get("tab"));
  const detail = useKbDetail(kbId);
  const { config } = useKbConfig();
  const { kb, setKb, loading, loadError, scheduleRefresh, refresh } = detail;
  const [viewing, setViewing] = useState<ViewingState | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [roleBusy, setRoleBusy] = useState(false);

  const onKbEvent = useCallback(
    (event: KbEventNotification) => {
      const eventKbId = event.data?.kb_id;
      if (!eventKbId || eventKbId === kbId) scheduleRefresh(300);
    },
    [kbId, scheduleRefresh],
  );
  useWebSocket({ enabled: true, onKbEvent });

  useEffect(
    () => subscribeKnowledgeBaseRefresh(() => scheduleRefresh(0), { kbId }),
    [kbId, scheduleRefresh],
  );

  const changeTab = useCallback(
    (next: KbDetailTab) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          if (next === "documents") params.delete("tab");
          else params.set("tab", next);
          return params;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const openHit = useCallback(
    async (hit: KBSearchHit) => {
      try {
        const doc = await knowledgeBaseApi.getDocument(kbId, hit.doc_id);
        setViewing({
          doc,
          focus: { startLine: hit.start_line, endLine: hit.end_line },
          tab: "content",
        });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : String(e));
      }
    },
    [kbId],
  );

  const openMention = useCallback(
    async (mention: KBGraphMention) => {
      try {
        const doc = await knowledgeBaseApi.getDocument(kbId, mention.doc_id);
        setViewing({
          doc,
          focus:
            mention.start_line != null && mention.end_line != null
              ? { startLine: mention.start_line, endLine: mention.end_line }
              : null,
          tab: "content",
        });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : String(e));
      }
    },
    [kbId],
  );

  if (loadError === "disabled") return <KnowledgeDisabledState />;
  if (loadError === "notFound") {
    return <KnowledgeNotFoundState onBack={() => navigate("/knowledge")} />;
  }
  if (!kb) {
    return loading ? (
      <PanelLoadingState text={t("common.loading", "加载中...")} />
    ) : (
      <div className="flex h-full items-center justify-center px-6">
        <div className="text-center">
          <p className="text-sm text-stone-500 dark:text-stone-400">
            {t("knowledge.loadFailed", "知识库加载失败")}
          </p>
          <button type="button" onClick={() => void refresh()} className={`${PRIMARY_BTN} mt-3`}>
            {t("knowledge.retryLoad", "重新加载")}
          </button>
        </div>
      </div>
    );
  }

  const { stats } = kb;
  const caps = getKbCapabilities(kb.my_role, kb);

  const runRoleAction = async (action: "subscribe" | "unsubscribe" | "leave") => {
    setRoleBusy(true);
    try {
      if (action === "subscribe") {
        await knowledgeBaseApi.subscribe(kbId);
        toast.success(t("knowledge.subscribe.subscribed", "已订阅"));
        await refresh();
      } else {
        if (action === "unsubscribe") await knowledgeBaseApi.unsubscribe(kbId);
        else await knowledgeBaseApi.leave(kbId, user?.id ?? "");
        toast.success(
          action === "unsubscribe"
            ? t("knowledge.subscribe.unsubscribed", "已取消订阅")
            : t("knowledge.share.left", "已退出共享"),
        );
        navigate("/knowledge");
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setRoleBusy(false);
      setLeaveOpen(false);
    }
  };

  const statItems: StatItem[] = [
    {
      key: "docs",
      icon: FileText,
      text: t("knowledge.stats.docs", { count: stats.doc_count, defaultValue: "{{count}} 个文档" }),
    },
    {
      key: "indexed",
      icon: CheckCircle2,
      text: t("knowledge.stats.indexed", { count: stats.indexed_count, defaultValue: "{{count}} 已完成" }),
    },
  ];
  if (stats.processing_count > 0) {
    statItems.push({
      key: "processing",
      icon: Loader2,
      tone: "info",
      text: t("knowledge.stats.processing", {
        count: stats.processing_count,
        defaultValue: "{{count}} 处理中",
      }),
    });
  }
  if (stats.failed_count > 0) {
    statItems.push({
      key: "failed",
      icon: AlertTriangle,
      tone: "danger",
      text: t("knowledge.stats.failed", { count: stats.failed_count, defaultValue: "{{count}} 失败" }),
    });
  }
  statItems.push(
    {
      key: "chunks",
      icon: Layers,
      text: t("knowledge.stats.chunks", { count: stats.chunk_count, defaultValue: "{{count}} 个分块" }),
    },
    { key: "size", icon: HardDrive, text: formatFileSize(stats.size_bytes) },
  );

  const moreItems: ActionMenuItem[] = [];
  if (caps.canUnsubscribe) {
    moreItems.push({
      key: "unsubscribe",
      label: t("knowledge.subscribe.unsubscribe", "取消订阅"),
      icon: BellOff,
      onSelect: () => void runRoleAction("unsubscribe"),
    });
  }
  if (caps.canLeave) {
    moreItems.push({
      key: "leave",
      label: t("knowledge.share.leave", "退出共享"),
      icon: LogOut,
      danger: true,
      onSelect: () => setLeaveOpen(true),
    });
  }

  const showGraphTab = config?.allow_graph !== false;
  const tabEntries: Array<{ key: KbDetailTab; label: string; icon: typeof FileText }> = [
    { key: "documents", label: t("knowledge.tabs.documents", "文档"), icon: FileText },
    { key: "search", label: t("knowledge.tabs.search", "检索测试"), icon: Search },
    ...(showGraphTab
      ? [{ key: "graph" as const, label: t("knowledge.tabs.graph", "图谱"), icon: Network }]
      : []),
    ...(caps.canManageSettings
      ? [{ key: "settings" as const, label: t("knowledge.tabs.settings", "设置"), icon: Settings2 }]
      : []),
  ];
  const activeTab: KbDetailTab =
    (tab === "settings" && !caps.canManageSettings) || (tab === "graph" && !showGraphTab)
      ? "documents"
      : tab;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 px-4 pt-3 sm:px-6 lg:px-8">
        <button
          type="button"
          onClick={() => navigate("/knowledge")}
          className="inline-flex items-center gap-1 rounded-lg py-1 pr-2 text-sm text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)]"
        >
          <ArrowLeft size={16} />
          {t("knowledge.backToList", "返回知识库列表")}
        </button>
      </div>

      <PanelHeader
        title={kb.name}
        subtitle={kb.description || undefined}
        icon={<LibraryBig />}
        actions={
          <div className="flex items-center gap-2">
            <KbChatToggle kb={kb} onChange={setKb} />
            {caps.canShare && (
              <button type="button" onClick={() => setShareOpen(true)} className={OUTLINE_BTN}>
                <Share2 size={16} />
                {t("knowledge.share.button", "分享")}
              </button>
            )}
            {caps.canSubscribe && (
              <button
                type="button"
                disabled={roleBusy}
                onClick={() => void runRoleAction("subscribe")}
                className={PRIMARY_BTN}
              >
                {t("knowledge.subscribe.subscribe", "订阅")}
              </button>
            )}
            {moreItems.length > 0 && (
              <KnowledgeActionMenu
                items={moreItems}
                label={t("knowledge.card.more", "更多操作")}
                buttonClassName={ICON_OUTLINE_BTN}
              />
            )}
          </div>
        }
      >
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="inline-flex items-center gap-1.5 empty:hidden">
            {kb.my_role !== "owner" && (
              <KnowledgeStatusPill tone="neutral">
                {t(`knowledge.role.${kb.my_role}`, kb.my_role)}
              </KnowledgeStatusPill>
            )}
            <KnowledgePublicStatusPill status={kb.public_status} />
          </span>
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px]">
            {statItems.map(({ key, text, icon: Icon, tone }) => (
              <li
                key={key}
                className={`inline-flex items-center gap-1.5 ${
                  tone ? STAT_TONE_CLASS[tone] : "text-[var(--theme-text-secondary)]"
                }`}
              >
                <Icon
                  size={14}
                  className={tone === "info" ? "animate-spin" : undefined}
                  aria-hidden="true"
                />
                {text}
              </li>
            ))}
          </ul>
        </div>
        {kb.chat_enabled != null && (
          <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">
            {t("knowledge.chat.hint", "在对话中输入 @ 可临时指定知识库")}
          </p>
        )}
      </PanelHeader>

      {config && !config.ready && <KnowledgeNotReadyNotice className="mx-4 mt-3 sm:mx-6" />}

      <div className="flex shrink-0 items-center border-b border-[var(--theme-border)] px-4 py-2">
        <div
          className="inline-flex items-center gap-1 rounded-lg bg-[var(--theme-bg-subtle)] p-1"
          role="tablist"
          aria-label={kb.name}
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
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {activeTab === "documents" && (
          <DocumentsTab
            kbId={kbId}
            detail={detail}
            config={config}
            readOnly={!caps.canWriteDocs}
            onOpenDocument={(doc, docTab = "content") =>
              setViewing({ doc, focus: null, tab: docTab })
            }
          />
        )}
        {activeTab === "search" && (
          <RetrievalTestTab kb={kb} config={config} onOpenHit={(hit) => void openHit(hit)} />
        )}
        {activeTab === "graph" && (
          <GraphTab
            kb={kb}
            config={config}
            canManage={kb.can_manage}
            onChanged={() => scheduleRefresh(POST_ACTION_REFRESH_MS)}
            onOpenMention={(mention) => void openMention(mention)}
          />
        )}
        {activeTab === "settings" && (
          <SettingsTab
            kb={kb}
            config={config}
            onSaved={setKb}
            onReindexQueued={() => scheduleRefresh(POST_ACTION_REFRESH_MS)}
          />
        )}
      </div>

      {shareOpen && (
        <KnowledgeShareDialog
          kb={kb}
          config={config}
          onClose={() => setShareOpen(false)}
          onKbChange={setKb}
        />
      )}

      <ConfirmDialog
        isOpen={leaveOpen}
        title={t("knowledge.share.leaveTitle", "退出共享")}
        message={t("knowledge.share.leaveConfirm", {
          name: kb.name,
          defaultValue: "确定退出“{{name}}”吗？退出后将无法再访问这个知识库。",
        })}
        confirmText={t("knowledge.share.leave", "退出共享")}
        variant="danger"
        loading={roleBusy}
        onConfirm={() => void runRoleAction("leave")}
        onCancel={() => setLeaveOpen(false)}
      />

      {viewing && (
        <DocumentDetailSidebar
          key={`${viewing.doc.id}:${viewing.focus?.startLine ?? 0}`}
          kbId={kbId}
          doc={viewing.doc}
          focus={viewing.focus}
          initialTab={viewing.tab}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}
