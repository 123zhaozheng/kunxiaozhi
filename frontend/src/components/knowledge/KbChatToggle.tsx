import { useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import {
  knowledgeBaseApi,
  type KnowledgeBase,
} from "../../services/api/knowledgeBase";
import { ToggleSwitch } from "../panels/AgentPanel/shared/ToggleSwitch";

/** 「对话中启用」开关；非成员（chat_enabled 为 null/缺省）不渲染。 */
export function KbChatToggle({
  kb,
  onChange,
}: {
  kb: KnowledgeBase;
  onChange: (kb: KnowledgeBase) => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  if (kb.chat_enabled === null || kb.chat_enabled === undefined) return null;
  const enabled = kb.chat_enabled;
  const label = t("knowledge.chat.enabled", "对话中启用");

  const toggle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await knowledgeBaseApi.setChatEnabled(kb.id, !enabled);
      onChange({ ...kb, ...next, chat_enabled: next.chat_enabled ?? !enabled });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="flex items-center gap-2"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <span className="text-xs text-[var(--theme-text-secondary)]">{label}</span>
      <ToggleSwitch
        enabled={enabled}
        onToggle={() => void toggle()}
        disabled={busy}
        ariaLabel={label}
      />
    </div>
  );
}
