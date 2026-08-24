import { useCallback, useEffect, useRef, useState } from "react";
import {
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCw,
  Search,
  Server,
  Trash2,
  Wifi,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { settingsApi } from "../../services/api";
import { openSandboxApi } from "../../services/api/opensandbox";
import type {
  OpenSandboxInventoryItem,
  OpenSandboxNode,
  OpenSandboxNodeInput,
  OpenSandboxNodesResponse,
} from "../../types";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { GlassSelect } from "../common/GlassSelect";
import {
  resolveInventoryPage,
  type InventoryLoadIntent,
} from "./openSandboxInventoryState";

const inputClass =
  "mt-1 w-full rounded-lg border border-[var(--glass-border)] bg-[var(--theme-bg-card)] px-3 py-2 text-sm text-stone-900 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60 dark:text-stone-100";

const blankNode = (): OpenSandboxNodeInput => ({
  id: `node-${Date.now()}`,
  domain: "",
  image: "ubuntu",
  timeout: 3600,
  work_dir: "/root",
  use_server_proxy: true,
  max_sandboxes: 1,
  enabled: true,
  priority: 100,
});

function healthTone(state?: string) {
  if (state === "healthy") {
    return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200";
  }
  if (state === "unavailable") {
    return "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200";
  }
  return "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300";
}

function statusTone(state?: string) {
  if (state === "running" || state === "started") {
    return "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200";
  }
  if (state === "paused" || state === "stopped" || state === "archived") {
    return "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200";
  }
  if (state === "terminated" || state === "destroyed" || state === "not_found") {
    return "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400";
  }
  return "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300";
}

function usesForceRemoveNode(node?: Pick<OpenSandboxNode, "health_state">) {
  if (!node) {
    return false;
  }
  const health = node.health_state || "unknown";
  return health === "unknown" || health === "unavailable";
}

function canForgetLocalInventoryRow(
  item: Pick<OpenSandboxInventoryItem, "state" | "node_id">,
  nodes: Array<Pick<OpenSandboxNode, "id" | "health_state">> | undefined,
) {
  if (item.state === "unknown" || item.state === "creating") {
    return true;
  }
  const health = nodes?.find((node) => node.id === item.node_id)?.health_state;
  return health === "unknown" || health === "unavailable";
}

/** Keep the stable backend error code visible (R4); duck-typed like isSandboxCapacityError. */
function describeActionError(cause: unknown, fallback: string) {
  const message = cause instanceof Error ? cause.message : fallback;
  const code =
    cause && typeof cause === "object" && "code" in cause
      ? (cause as { code?: unknown }).code
      : undefined;
  if (typeof code !== "string" || !code || message.includes(code)) {
    return message;
  }
  return `${message} (${code})`;
}

export function OpenSandboxNodesPanel() {
  const { t, i18n } = useTranslation();
  const copy = {
    title: t("openSandboxAdmin.title", "OpenSandbox 节点"),
    description: t(
      "openSandboxAdmin.description",
      "配置节点、查看容量，并管理由昆小智创建的沙箱。",
    ),
    mode: t("openSandboxAdmin.mode", "模式"),
    multiNode: t("openSandboxAdmin.multiNode", "多节点"),
    legacy: t("openSandboxAdmin.legacy", "单节点兼容"),
    legacyHint: t(
      "openSandboxAdmin.legacyHint",
      "当前沿用下方设置里的单节点 OpenSandbox 配置。托管列表仍可查看和操作；切到多节点后再在这里添加独立节点。",
    ),
    addNode: t("openSandboxAdmin.addNode", "添加节点"),
    node: t("openSandboxAdmin.node", "节点"),
    domain: t("openSandboxAdmin.domain", "地址"),
    timeout: t("openSandboxAdmin.timeout", "超时（秒）"),
    apiKey: t("openSandboxAdmin.apiKey", "API Key"),
    serverProxy: t("openSandboxAdmin.serverProxy", "Server Proxy"),
    configuredSecret: t("openSandboxAdmin.configuredSecret", "已设置，留空保持"),
    clearSecret: t("openSandboxAdmin.clearSecret", "清除 API Key"),
    image: t("openSandboxAdmin.image", "镜像"),
    workDir: t("openSandboxAdmin.workDir", "工作目录"),
    maxSandboxes: t("openSandboxAdmin.maxSandboxes", "最大沙箱数"),
    priority: t("openSandboxAdmin.priority", "优先级"),
    enabled: t("openSandboxAdmin.enabled", "启用"),
    health: t("openSandboxAdmin.health", "健康"),
    healthUnknown: t("openSandboxAdmin.healthUnknown", "未知"),
    lastHealth: t("openSandboxAdmin.lastHealth", "最近探测"),
    never: t("openSandboxAdmin.never", "从未"),
    capacity: t("openSandboxAdmin.capacity", "容量"),
    overCapacity: t("openSandboxAdmin.overCapacity", "超容量"),
    error: t("openSandboxAdmin.error", "错误"),
    probe: t("openSandboxAdmin.probe", "探测"),
    drain: t("openSandboxAdmin.drain", "排空"),
    undrain: t("openSandboxAdmin.undrain", "解除排空"),
    removeNodeHint: t(
      "openSandboxAdmin.removeNodeHint",
      "移除节点（有绑定或占用时会被拒绝）",
    ),
    forceRemove: t("openSandboxAdmin.forceRemove", "强制移除"),
    forceRemoveHint: t(
      "openSandboxAdmin.forceRemoveHint",
      "强制移除：立即清理本地占用并删除节点（不经过保存）",
    ),
    save: t("openSandboxAdmin.save", "保存节点配置"),
    inventory: t("openSandboxAdmin.inventory", "托管沙箱"),
    inventoryHint: t(
      "openSandboxAdmin.inventoryHint",
      "仅显示昆小智创建并有绑定的沙箱，不是节点上的全部容器。",
    ),
    inventoryHintLegacy: t(
      "openSandboxAdmin.inventoryHintLegacy",
      "单节点兼容模式不展示托管列表。切到多节点后，只显示带节点归属、可探测的绑定。",
    ),
    allNodes: t("openSandboxAdmin.allNodes", "全部节点"),
    allStates: t("openSandboxAdmin.allStates", "全部状态"),
    running: t("openSandboxAdmin.running", "运行中"),
    frozen: t("openSandboxAdmin.frozen", "冻结"),
    unknown: t("openSandboxAdmin.unknown", "未知"),
    emptyInventory: t("openSandboxAdmin.emptyInventory", "暂无托管沙箱"),
    searchPlaceholder: t("openSandboxAdmin.searchPlaceholder", "工号或沙箱 ID"),
    refreshList: t("openSandboxAdmin.refreshList", "刷新列表"),
    user: t("openSandboxAdmin.user", "用户"),
    sandboxId: t("openSandboxAdmin.sandboxId", "沙箱 ID"),
    createdAt: t("openSandboxAdmin.createdAt", "创建时间"),
    lastUsedAt: t("openSandboxAdmin.lastUsedAt", "最后使用"),
    expiresAt: t("openSandboxAdmin.expiresAt", "到期时间"),
    actions: t("openSandboxAdmin.actions", "操作"),
    freezeHint: t("openSandboxAdmin.freezeHint", "冻结：不释放内存，TTL 继续"),
    resume: t("openSandboxAdmin.resume", "恢复"),
    renew: t("openSandboxAdmin.renew", "续期 TTL"),
    terminateHint: t(
      "openSandboxAdmin.terminateHint",
      "终止（不可恢复，文件将丢失）",
    ),
    rows: t("openSandboxAdmin.rows", "条"),
    terminateTitle: t("openSandboxAdmin.terminateTitle", "终止沙箱"),
    terminateWarning: t(
      "openSandboxAdmin.terminateWarning",
      "此操作不可恢复，沙箱文件将永久丢失，且可能中断正在执行的任务。",
    ),
    confirmTerminate: t("openSandboxAdmin.confirmTerminate", "确认终止"),
    forgetLocalHint: t(
      "openSandboxAdmin.forgetLocalHint",
      "仅清理本地账本（不调用远端）",
    ),
    forgetLocalTitle: t("openSandboxAdmin.forgetLocalTitle", "仅清理本地账本"),
    forgetLocalWarning: t(
      "openSandboxAdmin.forgetLocalWarning",
      "将清除本地占用，该节点容量 x/10 会立即下降。远端容器可能仍在，需等待 TTL 回收。此操作不可恢复。",
    ),
    confirmForgetLocal: t("openSandboxAdmin.confirmForgetLocal", "确认清理本地"),
    removeTitle: t("openSandboxAdmin.removeTitle", "移除节点"),
    removeWarning: t(
      "openSandboxAdmin.removeWarning",
      "仅未被使用的节点可以移除；保存配置后才会生效。",
    ),
    forceRemoveTitle: t("openSandboxAdmin.forceRemoveTitle", "强制移除节点"),
    forceRemoveWarning: t(
      "openSandboxAdmin.forceRemoveWarning",
      "将清除本地占用和容量 x/10。远端容器可能仍在，需等待 TTL 回收。若这是最后一个多节点，将切换到单节点兼容。此操作不可恢复。",
    ),
    confirmForceRemove: t("openSandboxAdmin.confirmForceRemove", "确认强制移除"),
    remove: t("openSandboxAdmin.remove", "移除"),
    providerBinding: t("openSandboxAdmin.providerBinding", "状态"),
  };
  const [data, setData] = useState<OpenSandboxNodesResponse | null>(null);
  const [draft, setDraft] = useState<OpenSandboxNodeInput[]>([]);
  const [inventory, setInventory] = useState<OpenSandboxInventoryItem[]>([]);
  const [inventoryTotal, setInventoryTotal] = useState(0);
  const [inventoryPage, setInventoryPage] = useState(0);
  const [stateFilter, setStateFilter] = useState("");
  const [nodeFilter, setNodeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmSandbox, setConfirmSandbox] =
    useState<OpenSandboxInventoryItem | null>(null);
  const [confirmForget, setConfirmForget] =
    useState<OpenSandboxInventoryItem | null>(null);
  const [confirmNode, setConfirmNode] = useState<string | null>(null);
  const [confirmForceRemove, setConfirmForceRemove] = useState<string | null>(null);
  const [forceRemoveBusy, setForceRemoveBusy] = useState(false);
  // Persisted mode, unaffected by the unsaved mode dropdown: the backend rejects
  // force-remove outright while the stored mode is still `legacy`.
  const [serverMode, setServerMode] =
    useState<OpenSandboxNodesResponse["mode"] | null>(null);
  const [actionKey, setActionKey] = useState<string | null>(null);
  const mounted = useRef(true);
  const translateRef = useRef(t);
  const inventoryPageRef = useRef(0);
  const inventoryRequestRef = useRef(0);
  const inventoryFiltersRef = useRef({
    nodeFilter: "",
    stateFilter: "",
    search: "",
  });
  const filtersInitialized = useRef(false);
  const draftInitialized = useRef(false);
  const draftDirty = useRef(false);

  translateRef.current = t;
  inventoryFiltersRef.current = { nodeFilter, stateFilter, search };

  const load = useCallback(async (intent: InventoryLoadIntent = { type: "refresh" }) => {
    const page = resolveInventoryPage(inventoryPageRef.current, intent);
    const requestId = ++inventoryRequestRef.current;
    inventoryPageRef.current = page;
    setInventoryPage(page);
    setLoading(true);
    setError(null);
    try {
      const next = await settingsApi.getOpenSandboxNodes();
      const filters = inventoryFiltersRef.current;
      const result =
        next.mode === "legacy"
          ? { items: [], total: 0, skip: page * 25, limit: 25 }
          : await openSandboxApi.listSandboxes({
              skip: page * 25,
              limit: 25,
              node_id: filters.nodeFilter,
              state: filters.stateFilter,
              search: filters.search,
            });
      if (!mounted.current || requestId !== inventoryRequestRef.current) return;
      setServerMode(next.mode);
      setData((current) =>
        draftDirty.current && current
          ? { ...next, mode: current.mode, revision: current.revision }
          : next,
      );
      if (!draftInitialized.current) {
        setDraft(
          next.nodes.map((node) => ({
            id: node.id,
            domain: node.domain,
            image: node.image,
            timeout: node.timeout,
            work_dir: node.work_dir,
            use_server_proxy: node.use_server_proxy,
            max_sandboxes: node.max_sandboxes,
            enabled: node.enabled,
            priority: node.priority,
          })),
        );
        draftInitialized.current = true;
      }
      setInventory(result.items);
      setInventoryTotal(result.total);
    } catch (cause) {
      if (mounted.current && requestId === inventoryRequestRef.current) {
        setError(
          cause instanceof Error
            ? cause.message
            : translateRef.current("common.error", "请求失败"),
        );
      }
    } finally {
      if (mounted.current && requestId === inventoryRequestRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (document.visibilityState === "visible") void load({ type: "refresh" });
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load({ type: "refresh" });
    }, 10_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void load({ type: "refresh" });
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);

  useEffect(() => {
    if (!filtersInitialized.current) {
      filtersInitialized.current = true;
      return;
    }
    inventoryPageRef.current = 0;
    setInventoryPage(0);
    if (document.visibilityState === "visible") {
      void load({ type: "filters-changed" });
    }
  }, [load, nodeFilter, search, stateFilter]);

  const updateNode = (index: number, patch: Partial<OpenSandboxNodeInput>) => {
    draftDirty.current = true;
    setDraft((current) =>
      current.map((node, i) => (i === index ? { ...node, ...patch } : node)),
    );
  };

  const save = async () => {
    try {
      const next = await settingsApi.updateOpenSandboxNodes(
        data?.mode ?? "multi_node",
        draft,
        data?.revision,
      );
      setData(next);
      setServerMode(next.mode);
      setDraft(
        next.nodes.map((node) => ({
          id: node.id,
          domain: node.domain,
          image: node.image,
          timeout: node.timeout,
          work_dir: node.work_dir,
          use_server_proxy: node.use_server_proxy,
          max_sandboxes: node.max_sandboxes,
          enabled: node.enabled,
          priority: node.priority,
        })),
      );
      draftDirty.current = false;
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : t("common.error", "保存失败"),
      );
    }
  };

  const runAction = async (
    item: OpenSandboxInventoryItem,
    action: "pause" | "resume" | "renew" | "terminate",
  ) => {
    const key = `${item.node_id}:${item.sandbox_id}`;
    setActionKey(key);
    try {
      if (action === "pause") await openSandboxApi.pause(item.node_id, item.sandbox_id);
      if (action === "resume") await openSandboxApi.resume(item.node_id, item.sandbox_id);
      if (action === "renew") await openSandboxApi.renew(item.node_id, item.sandbox_id);
      if (action === "terminate") {
        await openSandboxApi.terminate(item.node_id, item.sandbox_id);
      }
      await load({ type: "refresh" });
    } catch (cause) {
      setError(describeActionError(cause, t("common.error", "操作失败")));
    } finally {
      if (mounted.current) setActionKey(null);
      setConfirmSandbox(null);
    }
  };

  const forgetLocal = async (item: OpenSandboxInventoryItem) => {
    const key = `${item.node_id}:${item.sandbox_id}`;
    setActionKey(key);
    try {
      await openSandboxApi.terminate(item.node_id, item.sandbox_id, {
        local_only: true,
      });
      await load({ type: "refresh" });
    } catch (cause) {
      setError(describeActionError(cause, t("common.error", "操作失败")));
    } finally {
      if (mounted.current) setActionKey(null);
      setConfirmForget(null);
    }
  };

  const removeNode = async (nodeId: string) => {
    draftDirty.current = true;
    setDraft((current) => current.filter((node) => node.id !== nodeId));
    setConfirmNode(null);
  };

  const forceRemoveNode = async (nodeId: string) => {
    setForceRemoveBusy(true);
    try {
      if (!data?.revision) {
        throw new Error(t("common.error", "操作失败"));
      }
      const next = await settingsApi.forceRemoveOpenSandboxNode(nodeId, {
        confirm: true,
        expected_revision: data.revision,
      });
      draftDirty.current = false;
      draftInitialized.current = false;
      setConfirmForceRemove(null);
      // Adopt the bumped revision (and possible legacy switch) right away so a
      // second force-remove does not send a stale expected_revision.
      if (next) {
        setData(next);
        setServerMode(next.mode);
      }
      await load({ type: "refresh" });
    } catch (cause) {
      setError(describeActionError(cause, t("common.error", "操作失败")));
      setConfirmForceRemove(null);
    } finally {
      if (mounted.current) setForceRemoveBusy(false);
    }
  };

  const probeNode = async (nodeId: string) => {
    try {
      await settingsApi.probeOpenSandboxNode(nodeId);
      await load({ type: "refresh" });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : t("common.error", "探测失败"),
      );
    }
  };

  const toggleDrain = async (nodeId: string, draining: boolean) => {
    try {
      await settingsApi.drainOpenSandboxNode(nodeId, draining);
      await load({ type: "refresh" });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : t("common.error", "操作失败"),
      );
    }
  };

  const pageCount = Math.max(1, Math.ceil(inventoryTotal / 25));
  const isLegacy = (data?.mode ?? "multi_node") === "legacy";
  const formatTimestamp = (value?: string | null) => {
    if (!value) return copy.never;
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return copy.never;
    return new Intl.DateTimeFormat(i18n.language, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(parsed);
  };

  const renderStatus = (node?: OpenSandboxNode, maxSandboxes?: number) => (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className={`rounded-full px-2 py-0.5 ${healthTone(node?.health_state)}`}>
        {copy.health}: {node?.health_state ?? copy.healthUnknown}
      </span>
      <span className="text-stone-500 dark:text-stone-400">
        {copy.lastHealth}: {formatTimestamp(node?.last_health_at)}
      </span>
      <span className="text-stone-500 dark:text-stone-400">
        {copy.capacity}: {node?.used_sandboxes ?? 0}/{maxSandboxes ?? node?.max_sandboxes ?? 0}
      </span>
      {node?.over_capacity && (
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
          {copy.overCapacity}
        </span>
      )}
      {node?.last_error && (
        <span className="max-w-full truncate text-red-600 dark:text-red-400" title={node.last_error}>
          {copy.error}: {node.last_error}
        </span>
      )}
    </div>
  );

  return (
    <section className="space-y-4" aria-label={copy.title}>
      <div className="glass-card rounded-xl p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
              <Server size={20} />
            </div>
            <div>
              <h3 className="font-semibold text-stone-900 dark:text-stone-100">
                {copy.title}
              </h3>
              <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
                {copy.description}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
              {copy.mode}
              <div className="mt-1 min-w-[11rem]">
                <GlassSelect
                  value={data?.mode ?? "multi_node"}
                  onChange={(value) => {
                    draftDirty.current = true;
                    setData((current) =>
                      current
                        ? {
                            ...current,
                            mode: value as OpenSandboxNodesResponse["mode"],
                          }
                        : current,
                    );
                  }}
                  options={[
                    { value: "multi_node", label: copy.multiNode },
                    { value: "legacy", label: copy.legacy },
                  ]}
                />
              </div>
            </label>
            <button
              type="button"
              className="btn-secondary mt-5 flex items-center gap-2 px-3 py-2 text-sm"
              onClick={() => void load({ type: "refresh" })}
              disabled={loading}
              title={t("common.refresh", "刷新")}
            >
              <RefreshCw size={16} />
              {copy.refreshList}
            </button>
            <button
              type="button"
              className="btn-primary mt-5 px-4 py-2 text-sm"
              onClick={() => void save()}
            >
              {copy.save}
            </button>
          </div>
        </div>
      </div>

      {isLegacy && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
          {copy.legacyHint}
        </div>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200"
        >
          {error}
        </p>
      )}

      {isLegacy ? (
        <div className="space-y-3">
          {(data?.nodes ?? []).map((node) => (
            <div key={node.id} className="glass-card space-y-3 rounded-xl p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-medium text-stone-900 dark:text-stone-100">
                    {copy.node} {node.id}
                  </p>
                  <p className="mt-1 break-all text-sm text-stone-500 dark:text-stone-400">
                    {node.domain}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-secondary flex items-center gap-2 px-3 py-2 text-sm"
                  onClick={() => void probeNode(node.id)}
                >
                  <Wifi size={15} />
                  {copy.probe}
                </button>
              </div>
              {renderStatus(node)}
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {draft.map((node, index) => {
            const current = data?.nodes.find((item) => item.id === node.id);
            return (
              <div key={`${node.id}-${index}`} className="glass-card space-y-4 rounded-xl p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium text-stone-900 dark:text-stone-100">
                    {copy.node} {node.id}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn-secondary flex items-center gap-1 px-3 py-1.5 text-sm"
                      onClick={() => void probeNode(node.id)}
                    >
                      <Wifi size={15} />
                      {copy.probe}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary px-3 py-1.5 text-sm"
                      onClick={() => void toggleDrain(node.id, !current?.draining)}
                    >
                      {current?.draining ? copy.undrain : copy.drain}
                    </button>
                    {serverMode === "multi_node" &&
                      usesForceRemoveNode(current) && (
                        <button
                          type="button"
                          className="btn-secondary px-3 py-1.5 text-sm text-red-600 dark:text-red-400"
                          title={copy.forceRemoveHint}
                          aria-label={copy.forceRemoveHint}
                          onClick={() => setConfirmForceRemove(node.id)}
                        >
                          {copy.forceRemove}
                        </button>
                      )}
                    <button
                      type="button"
                      className="btn-secondary px-3 py-1.5 text-sm text-red-600 dark:text-red-400"
                      title={copy.removeNodeHint}
                      aria-label={copy.removeNodeHint}
                      onClick={() => setConfirmNode(node.id)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    ID
                    <input
                      className={inputClass}
                      value={node.id}
                      disabled={Boolean(current)}
                      onChange={(event) => updateNode(index, { id: event.target.value })}
                    />
                  </label>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    {copy.domain}
                    <input
                      className={inputClass}
                      value={node.domain}
                      onChange={(event) =>
                        updateNode(index, { domain: event.target.value })
                      }
                    />
                  </label>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    {copy.apiKey}
                    <input
                      className={inputClass}
                      type="password"
                      placeholder={current?.has_api_key ? copy.configuredSecret : ""}
                      value={node.api_key ?? ""}
                      onChange={(event) =>
                        updateNode(index, {
                          api_key: event.target.value,
                          clear_api_key: false,
                        })
                      }
                    />
                  </label>
                  <label className="flex items-end gap-2 pb-2 text-sm text-stone-600 dark:text-stone-400">
                    <input
                      type="checkbox"
                      checked={Boolean(node.clear_api_key)}
                      onChange={(event) =>
                        updateNode(index, {
                          clear_api_key: event.target.checked,
                          api_key: "",
                        })
                      }
                    />
                    {copy.clearSecret}
                  </label>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    {copy.image}
                    <input
                      className={inputClass}
                      value={node.image}
                      onChange={(event) =>
                        updateNode(index, { image: event.target.value })
                      }
                    />
                  </label>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    {copy.timeout}
                    <input
                      className={inputClass}
                      type="number"
                      min={1}
                      value={node.timeout}
                      onChange={(event) =>
                        updateNode(index, {
                          timeout: Number(event.target.value) || 3600,
                        })
                      }
                    />
                  </label>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    {copy.workDir}
                    <input
                      className={inputClass}
                      value={node.work_dir}
                      onChange={(event) =>
                        updateNode(index, { work_dir: event.target.value })
                      }
                    />
                  </label>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    {copy.maxSandboxes}
                    <input
                      className={inputClass}
                      type="number"
                      min={1}
                      value={node.max_sandboxes}
                      onChange={(event) =>
                        updateNode(index, {
                          max_sandboxes: Number(event.target.value) || 1,
                        })
                      }
                    />
                  </label>
                  <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
                    {copy.priority}
                    <input
                      className={inputClass}
                      type="number"
                      value={node.priority}
                      onChange={(event) =>
                        updateNode(index, {
                          priority: Number(event.target.value) || 0,
                        })
                      }
                    />
                  </label>
                  <div className="flex flex-wrap items-center gap-4 pt-6 text-sm text-stone-600 dark:text-stone-400">
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={node.use_server_proxy}
                        onChange={(event) =>
                          updateNode(index, {
                            use_server_proxy: event.target.checked,
                          })
                        }
                      />
                      {copy.serverProxy}
                    </label>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={node.enabled}
                        onChange={(event) =>
                          updateNode(index, { enabled: event.target.checked })
                        }
                      />
                      {copy.enabled}
                    </label>
                  </div>
                </div>
                {renderStatus(current, node.max_sandboxes)}
              </div>
            );
          })}
          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              className="btn-secondary flex items-center gap-2 px-4 py-2 text-sm"
              onClick={() => {
                draftDirty.current = true;
                setDraft((current) => [...current, blankNode()]);
              }}
            >
              <Plus size={16} />
              {copy.addNode}
            </button>
          </div>
        </div>
      )}

      <div className="glass-card overflow-hidden rounded-xl">
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--glass-border)] p-4">
          <div className="mr-auto">
            <p className="font-medium text-stone-900 dark:text-stone-100">
              {copy.inventory}
            </p>
            <p className="mt-1 text-xs text-stone-500 dark:text-stone-400">
              {isLegacy ? copy.inventoryHintLegacy : copy.inventoryHint}
            </p>
          </div>
          <div className="min-w-[8rem]">
            <GlassSelect
              value={nodeFilter}
              onChange={setNodeFilter}
              options={[
                { value: "", label: copy.allNodes },
                ...(data?.nodes ?? []).map((node) => ({
                  value: node.id,
                  label: node.id,
                })),
              ]}
            />
          </div>
          <div className="min-w-[8rem]">
            <GlassSelect
              value={stateFilter}
              onChange={setStateFilter}
              options={[
                { value: "", label: copy.allStates },
                { value: "running", label: copy.running },
                { value: "paused", label: copy.frozen },
                { value: "unknown", label: copy.unknown },
              ]}
            />
          </div>
          <label className="flex min-w-[12rem] flex-1 items-center gap-2 rounded-lg border border-[var(--glass-border)] bg-[var(--theme-bg-card)] px-3 py-2 text-sm">
            <Search size={14} className="text-stone-400" />
            <input
              className="w-full bg-transparent outline-none"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={copy.searchPlaceholder}
            />
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[960px] w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-stone-400">
                <th className="px-4 py-3">{copy.user}</th>
                <th className="px-4 py-3">{copy.node}</th>
                <th className="px-4 py-3">{copy.sandboxId}</th>
                <th className="px-4 py-3">{copy.providerBinding}</th>
                <th className="px-4 py-3">{copy.createdAt}</th>
                <th className="px-4 py-3">{copy.lastUsedAt}</th>
                <th className="px-4 py-3">{copy.expiresAt}</th>
                <th className="px-4 py-3">{copy.actions}</th>
              </tr>
            </thead>
            <tbody>
              {inventory.length === 0 ? (
                <tr>
                  <td
                    className="px-4 py-10 text-center text-stone-400"
                    colSpan={8}
                  >
                    {copy.emptyInventory}
                  </td>
                </tr>
              ) : (
                inventory.map((item) => {
                  const key = `${item.node_id}:${item.sandbox_id}`;
                  const busy = actionKey === key;
                  return (
                    <tr
                      key={key}
                      className="border-t border-[var(--glass-border)]"
                    >
                      <td
                        className="px-4 py-3 font-medium text-stone-900 dark:text-stone-100"
                        title={item.user_id || undefined}
                      >
                        {item.username || item.user_id || "-"}
                      </td>
                      <td className="px-4 py-3">{item.node_id}</td>
                      <td className="px-4 py-3 font-mono text-xs">
                        {item.sandbox_id}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`rounded-full px-2 py-0.5 text-xs ${statusTone(item.state)}`}>
                          {item.state}
                        </span>
                        <span className="ml-2 text-xs text-stone-400">
                          {item.binding_state || "-"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-stone-500">
                        {formatTimestamp(item.created_at)}
                      </td>
                      <td className="px-4 py-3 text-stone-500">
                        {formatTimestamp(item.last_used_at)}
                      </td>
                      <td className="px-4 py-3 text-stone-500">
                        {formatTimestamp(item.expires_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-1">
                          {item.actions.pause && (
                            <button
                              type="button"
                              className="rounded-lg p-1.5 text-stone-600 hover:bg-stone-100 disabled:opacity-40 dark:text-stone-300 dark:hover:bg-stone-800"
                              disabled={busy}
                              title={copy.freezeHint}
                              aria-label={copy.freezeHint}
                              onClick={() => void runAction(item, "pause")}
                            >
                              <Pause size={15} />
                            </button>
                          )}
                          {item.actions.resume && (
                            <button
                              type="button"
                              className="rounded-lg p-1.5 text-stone-600 hover:bg-stone-100 disabled:opacity-40 dark:text-stone-300 dark:hover:bg-stone-800"
                              disabled={busy}
                              title={copy.resume}
                              aria-label={copy.resume}
                              onClick={() => void runAction(item, "resume")}
                            >
                              <Play size={15} />
                            </button>
                          )}
                          {item.actions.renew && (
                            <button
                              type="button"
                              className="rounded-lg p-1.5 text-stone-600 hover:bg-stone-100 disabled:opacity-40 dark:text-stone-300 dark:hover:bg-stone-800"
                              disabled={busy}
                              title={copy.renew}
                              aria-label={copy.renew}
                              onClick={() => void runAction(item, "renew")}
                            >
                              <RotateCw size={15} />
                            </button>
                          )}
                          {item.actions.terminate && (
                            <button
                              type="button"
                              className="rounded-lg p-1.5 text-red-500 hover:bg-red-50 disabled:opacity-40 dark:hover:bg-red-950/40"
                              disabled={busy}
                              title={copy.terminateHint}
                              aria-label={copy.terminateHint}
                              onClick={() => setConfirmSandbox(item)}
                            >
                              <Trash2 size={15} />
                            </button>
                          )}
                          {canForgetLocalInventoryRow(item, data?.nodes) && (
                            <button
                              type="button"
                              className="rounded-lg px-1.5 py-1 text-xs text-red-600 hover:bg-red-50 disabled:opacity-40 dark:text-red-400 dark:hover:bg-red-950/40"
                              disabled={busy}
                              title={copy.forgetLocalHint}
                              aria-label={copy.forgetLocalHint}
                              onClick={() => setConfirmForget(item)}
                            >
                              {copy.forgetLocalHint}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-[var(--glass-border)] px-4 py-3 text-sm text-stone-500">
          <span>
            {inventoryTotal} {copy.rows}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-secondary"
              disabled={inventoryPage === 0}
              onClick={() =>
                void load({ type: "navigate", page: inventoryPage - 1 })
              }
            >
              {t("common.previous", "上一页")}
            </button>
            <span>
              {inventoryPage + 1}/{pageCount}
            </span>
            <button
              type="button"
              className="btn-secondary"
              disabled={inventoryPage + 1 >= pageCount}
              onClick={() =>
                void load({ type: "navigate", page: inventoryPage + 1 })
              }
            >
              {t("common.next", "下一页")}
            </button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        isOpen={Boolean(confirmSandbox)}
        title={copy.terminateTitle}
        message={copy.terminateWarning}
        variant="danger"
        confirmText={copy.confirmTerminate}
        onConfirm={() =>
          confirmSandbox && void runAction(confirmSandbox, "terminate")
        }
        onCancel={() => setConfirmSandbox(null)}
      />
      <ConfirmDialog
        isOpen={Boolean(confirmForget)}
        title={copy.forgetLocalTitle}
        message={copy.forgetLocalWarning}
        variant="danger"
        confirmText={copy.confirmForgetLocal}
        onConfirm={() => confirmForget && void forgetLocal(confirmForget)}
        onCancel={() => setConfirmForget(null)}
      />
      <ConfirmDialog
        isOpen={Boolean(confirmNode)}
        title={copy.removeTitle}
        message={copy.removeWarning}
        variant="warning"
        confirmText={copy.remove}
        onConfirm={() => confirmNode && void removeNode(confirmNode)}
        onCancel={() => setConfirmNode(null)}
      />
      <ConfirmDialog
        isOpen={Boolean(confirmForceRemove)}
        title={copy.forceRemoveTitle}
        message={copy.forceRemoveWarning}
        variant="danger"
        confirmText={copy.confirmForceRemove}
        loading={forceRemoveBusy}
        onConfirm={() =>
          confirmForceRemove && void forceRemoveNode(confirmForceRemove)
        }
        onCancel={() => setConfirmForceRemove(null)}
      />
    </section>
  );
}
