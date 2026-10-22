import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { ArrowRight, Check, Loader2, LibraryBig, X } from "lucide-react";
import { useAuth } from "../../../../hooks/useAuth";
import {
  knowledgeBaseApi,
  type KBDraftStatus,
  type KBSaveDraft,
  type KnowledgeBase,
} from "../../../../services/api/knowledgeBase";
import type { KbSaveDraftResult } from "./knowledgeBaseResult";
import { GHOST_BTN, INPUT_CLS, PRIMARY_BTN } from "../../../knowledge/knowledgeStyles";

type Access = "loading" | "interactive" | "readonly";

export function KbSaveDraftCard({ result }: { result: KbSaveDraftResult }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const draftId = result.draft_id;

  const [access, setAccess] = useState<Access>(
    isAuthenticated ? "loading" : "readonly",
  );
  const [serverDraft, setServerDraft] = useState<KBSaveDraft | null>(null);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState<string>(result.suggested_kb_id ?? "");
  const [busy, setBusy] = useState<"confirm" | "cancel" | null>(null);
  const [savedKbId, setSavedKbId] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated) {
      setAccess("readonly");
      return;
    }
    let cancelled = false;
    setAccess("loading");
    Promise.all([
      knowledgeBaseApi.getDraft(draftId),
      knowledgeBaseApi
        .list("writable")
        .then((r) => r.items)
        .catch(() => [] as KnowledgeBase[]),
    ])
      .then(([draft, items]) => {
        if (cancelled) return;
        setServerDraft(draft);
        setKbs(items);
        setAccess("interactive");
      })
      .catch(() => {
        if (!cancelled) setAccess("readonly");
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, draftId]);

  useEffect(() => {
    if (kbs.length === 0) return;
    setKbId((cur) => {
      if (cur && kbs.some((k) => k.id === cur)) return cur;
      const suggested = result.suggested_kb_id;
      if (suggested && kbs.some((k) => k.id === suggested)) return suggested;
      return kbs[0].id;
    });
  }, [kbs, result.suggested_kb_id]);

  const status: KBDraftStatus | null = serverDraft?.status ?? null;
  const resolvedKbId = savedKbId ?? serverDraft?.kb_id ?? null;
  const resolvedKbName = useMemo(() => {
    const id = resolvedKbId;
    if (!id) return "";
    return kbs.find((k) => k.id === id)?.name ?? "";
  }, [kbs, resolvedKbId]);
  const pending = status === "pending";
  const interactive = access === "interactive" && pending;

  const confirm = async () => {
    if (!kbId || busy) return;
    setBusy("confirm");
    try {
      const res = await knowledgeBaseApi.confirmDraft(draftId, {
        kb_id: kbId,
        title: result.title || undefined,
      });
      setSavedKbId(res.kb_id);
      setServerDraft((d) =>
        d ? { ...d, status: "confirmed", kb_id: res.kb_id } : d,
      );
      toast.success(t("knowledge.draft.saved", "已存入知识库"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      knowledgeBaseApi.getDraft(draftId).then(setServerDraft).catch(() => {});
    } finally {
      setBusy(null);
    }
  };

  const cancel = async () => {
    if (busy) return;
    setBusy("cancel");
    try {
      const d = await knowledgeBaseApi.cancelDraft(draftId);
      setServerDraft(d);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
      knowledgeBaseApi.getDraft(draftId).then(setServerDraft).catch(() => {});
    } finally {
      setBusy(null);
    }
  };

  const statusText =
    status === "confirmed"
      ? t("knowledge.draft.confirmedIn", {
          name: resolvedKbName || result.suggested_kb_name || "",
          defaultValue: "已存入《{{name}}》",
        })
      : status === "cancelled"
        ? t("knowledge.draft.cancelled", "已取消")
        : status === "expired"
          ? t("knowledge.draft.expired", "已过期")
          : access === "readonly"
            ? t("knowledge.draft.readonly", "仅展示草稿内容")
            : null;

  return (
    <div className="my-2 rounded-2xl border border-stone-200 bg-white p-4 dark:border-stone-700 dark:bg-stone-900">
      <div className="flex items-center gap-2 text-sm font-medium text-stone-900 dark:text-stone-100">
        <LibraryBig size={16} className="shrink-0 text-stone-500" />
        <span className="min-w-0 flex-1 truncate">
          {result.title || t("knowledge.draft.untitled", "未命名文档")}
        </span>
        {result.char_count !== null && (
          <span className="shrink-0 text-xs font-normal text-stone-400">
            {t("knowledge.draft.chars", {
              count: result.char_count,
              defaultValue: "{{count}} 字",
            })}
          </span>
        )}
      </div>
      {result.preview && (
        <p className="mt-2 line-clamp-6 whitespace-pre-wrap break-words text-sm text-stone-600 dark:text-stone-300">
          {result.preview}
        </p>
      )}

      {interactive && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-xs text-stone-500">
            {t("knowledge.draft.targetKb", "保存到")}
          </label>
          <select
            className={`${INPUT_CLS} !w-auto min-w-40 !py-1.5`}
            value={kbId}
            disabled={busy !== null || kbs.length === 0}
            onChange={(e) => setKbId(e.target.value)}
          >
            {kbs.length === 0 && (
              <option value="">
                {t("knowledge.draft.noWritableKb", "没有可写入的知识库")}
              </option>
            )}
            {kbs.map((kb) => (
              <option key={kb.id} value={kb.id}>
                {kb.name}
              </option>
            ))}
          </select>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              className={GHOST_BTN}
              disabled={busy !== null}
              onClick={() => void cancel()}
            >
              {busy === "cancel" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <X size={14} />
              )}
              {t("knowledge.draft.cancel", "取消")}
            </button>
            <button
              type="button"
              className={PRIMARY_BTN}
              disabled={busy !== null || !kbId}
              onClick={() => void confirm()}
            >
              {busy === "confirm" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Check size={14} />
              )}
              {t("knowledge.draft.confirm", "确认保存")}
            </button>
          </div>
        </div>
      )}

      {(statusText || access === "loading") && (
        <div className="mt-3 flex items-center gap-2 text-xs text-stone-500 dark:text-stone-400">
          {access === "loading" ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            statusText
          )}
          {status === "confirmed" && resolvedKbId && (
            <button
              type="button"
              className="inline-flex items-center gap-0.5 text-blue-600 hover:underline dark:text-blue-400"
              onClick={() => navigate(`/knowledge/${resolvedKbId}`)}
            >
              {t("knowledge.draft.open", "查看")}
              <ArrowRight size={12} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
