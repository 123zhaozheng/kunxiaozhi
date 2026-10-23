import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
  MoreHorizontal,
  Pencil,
  Share2,
  Trash2,
} from "lucide-react";
import { knowledgeBaseApi, type KnowledgeBase } from "../../services/api/knowledgeBase";
import { formatTimeAgo } from "../../utils/datetime";
import { OUTLINE_BTN } from "./knowledgeStyles";
import { KnowledgePublicStatusPill } from "./KnowledgePublicStatusPill";
import { STATUS_TONE_CLASSES, getKbCapabilities, isKbProcessing } from "./knowledgeUtils";

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

interface CardMenuItem {
  key: string;
  label: string;
  icon: typeof Pencil;
  danger?: boolean;
  onSelect: () => void;
}

function CardMenu({ items, label }: { items: CardMenuItem[]; label: string }) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (!pos) return;
    const close = () => setPos(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [pos]);

  // Keep the menu inside the viewport once its size is known.
  useLayoutEffect(() => {
    const el = menuRef.current;
    if (!pos || !el) return;
    const rect = el.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) {
      el.style.top = `${Math.max(8, window.innerHeight - rect.height - 8)}px`;
    }
  }, [pos]);

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (pos) {
      setPos(null);
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPos({ top: rect.bottom + 4, left: Math.max(8, rect.right - 144) });
  };

  const itemCls =
    "flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-stone-100 dark:hover:bg-stone-800";

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={!!pos}
        className="-mr-1.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] hover:bg-[var(--theme-sidebar-hover)]"
      >
        <MoreHorizontal size={16} />
      </button>
      {pos &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            style={{ top: pos.top, left: pos.left }}
            onClick={(e) => e.stopPropagation()}
            className="fixed z-[999] w-36 overflow-hidden rounded-xl border border-stone-200 bg-white py-1 shadow-lg dark:border-stone-700 dark:bg-stone-900"
          >
            {items.map(({ key, label: itemLabel, icon: Icon, danger, onSelect }) => (
              <button
                key={key}
                type="button"
                role="menuitem"
                className={`${itemCls} ${
                  danger ? "text-red-600 dark:text-red-400" : "text-stone-700 dark:text-stone-200"
                }`}
                onClick={() => {
                  setPos(null);
                  onSelect();
                }}
              >
                <Icon size={14} />
                {itemLabel}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
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

  const menuItems: CardMenuItem[] = [];
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
  const pills = (
    <>
      {processing && (
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_TONE_CLASSES.info}`}
        >
          <Loader2 size={10} className="animate-spin" aria-hidden="true" />
          {t("knowledge.card.processing", "处理中")}
        </span>
      )}
      {stats.failed_count > 0 && (
        <span className="scb__status-pill scb__status-pill--danger">
          {t("knowledge.card.failedCount", {
            count: stats.failed_count,
            defaultValue: "{{count}} 个失败",
          })}
        </span>
      )}
      {kb.index_outdated && (
        <span className="scb__status-pill scb__status-pill--disabled">
          {t("knowledge.card.indexOutdated", "需重建索引")}
        </span>
      )}
      {!isOwner && (
        <span className="scb__status-pill scb__status-pill--inactive">
          {t(`knowledge.role.${kb.my_role}`, kb.my_role)}
        </span>
      )}
      {isOwner && <KnowledgePublicStatusPill status={kb.public_status} />}
      {kb.chat_enabled === false && (
        <span className="scb__status-pill scb__status-pill--inactive">
          {t("knowledge.card.chatOff", "对话中未启用")}
        </span>
      )}
    </>
  );

  return (
    <div
      role="link"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onOpen();
        }
      }}
      className="scb group flex h-full cursor-pointer flex-col bg-[var(--theme-bg-card)] p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-subtle)]">
          <LibraryBig size={17} className="text-[var(--theme-text-secondary)]" />
        </div>
        <div className="min-w-0 flex-1">
          <h3
            className="truncate text-[15px] font-semibold leading-snug text-[var(--theme-text)]"
            title={kb.name}
          >
            {kb.name}
          </h3>
          <p className="mt-0.5 line-clamp-2 text-[13px] leading-relaxed text-[var(--theme-text-secondary)]">
            {kb.description || t("knowledge.card.noDescription", "暂无描述")}
          </p>
        </div>
        {menuItems.length > 0 && (
          <CardMenu items={menuItems} label={t("knowledge.card.more", "更多操作")} />
        )}
      </div>

      <div className="mt-2 flex min-h-0 flex-wrap items-center gap-1.5 empty:hidden">{pills}</div>

      <div className="mt-auto flex items-center justify-between gap-2 pt-3 text-xs text-[var(--theme-text-tertiary)]">
        <span className="inline-flex min-w-0 items-center gap-1">
          <FileText size={13} aria-hidden="true" />
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
            onClick={(e) => {
              e.stopPropagation();
              if (caps.canSubscribe) onSubscribe?.();
              else onUnsubscribe?.();
            }}
            className={`${OUTLINE_BTN} !rounded-lg !px-2.5 !py-1 text-xs`}
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
