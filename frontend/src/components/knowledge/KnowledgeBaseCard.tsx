import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  Clock,
  FileText,
  Loader2,
  LibraryBig,
  LogOut,
  MessageSquare,
  MessageSquareOff,
  Pencil,
  Share2,
  Trash2,
} from "lucide-react";
import { knowledgeBaseApi, type KnowledgeBase } from "../../services/api/knowledgeBase";
import { formatTimeAgo } from "../../utils/datetime";
import { KnowledgeActionMenu, type ActionMenuItem } from "./KnowledgeActionMenu";
import { KnowledgeStatusPill } from "./KnowledgeStatusPill";
import { OUTLINE_BTN_SM } from "./knowledgeStyles";
import {
  getKbCapabilities,
  getPublicStatusMeta,
  isKbProcessing,
  type StatusTone,
} from "./knowledgeUtils";

/** Cards stay compact: only the most important states are shown inline. */
const MAX_CARD_PILLS = 2;

interface KnowledgeBaseCardProps {
  kb: KnowledgeBase;
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onShare?: () => void;
  onLeave?: () => void;
  onSubscribe?: () => void;
  onUnsubscribe?: () => void;
  subscribeBusy?: boolean;
  onChatEnabledChange?: (kb: KnowledgeBase) => void;
}

interface CardPill {
  key: string;
  tone: StatusTone;
  text: string;
  spinner?: boolean;
}

export function KnowledgeBaseCard({
  kb,
  onOpen,
  onEdit,
  onDelete,
  onShare,
  onLeave,
  onSubscribe,
  onUnsubscribe,
  subscribeBusy = false,
  onChatEnabledChange,
}: KnowledgeBaseCardProps) {
  const { t } = useTranslation();
  const processing = isKbProcessing(kb);
  const { stats } = kb;
  const caps = getKbCapabilities(kb.my_role, kb);
  const isOwner = kb.my_role === "owner";
  const chatToggleable = !!onChatEnabledChange && kb.chat_enabled != null;

  const toggleChat = async () => {
    try {
      const next = await knowledgeBaseApi.setChatEnabled(kb.id, !kb.chat_enabled);
      onChatEnabledChange?.({ ...kb, ...next, chat_enabled: next.chat_enabled ?? !kb.chat_enabled });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const menuItems: ActionMenuItem[] = [];
  if (chatToggleable) {
    menuItems.push({
      key: "chat",
      label: kb.chat_enabled
        ? t("knowledge.card.disableChat", "对话中停用")
        : t("knowledge.card.enableChat", "对话中启用"),
      icon: kb.chat_enabled ? MessageSquareOff : MessageSquare,
      onSelect: () => void toggleChat(),
    });
  }
  if (caps.canManageSettings) {
    menuItems.push({ key: "edit", label: t("common.edit", "编辑"), icon: Pencil, onSelect: onEdit });
    if (onShare) {
      menuItems.push({
        key: "share",
        label: t("knowledge.share.button", "分享"),
        icon: Share2,
        onSelect: onShare,
      });
    }
    menuItems.push({
      key: "delete",
      label: t("common.delete", "删除"),
      icon: Trash2,
      danger: true,
      onSelect: onDelete,
    });
  } else if (caps.canLeave && onLeave) {
    menuItems.push({
      key: "leave",
      label: t("knowledge.share.leave", "退出共享"),
      icon: LogOut,
      danger: true,
      onSelect: onLeave,
    });
  }

  const showSubscribe =
    (caps.canSubscribe && !!onSubscribe) || (caps.canUnsubscribe && !!onUnsubscribe);

  // Ordered by importance; anything past MAX_CARD_PILLS collapses into a "+N" pill.
  const pills: CardPill[] = [];
  if (stats.failed_count > 0) {
    pills.push({
      key: "failed",
      tone: "danger",
      text: t("knowledge.card.failedCount", {
        count: stats.failed_count,
        defaultValue: "{{count}} 个失败",
      }),
    });
  }
  if (processing) {
    pills.push({
      key: "processing",
      tone: "info",
      text: t("knowledge.card.processing", "处理中"),
      spinner: true,
    });
  }
  if (kb.index_outdated) {
    pills.push({
      key: "outdated",
      tone: "warning",
      text: t("knowledge.card.indexOutdated", "需重建索引"),
    });
  }
  if (!isOwner) {
    pills.push({
      key: "role",
      tone: "neutral",
      text: t(`knowledge.role.${kb.my_role}`, kb.my_role),
    });
  } else {
    const publicMeta = getPublicStatusMeta(kb.public_status);
    if (publicMeta.visible) {
      pills.push({
        key: "public",
        tone: publicMeta.tone,
        text: t(`knowledge.publicStatus.${kb.public_status}`, String(kb.public_status)),
      });
    }
  }
  if (kb.chat_enabled === false) {
    pills.push({
      key: "chatOff",
      tone: "neutral",
      text: t("knowledge.card.chatOff", "对话中未启用"),
    });
  }
  const visiblePills = pills.slice(0, MAX_CARD_PILLS);
  const hiddenPills = pills.slice(MAX_CARD_PILLS);

  return (
    <div className="scb group relative flex h-full cursor-pointer flex-col bg-[var(--theme-bg-card)] p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)]">
          <LibraryBig size={17} className="text-[var(--theme-text-secondary)]" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] font-semibold leading-snug text-[var(--theme-text)]">
            <button
              type="button"
              onClick={onOpen}
              title={kb.name}
              className="block w-full truncate rounded-md text-left after:absolute after:inset-0 after:content-[''] focus:outline-none focus-visible:after:ring-2 focus-visible:after:ring-[var(--theme-ring)]"
            >
              {kb.name}
            </button>
          </h3>
          <p className="mt-0.5 line-clamp-2 text-[13px] leading-relaxed text-[var(--theme-text-secondary)]">
            {kb.description || t("knowledge.card.noDescription", "暂无描述")}
          </p>
        </div>
        {menuItems.length > 0 && (
          <KnowledgeActionMenu items={menuItems} label={t("knowledge.card.more", "更多操作")} />
        )}
      </div>

      {pills.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {visiblePills.map((pill) => (
            <KnowledgeStatusPill
              key={pill.key}
              tone={pill.tone}
              icon={
                pill.spinner ? (
                  <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                ) : undefined
              }
            >
              {pill.text}
            </KnowledgeStatusPill>
          ))}
          {hiddenPills.length > 0 && (
            <KnowledgeStatusPill
              tone="neutral"
              title={hiddenPills.map((pill) => pill.text).join(" · ")}
            >
              {`+${hiddenPills.length}`}
            </KnowledgeStatusPill>
          )}
        </div>
      )}

      <div className="mt-auto flex items-center justify-between gap-2 pt-3 text-xs text-[var(--theme-text-tertiary)]">
        <span className="inline-flex min-w-0 items-center gap-1">
          <FileText size={13} className="shrink-0" aria-hidden="true" />
          {t("knowledge.card.docCount", {
            count: stats.doc_count,
            defaultValue: "{{count}} 个文档",
          })}
          {!isOwner && kb.owner_username && (
            <span className="truncate">
              {" · "}
              {t("knowledge.card.owner", {
                name: kb.owner_username,
                defaultValue: "所有者：{{name}}",
              })}
            </span>
          )}
        </span>
        {showSubscribe ? (
          <button
            type="button"
            disabled={subscribeBusy}
            onClick={() => {
              if (caps.canSubscribe) onSubscribe?.();
              else onUnsubscribe?.();
            }}
            className={`${OUTLINE_BTN_SM} relative z-10 shrink-0`}
          >
            {caps.canSubscribe
              ? t("knowledge.subscribe.subscribe", "订阅")
              : t("knowledge.subscribe.unsubscribe", "取消订阅")}
          </button>
        ) : (
          <span className="inline-flex shrink-0 items-center gap-1">
            <Clock size={13} aria-hidden="true" />
            {formatTimeAgo(t, kb.updated_at)}
          </span>
        )}
      </div>
    </div>
  );
}
