import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Network } from "lucide-react";
import { INPUT_CLS, NOTICE_WARNING, PRIMARY_BTN } from "../knowledgeStyles";

export const GRAPH_SCHEMA_MAX = 1000;

interface GraphEnableCardProps {
  canManage: boolean;
  allowGraph: boolean;
  busy: boolean;
  onEnable: (schema: string) => void;
}

export function GraphEnableCard({ canManage, allowGraph, busy, onEnable }: GraphEnableCardProps) {
  const { t } = useTranslation();
  const [schema, setSchema] = useState("");

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 sm:px-6">
      <div className="rounded-2xl border border-stone-200 bg-white p-6 dark:border-stone-700 dark:bg-stone-900">
        <div className="flex items-center gap-2 text-base font-semibold text-stone-900 dark:text-stone-100">
          <Network size={18} />
          {t("knowledge.graph.title", "知识图谱")}
        </div>
        <p className="mt-2 text-sm leading-relaxed text-stone-600 dark:text-stone-300">
          {t(
            "knowledge.graph.intro",
            "开启后会使用管理员配置的大模型，从已索引的分块中抽取实体与关系，并用于增强检索。抽取需要调用大模型，文档较多时可能耗时较长并产生费用。",
          )}
        </p>
        {!allowGraph && (
          <div className={`${NOTICE_WARNING} mt-4`}>
            {t("knowledge.graph.disabledByAdmin", "管理员已关闭知识图谱功能。")}
          </div>
        )}
        {canManage && allowGraph ? (
          <>
            <label
              htmlFor="kb-graph-schema"
              className="mt-4 block text-xs font-medium text-stone-500 dark:text-stone-400"
            >
              {t("knowledge.graph.schemaLabel", "抽取约束（可选）")}
            </label>
            <textarea
              id="kb-graph-schema"
              value={schema}
              maxLength={GRAPH_SCHEMA_MAX}
              rows={4}
              onChange={(e) => setSchema(e.target.value)}
              placeholder={t(
                "knowledge.graph.schemaPlaceholder",
                "例如：只抽取人物、公司、产品，以及它们之间的任职与合作关系。",
              )}
              className={`${INPUT_CLS} mt-1 resize-y`}
            />
            <div className="mt-1 text-right text-[11px] text-stone-400">
              {schema.length}/{GRAPH_SCHEMA_MAX}
            </div>
            <div className="mt-3 flex justify-end">
              <button
                type="button"
                disabled={busy}
                onClick={() => onEnable(schema.trim())}
                className={PRIMARY_BTN}
              >
                {busy && <Loader2 size={16} className="animate-spin" />}
                {t("knowledge.graph.enable", "开启图谱")}
              </button>
            </div>
          </>
        ) : (
          allowGraph && (
            <p className="mt-4 text-sm text-stone-500 dark:text-stone-400">
              {t("knowledge.graph.ownerOnly", "只有知识库所有者可以开启知识图谱。")}
            </p>
          )
        )}
      </div>
    </div>
  );
}
