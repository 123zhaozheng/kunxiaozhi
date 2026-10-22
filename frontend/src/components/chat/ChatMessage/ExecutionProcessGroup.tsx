// Agent「执行过程」收纳分组（design §4；2026-10-16 返工后形态）。
//
// 行渲染方案（经 Item 代码调查后的选择，替代旧 CollapsiblePill 复用）：
// 各 Item（Read/Edit/Write/Grep/Ls/Glob/Execute/ToolCallItem/ThinkingBlock/
// SummaryItem/SandboxItem）把 open 面板用的 children（detailContent）封在
// 组件内部 const 里，外部无法调用拿到；且 Item 只能渲染 CollapsiblePill 行
// （emerald 底 pill，正是返工要去掉的形态）。给 Item 加 variant/row 模式
// 需要动 10 个文件，改动面大且污染 Item 默认外观。
// 因此：组内渲染自有的 plain text 简洁行（图标 + 自然描述 + 右侧耗时/状态，
// 无 CollapsiblePill、无 emerald 底），点击经 openPersistentToolPanel 打开
// 侧栏，payload 由 processStepDetails.buildStepPanelPayload 逐 Item 镜像拼装
// （title/icon/status/subtitle/children/footer 与 Item 的 onPanelOpen 传参同源，
// 详情零损失）。CollapsiblePill 全局外观不动；subagent 例外，仍走
// MessagePartRenderer 原样渲染 SubagentBlock 卡片（不进简洁行）。
//
// 形态（参考图 Image 2）：无边框分组，头行（能力图标 chips + 状态文案 +
// chevron）+ 展开区缩进行列表（左侧细引导线），无卡片背景、无 ring。
import { useEffect, useMemo, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { clsx } from "clsx";
import {
  Ban,
  Bot,
  Box,
  Brain,
  Check,
  ChevronDown,
  CircleX,
  FilePlus,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  FolderSearch,
  LoaderCircle,
  Pencil,
  Search,
  SquareTerminal,
  LibraryBig,
  WandSparkles,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { MessagePartRenderer } from "./MessagePartRenderer";
import {
  getSubagentRoleIconMeta,
  openSubagentPanelByAgentId,
} from "./SubagentBlocks";
import { openPersistentToolPanel } from "./items/persistentToolPanelState";
import {
  applyFailureAutoExpand,
  getProcessGroupOverride,
  setProcessGroupExpanded,
  subscribeProcessGroupOverrides,
} from "./executionProcessPanelState";
import {
  computeStepDurationMs,
  classifyGenericToolKind,
  countFailedSteps,
  getStepStatus,
  getStepSummary,
  isProcessEntryFinished,
  isSpreadsheetStep,
  resolveSegmentDurationMs,
  resolveSegmentGroupKey,
  summarizeCapabilities,
  type ProcessCapabilityKind,
  type ProcessPartEntry,
  type ProcessSegment,
  type ProcessStepStatus,
  type ProcessStepSummary,
  type StreamGatingContext,
} from "./executionProcess";
import {
  buildStepPanelPayload,
  canOpenStep,
  refreshOpenStepPanel,
  subagentDisplayName,
  type StepPanelPart,
  type TranslateFn,
} from "./processStepDetails";
import type { MessagePart } from "../../../types/message";
import type { RevealPreviewRequest } from "./items/revealPreviewData";
import type { RevealPreviewOpenSource } from "./items/revealPreviewState";

export interface ExecutionProcessGroupProps {
  messageId: string;
  // 已分区保序的过程条目（含原始 partIndex，design §7.2）。
  // 多段消息下调用方传入单段（ProcessSegment）；单段消息即全量。
  processEntries: ProcessPartEntry[];
  // 同一消息的总段数（段覆盖态 key 用：1 段时退化为 messageId 本身）。
  segmentCount?: number;
  isStreaming?: boolean;
  // message.duration（来自 token:usage）；缺失时回退过程 part 起止时间。
  durationMs?: number;
  // message.parts.length —— 用于按原始下标推导 subagent 卡片的 isLast。
  messagePartCount: number;
  // —— 以下为组内 subagent 卡片经 MessagePartRenderer 透传的既有 props ——
  activePreview?: RevealPreviewRequest | null;
  onOpenPreview?: (
    preview: RevealPreviewRequest,
    source?: RevealPreviewOpenSource,
  ) => boolean;
  // shouldAllowAutoPreviewForPart 的按 entry 适配（依赖原始 partIndex）。
  allowAutoPreviewForPart?: (partIndex: number) => boolean;
  onRecommendQuestionClick?: (question: string) => void;
  // cancelled 重试注入的按 entry 适配（cancelled part 永不进组，为契约完整保留）。
  onRetryCancelledForPart?: (part: MessagePart) => (() => void) | undefined;
}

// —— 图标映射（返工 R2：参考图形态，不复用 Item pill 图标原样） ——
//
// thinking Brain；read FileText；write FilePlus；edit Pencil；grep Search；
// glob FolderSearch；ls FolderOpen；execute SquareTerminal（新式终端 glyph，
// 0.468.0 实测 EXISTS；TerminalSquare 不存在）；MCP/技能 WandSparkles
//（参考图魔杖；Wand2 不存在禁用）；通用工具 Wrench；
// xlsx 技能 FileSpreadsheet（Table2 未采用）；subagent 角色图标；
// sandbox Box；summary FileText。
// MCP/通用工具按"做了什么"分类展示（用户定稿：按技能内容分类）：
// 映射到内容 kind 后用对应既有工具图标；未命中时 mcp 回退 WandSparkles、
// 通用工具回退 Wrench。xlsx 优先表格 glyph。dedicated 工具不走本函数。
function genericContentIconFor(summary: ProcessStepSummary): LucideIcon | null {
  if (summary.kind !== "mcp" && summary.kind !== "tool") return null;
  if (isSpreadsheetStep(summary)) return FileSpreadsheet;
  const contentKind = classifyGenericToolKind(summary.toolName);
  if (contentKind === null) return null;
  switch (contentKind) {
    case "read":
      return FileText;
    case "write":
      return FilePlus;
    case "edit":
      return Pencil;
    case "grep":
      return Search;
    case "glob":
      return FolderSearch;
    case "ls":
      return FolderOpen;
    case "execute":
      return SquareTerminal;
    default:
      return null;
  }
}

function stepIconFor(
  kind: ProcessCapabilityKind,
  summary: ProcessStepSummary,
): LucideIcon {
  if (kind === "mcp" || kind === "tool") {
    if (isSpreadsheetStep(summary)) return FileSpreadsheet;
    const contentIcon =
      summary.kind === "mcp" || summary.kind === "tool"
        ? genericContentIconFor(summary)
        : null;
    if (contentIcon) return contentIcon;
    if (kind === "mcp") return WandSparkles;
    return Wrench;
  }
  switch (kind) {
    case "thinking":
      return Brain;
    case "read":
      return FileText;
    case "write":
      return FilePlus;
    case "edit":
      return Pencil;
    case "grep":
      return Search;
    case "glob":
      return FolderSearch;
    case "ls":
      return FolderOpen;
    case "execute":
      return SquareTerminal;
    case "knowledge":
      return LibraryBig;
    case "sandbox":
      return Box;
    case "summary":
      return FileText;
    case "subagent":
      return Bot;
    default:
      return Wrench;
  }
}

function capabilityIconFor(
  kind: ProcessCapabilityKind,
  entries: readonly ProcessPartEntry[],
): LucideIcon {
  if (kind === "subagent") {
    const subagentEntry = entries.find((e) => e.part.type === "subagent");
    const agentName =
      subagentEntry && subagentEntry.part.type === "subagent"
        ? subagentEntry.part.agent_name
        : "";
    return getSubagentRoleIconMeta(agentName).icon;
  }
  const entry = entries.find((e) => {
    if (e.part.type === "subagent") return false;
    const s = getStepSummary(e);
    if (kind === "mcp" || kind === "tool") {
      return s.kind === "mcp" || s.kind === "tool";
    }
    return s.kind === kind;
  });
  const summary = entry ? getStepSummary(entry) : undefined;
  if (!summary) return stepIconFor(kind, { kind } as ProcessStepSummary);
  return stepIconFor(kind, summary);
}

// —— 自然描述文案（返工 R3：中文短语，源自各 Item pill 文案语义） ——

function stepDescription(
  summary: ProcessStepSummary,
  t: (key: string, options?: Record<string, unknown>) => string,
  status?: ProcessStepStatus,
): string {
  switch (summary.kind) {
    case "thinking":
      // 与 ThinkingBlock 同口径（SubagentBlocks.tsx:613）：
      // 真正 streaming（门控后 loading）→"思考中"，否则→"已思考"。
      return status === "loading"
        ? t("chat.message.thinking")
        : t("chat.message.thought");
    case "summary":
      return t("chat.message.summary");
    case "read":
    case "write":
    case "edit": {
      const verb =
        summary.kind === "read"
          ? t("chat.message.toolRead")
          : summary.kind === "write"
            ? t("chat.message.toolWrite")
            : t("chat.message.toolEdit");
      const base = summary.filePath
        ? summary.filePath.split("/").filter(Boolean).pop() || summary.filePath
        : "";
      return base ? `${verb}「${base}」` : verb;
    }
    case "grep":
      return summary.pattern
        ? `${t("chat.message.toolSearch")}「${summary.pattern}」`
        : t("chat.message.toolSearch");
    case "glob":
      return summary.pattern
        ? `${t("chat.message.toolGlob")} ${summary.pattern}`
        : t("chat.message.toolGlob");
    case "ls":
      return `${t("chat.message.toolLs")} ${summary.dirName || summary.dirPath}`;
    case "execute": {
      const preview = summary.command || t("chat.message.toolExecute");
      return preview === t("chat.message.toolExecute")
        ? preview
        : `${t("chat.message.toolExecute")} ${preview}`;
    }
    case "knowledge": {
      const q = summary.query;
      switch (summary.op) {
        case "list":
          return t("knowledge.tools.list");
        case "search":
          return q ? t("knowledge.tools.search", { query: q }) : t("knowledge.tools.searchNoQuery");
        case "open":
          if (summary.filename && summary.line) {
            return t("knowledge.tools.openFileAtLine", {
              filename: summary.filename,
              line: summary.line,
            });
          }
          if (summary.filename) {
            return t("knowledge.tools.openFile", { filename: summary.filename });
          }
          return summary.line
            ? t("knowledge.tools.openAtLine", { line: summary.line })
            : t("knowledge.tools.open");
        case "find":
          return q
            ? t("knowledge.tools.find", { query: q, filename: summary.filename })
            : t("knowledge.tools.findNoQuery");
        case "files":
          return q ? t("knowledge.tools.files", { query: q }) : t("knowledge.tools.filesNoQuery");
      }
      return "";
    }
    case "mcp":
    case "tool": {
      const name = summary.toolName
        .split("_")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
        .join(" ");
      return summary.argSummary ? `${name} ${summary.argSummary}` : name;
    }
    case "sandbox":
      return t("chat.sandbox.name");
    case "subagent":
      return subagentDisplayName(summary.agentName);
    default:
      return "";
  }
}

// —— 右侧状态 glyph（小 glyph 色，非 pill 底；loading 用 spinner） ——

function StepStatusGlyph({ status }: { status: ProcessStepStatus }) {
  if (status === "loading") {
    return (
      <LoaderCircle
        size={12}
        className="shrink-0 animate-spin text-amber-500 dark:text-amber-400"
      />
    );
  }
  if (status === "success") {
    return <Check size={12} className="shrink-0 text-emerald-500 dark:text-emerald-400" />;
  }
  if (status === "error") {
    return <CircleX size={12} className="shrink-0 text-red-500 dark:text-red-400" />;
  }
  if (status === "cancelled") {
    return <Ban size={12} className="shrink-0 text-stone-400 dark:text-stone-500" />;
  }
  return null;
}

// 单步耗时文案：一 decimal 秒（"2.4 秒"）；≥60s 用分秒键。
function formatStepDuration(
  ms: number,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) {
    const rounded = Math.round(totalSeconds * 10) / 10;
    return t("chat.message.processStepDuration", {
      seconds: Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1),
    });
  }
  const whole = Math.round(totalSeconds);
  return t("chat.message.processDurationMinutes", {
    minutes: Math.floor(whole / 60),
    seconds: whole % 60,
  });
}

export function ExecutionProcessGroup({
  messageId,
  processEntries,
  segmentCount,
  isStreaming,
  durationMs,
  messagePartCount,
  activePreview,
  onOpenPreview,
  allowAutoPreviewForPart,
  onRecommendQuestionClick,
  onRetryCancelledForPart,
}: ExecutionProcessGroupProps) {
  const { t } = useTranslation();
  const streaming = isStreaming === true;

  // 段覆盖态 key：单段即 messageId（与分组前旧覆盖态兼容），
  // 多段按段首下标隔离。段数由调用方传入（1 段退化，无需判长度）。
  const groupKey = useMemo(
    () =>
      resolveSegmentGroupKey(
        messageId,
        processEntries as ProcessSegment,
        segmentCount ?? 1,
      ),
    [messageId, processEntries, segmentCount],
  );

  // 用户覆盖态（段 key 通道；失败自动展开写入同一通道，design §4.2/§6）。
  const override = useSyncExternalStore(
    subscribeProcessGroupOverrides,
    () => getProcessGroupOverride(groupKey),
    () => getProcessGroupOverride(groupKey),
  );

  const failedCount = useMemo(
    () => countFailedSteps(processEntries),
    [processEntries],
  );
  // thinking/summary 流式门控（与 MessagePartRenderer 同口径）：
  // 只有"消息仍在流式且该条目是消息最后一个 part"时 part.isStreaming
  // 才算真正 streaming。调用方已有 messagePartCount（原始 parts 长度），
  // 逐 entry 用 partIndex 推导 isLast，避免状态/进度/文案三处分歧。
  const gatingFor = useMemo(() => {
    const lastIndex = messagePartCount - 1;
    return (entry: ProcessPartEntry): StreamGatingContext => ({
      isMessageStreaming: streaming,
      isLast: entry.partIndex === lastIndex,
    });
  }, [messagePartCount, streaming]);
  const completedCount = useMemo(() => {
    let done = 0;
    for (const entry of processEntries) {
      if (isProcessEntryFinished(entry, gatingFor(entry))) done += 1;
    }
    return done;
  }, [processEntries, gatingFor]);
  const totalCount = processEntries.length;
  const capabilities = useMemo(
    () => summarizeCapabilities(processEntries),
    [processEntries],
  );
  // 头耗时：多段消息（segmentCount > 1）时用段内起止回退，
  // 避免整消息 duration（token:usage）贴到每个段头；
  // 单段时沿用传入的 durationMs（即整消息口径，本就是该段的）。
  // 段无时间戳（如纯 thinking 段）→ undefined，不显示假值。
  const segmentDurationMs = useMemo(
    () =>
      (segmentCount ?? 1) > 1 ? resolveSegmentDurationMs(processEntries) : durationMs,
    [durationMs, processEntries, segmentCount],
  );
  const durationText = useMemo(() => {
    if (streaming || segmentDurationMs === undefined) return undefined;
    const totalSeconds = segmentDurationMs / 1000;
    if (totalSeconds < 60) {
      const rounded = Math.round(totalSeconds * 10) / 10;
      return t("chat.message.processStepDuration", {
        seconds: Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1),
      });
    }
    const whole = Math.round(totalSeconds);
    return t("chat.message.processDurationMinutes", {
      minutes: Math.floor(whole / 60),
      seconds: whole % 60,
    });
  }, [segmentDurationMs, streaming, t]);
  const capabilityIcons = useMemo(
    () =>
      capabilities.kinds.map((kind) => ({
        kind,
        Icon: capabilityIconFor(kind, processEntries),
      })),
    [capabilities, processEntries],
  );

  // 展开态：覆盖态优先；无覆盖时完成且含失败默认展开，否则跟随流式态。
  const expanded =
    override ?? (!streaming && failedCount > 0 ? true : streaming);

  // 失败自动展开落盘（幂等由 store 保证；依赖只放标量，design §4.2）。
  useEffect(() => {
    applyFailureAutoExpand(groupKey, { isStreaming: streaming, failedCount });
  }, [groupKey, streaming, failedCount]);

  // 点开的思考/总结面板随流式内容与门控状态刷新（否则面板冻结在点开时的快照）。
  useEffect(() => {
    for (const entry of processEntries) {
      const part = entry.part;
      if (part.type !== "thinking" && part.type !== "summary") continue;
      refreshOpenStepPanel(
        part,
        !isProcessEntryFinished(entry, gatingFor(entry)),
        t as TranslateFn,
      );
    }
  }, [processEntries, gatingFor, t]);

  // 收起时 subagent 卡片仍需挂载（隐藏）：SubagentBlock 卸载会清空其面板数据，
  // 已打开的子代理侧栏会变空白，外部跳转也无法再打开该子代理面板。
  const hasSubagent = useMemo(
    () => processEntries.some((entry) => entry.part.type === "subagent"),
    [processEntries],
  );

  if (processEntries.length === 0) return null;

  const handleToggle = () => {
    setProcessGroupExpanded(groupKey, !expanded);
  };

  const handleStepOpen = (entry: ProcessPartEntry) => {
    const part = entry.part;
    if (part.type === "subagent") {
      openSubagentPanelByAgentId(part.agent_id);
      return;
    }
    if (
      part.type !== "tool" &&
      part.type !== "thinking" &&
      part.type !== "summary" &&
      part.type !== "sandbox"
    ) {
      return;
    }
    const summary = getStepSummary(entry);
    const Icon = stepIconFor(summary.kind, summary);
    const payload = buildStepPanelPayload(
      {
        part: part as StepPanelPart,
        isStreaming: !isProcessEntryFinished(entry, gatingFor(entry)),
      },
      {
        Icon16: <Icon size={16} />,
        t: t as (key: string, options?: Record<string, unknown>) => string,
      },
    );
    if (!payload || !payload.canOpen) return;
    openPersistentToolPanel({
      title: payload.title,
      icon: payload.icon,
      status: payload.status,
      subtitle: payload.subtitle,
      panelKey: payload.panelKey,
      children: payload.children,
      footer: payload.footer,
    });
  };

  const headerLabel = streaming
    ? t("chat.message.processTitle")
    : t("chat.message.processViewDetails");
  const stepsText = streaming
    ? t("chat.message.processProgress", {
        done: completedCount,
        total: totalCount,
      })
    : t("chat.message.processSteps", { count: totalCount });

  return (
    <div>
      {/* 分组头：一行，能力图标 chips + 状态文案 + chevron（无边框） */}
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 py-1 text-left"
      >
        <span aria-hidden="true" className="flex shrink-0 items-center gap-1">
          {capabilityIcons.map(({ kind, Icon }) => (
            <span
              key={kind}
              className="flex h-5 w-5 items-center justify-center rounded-md bg-stone-100 dark:bg-stone-800"
            >
              <Icon size={12} className="shrink-0 text-stone-500 dark:text-stone-400" />
            </span>
          ))}
          {capabilities.overflowCount > 0 && (
            <span className="text-[10px] tabular-nums text-theme-text-tertiary">
              {t("chat.message.processCapabilitiesMore", {
                count: capabilities.overflowCount,
              })}
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-theme-text">
          {headerLabel}{" "}
          <span className="font-normal text-theme-text-secondary">
            {stepsText}
          </span>
          {durationText && (
            <span className="font-normal text-theme-text-secondary">
              {" "}
              · {durationText}
            </span>
          )}
          {failedCount > 0 && (
            <span className="font-medium text-red-600 dark:text-red-400">
              {" "}
              ·{" "}
              {t("chat.message.processStepsFailed", { count: failedCount })}
            </span>
          )}
        </span>
        <ChevronDown
          size={14}
          className={clsx(
            "shrink-0 text-theme-text-tertiary transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>

      {/* 展开区：缩进行列表（左侧细引导线），无边框无分隔线 */}
      {(expanded || hasSubagent) && (
        <div
          className={clsx(
            "ml-2.5 border-l border-stone-200 dark:border-stone-700 pl-3 py-0.5",
            !expanded && "hidden",
          )}
        >
          {processEntries.map((entry) => {
            // subagent 保持卡片原样（不做简洁行，由 SubagentBlock 自带面板逻辑）。
            if (entry.part.type === "subagent") {
              return (
                <MessagePartRenderer
                  key={`${messageId}:${entry.partIndex}`}
                  part={entry.part}
                  messageId={messageId}
                  partIndex={entry.partIndex}
                  isStreaming={isStreaming}
                  isLast={entry.partIndex === messagePartCount - 1}
                  allowAutoPreview={allowAutoPreviewForPart?.(entry.partIndex)}
                  activePreview={activePreview}
                  onOpenPreview={onOpenPreview}
                  onRecommendQuestionClick={onRecommendQuestionClick}
                  onRetryCancelled={onRetryCancelledForPart?.(entry.part)}
                />
              );
            }
            if (!expanded) return null;
            const summary = getStepSummary(entry);
            const kind = summary.kind;
            const Icon = stepIconFor(kind, summary);
            const status = getStepStatus(entry, gatingFor(entry));
            const description = stepDescription(
              summary,
              t as (key: string, options?: Record<string, unknown>) => string,
              status,
            );
            const stepMs = computeStepDurationMs(entry);
            const running = status === "loading";
            // 可点性沿用各 Item 的 canExpand 门控（canOpenStep 纯数据检查，
            // 与 buildStepPanelPayload.canOpen 同口径，不拼 payload JSX）；
            // running 行同样可点（面板内看 args + 实时 running 条，ToolCallItem 口径）。
            // 注：subagent 分支在上方已 return，此处 part 不可能是 subagent。
            const openable = canOpenStep(entry.part as StepPanelPart) || running;
            return (
              <button
                key={`${messageId}:${entry.partIndex}`}
                type="button"
                onClick={() => handleStepOpen(entry)}
                disabled={!openable}
                className={clsx(
                  "flex w-full items-center gap-2 py-1 text-left text-[13px] leading-snug",
                  status === "error"
                    ? "text-red-600 dark:text-red-400"
                    : "text-stone-600 dark:text-stone-300",
                  openable
                    ? "cursor-pointer hover:text-stone-900 dark:hover:text-stone-100"
                    : "cursor-default",
                )}
                aria-label={description}
              >
                <Icon
                  size={13}
                  className={clsx(
                    "shrink-0",
                    status === "error"
                      ? "text-red-500 dark:text-red-400"
                      : "text-stone-400 dark:text-stone-500",
                  )}
                />
                <span className="min-w-0 flex-1 truncate">
                  {description}
                </span>
                {running ? (
                  <span className="flex shrink-0 items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    {t("chat.message.processStepRunning")}
                    <StepStatusGlyph status={status} />
                  </span>
                ) : (
                  <span className="flex shrink-0 items-center gap-1.5">
                    {stepMs !== undefined && (
                      <span className="text-xs tabular-nums text-stone-400 dark:text-stone-500">
                        {formatStepDuration(
                          stepMs,
                          t as (
                            key: string,
                            options?: Record<string, unknown>,
                          ) => string,
                        )}
                      </span>
                    )}
                    <StepStatusGlyph status={status} />
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
