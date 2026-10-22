import { useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { Loader2 } from "lucide-react";
import {
  knowledgeBaseApi,
  type KBConfig,
  type KnowledgeBase,
} from "../../services/api/knowledgeBase";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { DANGER_BTN, PRIMARY_BTN } from "./knowledgeStyles";
import { isPublicAllowed } from "./knowledgeUtils";
import { KnowledgePublicStatusPill } from "./KnowledgePublicStatusPill";

interface KnowledgePublishSectionProps {
  kb: KnowledgeBase;
  config: KBConfig | null;
  onKbChange: (kb: KnowledgeBase) => void;
}

export function KnowledgePublishSection({
  kb,
  config,
  onKbChange,
}: KnowledgePublishSectionProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (!isPublicAllowed(config)) return null;

  const status = kb.public_status;
  const isPublished = status === "pending" || status === "approved";

  const run = async (action: "publish" | "unpublish") => {
    setBusy(true);
    try {
      const next =
        action === "publish"
          ? await knowledgeBaseApi.publish(kb.id)
          : await knowledgeBaseApi.unpublish(kb.id);
      onKbChange(next);
      toast.success(
        action === "publish"
          ? next.public_status === "pending"
            ? t("knowledge.publish.submitted", "已提交审核")
            : t("knowledge.publish.published", "已发布到知识广场")
          : t("knowledge.publish.unpublished", "已取消公开"),
      );
      setConfirmOpen(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-stone-800 dark:text-stone-100">
          {t("knowledge.publish.title", "公开到知识广场")}
        </h3>
        <KnowledgePublicStatusPill status={status} />
      </div>
      <p className="text-xs text-stone-500 dark:text-stone-400">
        {t(
          "knowledge.publish.hint",
          "公开后，其他用户可以在知识广场查看并订阅这个知识库。部分站点需要管理员审核。",
        )}
      </p>
      {(status === "rejected" || status === "delisted") && kb.review?.reason && (
        <p className="text-xs text-red-600 dark:text-red-400">
          {t("knowledge.publish.reason", {
            reason: kb.review?.reason,
            defaultValue: "原因：{{reason}}",
          })}
        </p>
      )}
      {isPublished ? (
        <button
          type="button"
          onClick={() => setConfirmOpen(true)}
          disabled={busy}
          className={DANGER_BTN}
        >
          {t("knowledge.publish.unpublish", "取消公开")}
        </button>
      ) : (
        <button
          type="button"
          onClick={() => void run("publish")}
          disabled={busy}
          className={PRIMARY_BTN}
        >
          {busy && <Loader2 size={14} className="animate-spin" />}
          {status === "none"
            ? t("knowledge.publish.publish", "发布")
            : t("knowledge.publish.republish", "重新发布")}
        </button>
      )}

      <ConfirmDialog
        isOpen={confirmOpen}
        title={t("knowledge.publish.unpublishTitle", "取消公开")}
        message={t(
          "knowledge.publish.unpublishConfirm",
          "取消公开后，所有订阅者会失去访问权限，订阅关系也会被删除。确定继续吗？",
        )}
        confirmText={t("knowledge.publish.unpublish", "取消公开")}
        variant="danger"
        loading={busy}
        onConfirm={() => void run("unpublish")}
        onCancel={() => setConfirmOpen(false)}
      />
    </section>
  );
}
