import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
import { Loader2, Search, Shuffle } from "lucide-react";
import { ConfirmDialog } from "../../common/ConfirmDialog";
import {
  knowledgeBaseApi,
  type KBConfig,
  type KBGraphData,
  type KBGraphMention,
  type KBGraphStatus,
  type KnowledgeBase,
} from "../../../services/api/knowledgeBase";
import { INPUT_CLS, OUTLINE_BTN } from "../knowledgeStyles";
import { EntityDetailPanel } from "./EntityDetailPanel";
import { GraphEnableCard } from "./GraphEnableCard";
import { GraphStatusBar } from "./GraphStatusBar";
import { computeForceLayout } from "./forceLayout";
import { toFlowElements } from "./graphMapping";
import {
  GRAPH_DEFAULT_DEPTH,
  GRAPH_DEFAULT_NODES,
  GRAPH_DEPTH_OPTIONS,
  GRAPH_NODE_OPTIONS,
  GRAPH_POLL_MS,
  graphNeedsPolling,
} from "./graphUtils";

const GraphCanvas = lazy(() => import("./GraphCanvas"));

interface GraphTabProps {
  kb: KnowledgeBase;
  config: KBConfig | null;
  canManage: boolean;
  onChanged: () => void;
  onOpenMention: (mention: KBGraphMention) => void;
}

const errorMessage = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function GraphTab({ kb, config, canManage, onChanged, onOpenMention }: GraphTabProps) {
  const { t } = useTranslation();
  const kbId = kb.id;
  const [status, setStatus] = useState<KBGraphStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);
  const [graph, setGraph] = useState<KBGraphData | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [maxNodes, setMaxNodes] = useState<number>(GRAPH_DEFAULT_NODES);
  const [maxDepth, setMaxDepth] = useState<number>(GRAPH_DEFAULT_DEPTH);
  const [layoutSeed, setLayoutSeed] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const graphSeq = useRef(0);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await knowledgeBaseApi.graphStatus(kbId));
      setStatusError(false);
    } catch {
      setStatusError(true);
    }
  }, [kbId]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const polling = graphNeedsPolling(status);
  useEffect(() => {
    if (!polling) return undefined;
    const timer = setInterval(() => void loadStatus(), GRAPH_POLL_MS);
    return () => clearInterval(timer);
  }, [polling, loadStatus]);

  const enabled = status?.enabled ?? false;
  // The graph changes while a build runs, so progress counters double as a refetch trigger.
  const dataVersion = `${status?.done ?? 0}:${status?.entity_count ?? 0}:${status?.relation_count ?? 0}`;

  useEffect(() => {
    if (!enabled) {
      setGraph(null);
      return;
    }
    const seq = ++graphSeq.current;
    setGraphLoading(true);
    knowledgeBaseApi
      .getGraph(kbId, { q: query, max_nodes: maxNodes, max_depth: maxDepth })
      .then((res) => {
        if (seq === graphSeq.current) setGraph(res);
      })
      .catch((e) => {
        if (seq === graphSeq.current) toast.error(errorMessage(e));
      })
      .finally(() => {
        if (seq === graphSeq.current) setGraphLoading(false);
      });
  }, [kbId, enabled, query, maxNodes, maxDepth, dataVersion]);

  const layout = useMemo(
    () => (graph ? computeForceLayout(graph.nodes, graph.edges, { seed: layoutSeed }) : {}),
    [graph, layoutSeed],
  );
  const elements = useMemo(
    () => (graph ? toFlowElements(graph, layout, selectedId) : { nodes: [], edges: [] }),
    [graph, layout, selectedId],
  );
  // Selection only restyles nodes; the viewport refits solely when the node set or seed changes.
  const fitKey = useMemo(
    () => `${layoutSeed}:${graph?.nodes.map((n) => n.id).join(",") ?? ""}`,
    [layoutSeed, graph],
  );

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const enable = (schema: string) =>
    run(async () => {
      setStatus(await knowledgeBaseApi.enableGraph(kbId, schema));
      toast.success(t("knowledge.graph.enabled", "已开启知识图谱，开始构建"));
      onChanged();
    });

  const build = () =>
    run(async () => {
      const res = await knowledgeBaseApi.buildGraph(kbId);
      toast.success(
        res.count > 0
          ? t("knowledge.graph.buildQueued", "已加入构建队列")
          : t("knowledge.graph.nothingToBuild", "没有需要构建的分块"),
      );
      await loadStatus();
    });

  const disable = () =>
    run(async () => {
      setStatus(await knowledgeBaseApi.disableGraph(kbId));
      setSelectedId(null);
      setDisableOpen(false);
      toast.success(t("knowledge.graph.disabledDone", "已关闭知识图谱并清除图谱数据"));
      onChanged();
    });

  if (!status) {
    return statusError ? (
      <div className="p-8 text-center text-sm text-stone-500">
        {t("knowledge.graph.loadFailed", "图谱状态加载失败")}{" "}
        <button type="button" className="underline" onClick={() => void loadStatus()}>
          {t("knowledge.retryLoad", "重新加载")}
        </button>
      </div>
    ) : (
      <div className="flex justify-center p-8">
        <Loader2 className="animate-spin" size={20} />
      </div>
    );
  }

  if (!status.enabled) {
    return (
      <GraphEnableCard
        canManage={canManage}
        allowGraph={status.allow_graph && (config?.allow_graph ?? true)}
        busy={busy}
        onEnable={(schema) => void enable(schema)}
      />
    );
  }

  const nodeOptions = GRAPH_NODE_OPTIONS.map((n) => (
    <option key={n} value={n}>
      {n}
    </option>
  ));
  const labelCls = "flex items-center gap-1.5 text-xs text-[var(--theme-text-secondary)]";
  const selectCls =
    "rounded-lg border border-stone-200 bg-white px-2 py-1.5 text-sm dark:border-stone-700 dark:bg-stone-900";

  return (
    <div className="flex h-full min-h-[560px] flex-col">
      <GraphStatusBar
        kbId={kbId}
        status={status}
        canManage={canManage}
        busy={busy}
        onBuild={() => void build()}
        onDisable={() => setDisableOpen(true)}
      />

      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--theme-border)] px-4 py-2 sm:px-6">
        <form
          className="flex min-w-[220px] flex-1 items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(searchInput.trim());
            setSelectedId(null);
          }}
        >
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("knowledge.graph.search.placeholder", "搜索实体名称")}
            aria-label={t("knowledge.graph.search.placeholder", "搜索实体名称")}
            className={INPUT_CLS}
          />
          <button type="submit" className={OUTLINE_BTN}>
            <Search size={16} />
            {t("knowledge.graph.search.button", "搜索")}
          </button>
        </form>
        <label className={labelCls}>
          {t("knowledge.graph.maxNodes", "节点数")}
          <select
            value={maxNodes}
            onChange={(e) => setMaxNodes(Number(e.target.value))}
            className={selectCls}
          >
            {nodeOptions}
          </select>
        </label>
        <label className={labelCls}>
          {t("knowledge.graph.depth", "深度")}
          <select
            value={maxDepth}
            onChange={(e) => setMaxDepth(Number(e.target.value))}
            className={selectCls}
          >
            {GRAPH_DEPTH_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => setLayoutSeed((s) => s + 1)} className={OUTLINE_BTN}>
          <Shuffle size={16} />
          {t("knowledge.graph.relayout", "重新布局")}
        </button>
        {graph?.truncated && (
          <span className="text-xs text-amber-700 dark:text-amber-300">
            {t("knowledge.graph.truncated", "仅显示部分节点，可调高节点数或缩小搜索范围")}
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="relative min-h-[420px] min-w-0 flex-1">
          {graphLoading && !graph && (
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 className="animate-spin" size={20} />
            </div>
          )}
          {graph && graph.nodes.length === 0 && !graphLoading && (
            <div className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-stone-500 dark:text-stone-400">
              {query
                ? t("knowledge.graph.emptySearch", "没有匹配的实体")
                : t("knowledge.graph.empty", "还没有抽取到实体，构建完成后会在这里显示。")}
            </div>
          )}
          {graph && graph.nodes.length > 0 && (
            <Suspense
              fallback={
                <div className="flex h-full items-center justify-center">
                  <Loader2 className="animate-spin" size={20} />
                </div>
              }
            >
              <GraphCanvas
                nodes={elements.nodes}
                edges={elements.edges}
                fitKey={fitKey}
                onSelect={setSelectedId}
              />
            </Suspense>
          )}
        </div>
        {selectedId && (
          <EntityDetailPanel
            key={selectedId}
            kbId={kbId}
            entityId={selectedId}
            onClose={() => setSelectedId(null)}
            onSelectEntity={setSelectedId}
            onOpenMention={onOpenMention}
          />
        )}
      </div>

      <ConfirmDialog
        isOpen={disableOpen}
        title={t("knowledge.graph.disableTitle", "关闭知识图谱")}
        message={t(
          "knowledge.graph.disableConfirm",
          "关闭后会删除这个知识库的全部实体、关系和图谱向量，重新开启需要重新抽取。确定关闭吗？",
        )}
        confirmText={t("knowledge.graph.disable", "关闭图谱")}
        variant="danger"
        loading={busy}
        onConfirm={() => void disable()}
        onCancel={() => setDisableOpen(false)}
      />
    </div>
  );
}
