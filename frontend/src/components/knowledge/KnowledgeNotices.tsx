import { AlertTriangle, ArrowRight, LibraryBig } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { Permission } from "../../types";
import { NOTICE_BAR, OUTLINE_BTN } from "./knowledgeStyles";

export function KnowledgeNotReadyNotice({ className = "" }: { className?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canConfigure = hasPermission(Permission.SETTINGS_MANAGE);
  return (
    <div role="alert" className={`${NOTICE_BAR} ${className}`}>
      <AlertTriangle size={14} className="shrink-0 self-start mt-[3px]" />
      <span className="min-w-0 flex-1">
        <span className="font-medium">
          {t("knowledge.notReadyTitle", "向量模型未配置，文档入库暂停")}
        </span>
        <span className="text-amber-700/80 dark:text-amber-200/70">
          {" · "}
          {t("knowledge.notReadyHint", "仍可新建知识库，配置后文档自动处理")}
        </span>
      </span>
      {canConfigure ? (
        <button
          type="button"
          onClick={() => navigate("/settings?category=knowledge_base")}
          className="ml-auto inline-flex shrink-0 items-center gap-1 font-medium text-stone-900 hover:underline dark:text-stone-100"
        >
          {t("knowledge.goConfigure", "去配置")}
          <ArrowRight size={14} />
        </button>
      ) : (
        <span className="ml-auto shrink-0 text-amber-700/80 dark:text-amber-200/70">
          {t("knowledge.contactAdmin", "联系管理员")}
        </span>
      )}
    </div>
  );
}

export function KnowledgeDisabledState() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-md rounded-3xl border border-stone-200 p-6 text-center dark:border-stone-800">
        <LibraryBig className="mx-auto mb-3 text-stone-400" size={36} />
        <h3 className="text-lg font-semibold text-stone-900 dark:text-stone-100">
          {t("knowledge.disabledTitle", "知识库功能未开启")}
        </h3>
        <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
          {t("knowledge.disabledDesc", "管理员已关闭知识库功能，请联系管理员开启。")}
        </p>
        <button type="button" onClick={() => navigate("/chat")} className={`${OUTLINE_BTN} mt-4`}>
          {t("knowledge.backToChat", "返回聊天")}
        </button>
      </div>
    </div>
  );
}

export function KnowledgeNotFoundState({ onBack }: { onBack: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-md rounded-3xl border border-stone-200 p-6 text-center dark:border-stone-800">
        <LibraryBig className="mx-auto mb-3 text-stone-400" size={36} />
        <h3 className="text-lg font-semibold text-stone-900 dark:text-stone-100">
          {t("knowledge.notFoundTitle", "知识库不存在")}
        </h3>
        <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
          {t("knowledge.notFoundDesc", "它可能已被删除，或你没有访问权限。")}
        </p>
        <button type="button" onClick={onBack} className={`${OUTLINE_BTN} mt-4`}>
          {t("knowledge.backToList", "返回知识库列表")}
        </button>
      </div>
    </div>
  );
}
