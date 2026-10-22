import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { Loader2, Trash2, X } from "lucide-react";
import {
  knowledgeBaseApi,
  type KBConfig,
  type KBMember,
  type KBMemberRole,
  type KBResolvedUser,
  type KnowledgeBase,
} from "../../services/api/knowledgeBase";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { GlassSelect } from "../common/GlassSelect";
import { KnowledgePublishSection } from "./KnowledgePublishSection";
import { GHOST_BTN, INPUT_CLS, PRIMARY_BTN, ROW_ACTION_BTN } from "./knowledgeStyles";
import { SHARE_MAX_USERNAMES, parseUsernameInput } from "./knowledgeUtils";

interface KnowledgeShareDialogProps {
  kb: KnowledgeBase;
  config: KBConfig | null;
  onClose: () => void;
  onKbChange: (kb: KnowledgeBase) => void;
}

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function KnowledgeShareDialog({
  kb,
  config,
  onClose,
  onKbChange,
}: KnowledgeShareDialogProps) {
  const { t } = useTranslation();
  const [members, setMembers] = useState<KBMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [raw, setRaw] = useState("");
  const [usernames, setUsernames] = useState<string[]>([]);
  const [resolved, setResolved] = useState<KBResolvedUser[] | null>(null);
  const [unresolved, setUnresolved] = useState<string[]>([]);
  const [role, setRole] = useState<KBMemberRole>("viewer");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<KBMember | null>(null);

  const loadMembers = useCallback(async () => {
    try {
      setMembers(
        (await knowledgeBaseApi.listMembers(kb.id)).items.filter((m) => m.role !== "owner"),
      );
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [kb.id]);

  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !removing && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, removing]);

  const roleOptions = [
    { value: "viewer", label: t("knowledge.role.viewer", "查看者") },
    { value: "editor", label: t("knowledge.role.editor", "编辑者") },
  ];

  const addFromInput = () => {
    const { usernames: next, overflow } = parseUsernameInput(raw, usernames);
    setUsernames(next);
    setRaw("");
    setResolved(null);
    setUnresolved([]);
    if (overflow > 0) {
      toast.error(
        t("knowledge.share.tooMany", {
          max: SHARE_MAX_USERNAMES,
          defaultValue: "一次最多分享给 {{max}} 位用户",
        }),
      );
    }
  };

  const resolve = async () => {
    setBusy(true);
    try {
      const res = await knowledgeBaseApi.resolveUsers(usernames);
      const usable = (u: KBResolvedUser) => u.exists && u.active;
      setResolved(res.items.filter(usable));
      setUnresolved(res.items.filter((u) => !usable(u)).map((u) => u.username));
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const share = async () => {
    if (!resolved || resolved.length === 0) return;
    setBusy(true);
    try {
      const result = await knowledgeBaseApi.shareWith(
        kb.id,
        resolved.map((u) => u.username),
        role,
      );
      const done = result.added.length + result.updated.length;
      if (done > 0) {
        toast.success(
          t("knowledge.share.shared", {
            count: done,
            defaultValue: "已分享给 {{count}} 位用户",
          }),
        );
      }
      setUsernames([]);
      setResolved(null);
      setUnresolved(result.errors.map((e) => e.username));
      await loadMembers();
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const changeRole = async (member: KBMember, next: KBMemberRole) => {
    try {
      await knowledgeBaseApi.updateMemberRole(kb.id, member.user_id, next);
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, role: next } : m)),
      );
    } catch (e) {
      toast.error(errMessage(e));
    }
  };

  const confirmRemove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await knowledgeBaseApi.removeMember(kb.id, removing.user_id);
      setMembers((prev) => prev.filter((m) => m.user_id !== removing.user_id));
      setRemoving(null);
    } catch (e) {
      toast.error(errMessage(e));
    } finally {
      setBusy(false);
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
        aria-label={t("knowledge.share.title", "分享知识库")}
        className="flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-3xl bg-white shadow-2xl dark:bg-stone-900 sm:max-w-xl sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-stone-100 px-5 py-4 dark:border-stone-800">
          <h2 className="text-base font-semibold text-stone-900 dark:text-stone-100">
            {t("knowledge.share.title", "分享知识库")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-stone-500 hover:bg-stone-100 dark:hover:bg-stone-800"
            aria-label={t("common.close", "关闭")}
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
          <section className="space-y-2">
            <label className="text-sm font-medium text-stone-800 dark:text-stone-100">
              {t("knowledge.share.addUsers", "添加用户")}
            </label>
            <textarea
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              onPaste={(e) => {
                const text = e.clipboardData.getData("text");
                if (/[\s,，、;；]/.test(text.trim())) {
                  e.preventDefault();
                  const { usernames: next, overflow } = parseUsernameInput(text, usernames);
                  setUsernames(next);
                  setResolved(null);
                  setUnresolved([]);
                  if (overflow > 0) {
                    toast.error(
                      t("knowledge.share.tooMany", {
                        max: SHARE_MAX_USERNAMES,
                        defaultValue: "一次最多分享给 {{max}} 位用户",
                      }),
                    );
                  }
                }
              }}
              rows={3}
              placeholder={t(
                "knowledge.share.pastePlaceholder",
                "粘贴用户名，可用逗号、空格或换行分隔",
              )}
              className={INPUT_CLS}
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={addFromInput}
                disabled={raw.trim() === ""}
                className={GHOST_BTN}
              >
                {t("knowledge.share.add", "添加到列表")}
              </button>
              <span className="text-xs text-stone-500 dark:text-stone-400">
                {t("knowledge.share.count", {
                  count: usernames.length,
                  max: SHARE_MAX_USERNAMES,
                  defaultValue: "已添加 {{count}}/{{max}}",
                })}
              </span>
            </div>

            {usernames.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {usernames.map((name) => (
                  <span
                    key={name}
                    className="inline-flex items-center gap-1 rounded-full bg-[var(--theme-bg-subtle)] px-2.5 py-0.5 text-xs text-[var(--theme-text)]"
                  >
                    {name}
                    <button
                      type="button"
                      onClick={() => {
                        setUsernames((prev) => prev.filter((u) => u !== name));
                        setResolved(null);
                        setUnresolved([]);
                      }}
                      aria-label={t("common.remove", "移除")}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {unresolved.length > 0 && (
              <p className="text-xs text-red-600 dark:text-red-400">
                {t("knowledge.share.unresolved", {
                  list: unresolved.join("、"),
                  defaultValue: "未找到用户：{{list}}",
                })}
              </p>
            )}
            {resolved && resolved.length > 0 && (
              <p className="text-xs text-emerald-600 dark:text-emerald-400">
                {t("knowledge.share.resolvedCount", {
                  count: resolved.length,
                  defaultValue: "已识别 {{count}} 位用户",
                })}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2 pt-1">
              <GlassSelect
                value={role}
                onChange={(v) => setRole(v as KBMemberRole)}
                options={roleOptions}
                className="w-32"
              />
              {resolved === null || resolved.length === 0 ? (
                <button
                  type="button"
                  onClick={() => void resolve()}
                  disabled={busy || usernames.length === 0}
                  className={PRIMARY_BTN}
                >
                  {busy && <Loader2 size={14} className="animate-spin" />}
                  {t("knowledge.share.resolve", "识别用户")}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void share()}
                  disabled={busy}
                  className={PRIMARY_BTN}
                >
                  {busy && <Loader2 size={14} className="animate-spin" />}
                  {t("knowledge.share.confirm", "确认分享")}
                </button>
              )}
            </div>
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-medium text-stone-800 dark:text-stone-100">
              {t("knowledge.share.members", "已分享的成员")}
            </h3>
            {loading ? (
              <Loader2 size={16} className="animate-spin text-stone-400" />
            ) : members.length === 0 ? (
              <p className="text-sm text-stone-500 dark:text-stone-400">
                {t("knowledge.share.noMembers", "还没有分享给任何人")}
              </p>
            ) : (
              <ul className="divide-y divide-[var(--theme-border)] rounded-xl border border-[var(--theme-border)]">
                {members.map((m) => (
                  <li key={m.user_id} className="flex items-center gap-2 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-sm text-[var(--theme-text)]">
                      {m.username}
                    </span>
                    <GlassSelect
                      value={m.role}
                      onChange={(v) => void changeRole(m, v as KBMemberRole)}
                      options={roleOptions}
                      className="w-28"
                    />
                    <button
                      type="button"
                      onClick={() => setRemoving(m)}
                      className={`${ROW_ACTION_BTN} hover:!text-red-600`}
                      aria-label={t("knowledge.share.remove", "移除成员")}
                      title={t("knowledge.share.remove", "移除成员")}
                    >
                      <Trash2 size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <KnowledgePublishSection kb={kb} config={config} onKbChange={onKbChange} />
        </div>
      </div>

      <ConfirmDialog
        isOpen={!!removing}
        title={t("knowledge.share.removeTitle", "移除成员")}
        message={t("knowledge.share.removeConfirm", {
          name: removing?.username ?? "",
          defaultValue: "确定移除“{{name}}”吗？对方将无法再访问这个知识库。",
        })}
        confirmText={t("knowledge.share.remove", "移除成员")}
        variant="danger"
        loading={busy}
        onConfirm={() => void confirmRemove()}
        onCancel={() => setRemoving(null)}
      />
    </div>
  );
}
