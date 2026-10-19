// 纯函数选择器：Agent「执行过程」分组的数据层（design §3/§5）。
//
// 只消费顶层 `Message.parts`（实时 SSE 与历史重放同构，无需后端改动）。
// 无 React 依赖；文案由组件层用 i18n 落，本模块只返 kind + 结构化摘要参数。
import type {
  Message,
  MessagePart,
  SandboxPart,
  SubagentPart,
  ToolPart,
} from "../../../types/message";
import { isScheduledTaskDraftToolName } from "./items/scheduledTaskDraftResult";

// 分组头能力图标数量上限，超出以 +N 收敛（prd R2.1）。
export const MAX_CAPABILITY_ICONS = 5;

// Execute 简洁文案截断宽度，与 ExecuteItem pill 口径一致（ExecuteItem.tsx:168）。
export const EXECUTE_COMMAND_PREVIEW_LENGTH = 80;

// 通用/MCP 工具 args 摘要截断宽度（本模块新增规则，design §4.3）。
export const GENERIC_ARG_SUMMARY_LENGTH = 80;

// 过程类 part：thinking / tool（一般）/ subagent / sandbox / summary。
// subagent 不透明透传，禁止展平其内部 parts（design §7.1）。
export interface ProcessPartEntry {
  part: MessagePart;
  // 原始 message.parts 下标。组内渲染 MessagePartRenderer 时必须回传，
  // 自动预览/锚点/流式光标全部依赖原始下标（design §7.2）。
  partIndex: number;
}

export interface BodyPartEntry {
  part: MessagePart;
  partIndex: number;
}

export interface PartitionedMessageParts {
  processEntries: ProcessPartEntry[];
  bodyEntries: BodyPartEntry[];
}

// 能力类别。kind 划分以 MessagePartRenderer.tsx:80-247 的 tool 名 if 链为事实依据。
// MCP 判定（名含 ":"）复用 ToolCallItem.tsx:114-117 的冒号切分逻辑。
export type ProcessCapabilityKind =
  | "thinking"
  | "read"
  | "write"
  | "edit"
  | "grep"
  | "glob"
  | "ls"
  | "execute"
  | "mcp"
  | "tool"
  | "subagent"
  | "sandbox"
  | "summary";

// 单步摘要参数：纯数据，组件层用 t() 落文案。
// 截断规则归属：execute 80 截断在本模块内完成（与 ExecuteItem pill 同口径，
// 组件层直接渲染）；ls 同时返全路径 dirPath 与短名 dirName（分别对应
// LsItem subtitle / 面板 title 形态，组件层按展示位置选用）。
export type ProcessStepSummary =
  | { kind: "thinking" }
  | { kind: "summary" }
  | { kind: "sandbox"; sandboxId?: string }
  | { kind: "subagent"; agentName: string; input: string }
  | { kind: "read" | "write" | "edit"; filePath: string }
  | { kind: "grep"; pattern: string; searchPath: string }
  | { kind: "glob"; pattern: string; searchPath: string }
  | { kind: "ls"; dirPath: string; dirName: string }
  | { kind: "execute"; command: string }
  | { kind: "mcp" | "tool"; toolName: string; argSummary?: string };

export interface CapabilitySummary {
  // 按顶层 parts 顺序去重后的能力类别（不多于 MAX_CAPABILITY_ICONS 个）。
  kinds: ProcessCapabilityKind[];
  // 被上限截掉的类别数（分组头渲染 "+N" 用）。
  overflowCount: number;
}

export interface ProcessProgress {
  // 已结束步数（非 pending / 非 streaming）。
  completed: number;
  // 过程步数总数，与完成态 N 同口径（design §5.2）。
  total: number;
}

export interface CurrentActionSummary {
  entry: ProcessPartEntry;
  summary: ProcessStepSummary;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function truncateCommand(command: string): string {
  if (command.length <= EXECUTE_COMMAND_PREVIEW_LENGTH) return command;
  return `${command.slice(0, EXECUTE_COMMAND_PREVIEW_LENGTH - 3)}…`;
}

function truncateArgSummary(value: string): string {
  const text = value.trim();
  if (text.length <= GENERIC_ARG_SUMMARY_LENGTH) return text;
  return `${text.slice(0, GENERIC_ARG_SUMMARY_LENGTH - 3)}…`;
}

// 通用/MCP 工具 args 摘要：取首个有效字符串参数（design §4.3 适配 1）。
// 查找顺序复用 ToolCallItem 的展示口径：先解 partial（ToolCallItem.tsx:123-132），
// 再按 args 插入顺序取首个非空字符串；MCP 冒号切分在调用方完成，
// 这里只处理已切分后的展示名。
function summarizeFirstStringArg(args: Record<string, unknown>): string | undefined {
  const displayArgs: Record<string, unknown> = (() => {
    const partial = (args as { partial?: unknown }).partial;
    if (typeof partial === "string") {
      try {
        const parsed: unknown = JSON.parse(partial);
        if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
          return parsed as Record<string, unknown>;
        }
      } catch {
        return { partial };
      }
    }
    return args;
  })();
  for (const value of Object.values(displayArgs)) {
    if (typeof value === "string" && value.trim() !== "") {
      return truncateArgSummary(value);
    }
  }
  return undefined;
}

// 与 ToolCallItem.tsx:114-117 同口径的 MCP 判定与展示名切分。
function splitMcpToolName(name: string): { isMcp: boolean; toolName: string } {
  const colonIdx = name.indexOf(":");
  if (colonIdx > 0) {
    return { isMcp: true, toolName: name.substring(colonIdx + 1) };
  }
  return { isMcp: false, toolName: name };
}

// ls 短名，与 LsItem displayLabel 同口径（LsItem.tsx:50-51）。
function basenameOrSelf(dirPath: string): string {
  if (dirPath === "/") return "/";
  return dirPath.split("/").filter(Boolean).pop() || dirPath;
}

// 定时任务草稿工具是否以外置确认卡渲染：
// 与 MessagePartRenderer.tsx:225-234 同条件（非 pending、未失败、结果可解析）。
// 外置卡不进过程集；pending/失败/不可解析时回落通用 ToolCallItem，仍属过程。
function isExposedDraftCard(part: ToolPart): boolean {
  if (!isScheduledTaskDraftToolName(part.name)) return false;
  if (part.isPending || part.success === false) return false;
  const result = part.result;
  if (typeof result !== "string" && (typeof result !== "object" || result === null)) {
    return false;
  }
  const text = typeof result === "string" ? result.trim() : JSON.stringify(result);
  if (!text.startsWith("{")) return false;
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return false;
    }
    const payload = parsed as Record<string, unknown>;
    return (
      payload.entity_type === "scheduled_task_draft" &&
      typeof payload.draft_id === "string" &&
      payload.draft_id !== ""
    );
  } catch {
    return false;
  }
}

// tool part 的过程/外置判定（design §3 分区表）。
function isProcessToolPart(part: ToolPart): boolean {
  // 产物卡片外置（prd R4.2）。
  if (part.name === "reveal_file" || part.name === "reveal_project") return false;
  // 待确认草稿卡外置（prd R7.2）。
  if (isExposedDraftCard(part)) return false;
  return true;
}

// 按 design §3 分区表把顶层 parts 分为过程集与正文/外置集，各保原序与原始下标。
export function partitionMessageParts(parts: readonly MessagePart[]): PartitionedMessageParts {
  const processEntries: ProcessPartEntry[] = [];
  const bodyEntries: BodyPartEntry[] = [];
  parts.forEach((part, partIndex) => {
    switch (part.type) {
      case "thinking":
      case "subagent":
      case "sandbox":
      case "summary":
        processEntries.push({ part, partIndex });
        break;
      case "tool":
        if (isProcessToolPart(part)) {
          processEntries.push({ part, partIndex });
        } else {
          bodyEntries.push({ part, partIndex });
        }
        break;
      // text / todo / cancelled / recommend_questions / token_usage：
      // 正文区原位保留或现有挂载点不动，一律归 body（design §3）。
      default:
        bodyEntries.push({ part, partIndex });
        break;
    }
  });
  return { processEntries, bodyEntries };
}

// 流式门控上下文：与 MessagePartRenderer 的 isStreaming && isLast
// 门控同口径（MessagePartRenderer.tsx:253/294/307）。
// thinking/summary 的 part.isStreaming 只在"消息仍在流式且该条目是消息
// 最后一个 part"时才算真正 streaming；否则（如已完成消息中残留
// isStreaming=true 的 thinking）应按已结束处理。
export interface StreamGatingContext {
  isMessageStreaming?: boolean;
  // 该条目是否为消息最后一个 part（调用方用 entry.partIndex === partCount - 1 算出）。
  isLast?: boolean;
}

// thinking/summary 是否处于"真正 streaming"：
// 有 ctx 时按三者合取判定；无 ctx 时（旧调用方/旧测试）回退为 part.isStreaming。
function isActivelyStreamingPart(
  entry: ProcessPartEntry,
  ctx?: StreamGatingContext,
): boolean {
  const part = entry.part;
  if (part.type !== "thinking" && part.type !== "summary") return false;
  if (ctx === undefined) return part.isStreaming === true;
  return part.isStreaming === true && ctx.isMessageStreaming === true && ctx.isLast === true;
}

// 单步是否已结束（执行中「已完成 X」的 X 口径）。
// tool/subagent 看 isPending（clearAllLoadingStates 后 pending 全清）；
// thinking/summary 经 isActivelyStreamingPart 与原始渲染同门控；
// sandbox 看 status 是否离开 starting。
// cancelled 的 tool 视为已结束（终态，不再是"当前动作"）。
export function isProcessEntryFinished(
  entry: ProcessPartEntry,
  ctx?: StreamGatingContext,
): boolean {
  const part = entry.part;
  switch (part.type) {
    case "tool":
    case "subagent":
      return !part.isPending;
    case "thinking":
    case "summary":
      return !isActivelyStreamingPart(entry, ctx);
    case "sandbox":
      return part.status !== "starting";
    default:
      return true;
  }
}

// 步数 N = 过程条目数，各计 1 步；子代理内部不递归（design §5.2/R6.3）。
export function countProcessSteps(processEntries: readonly ProcessPartEntry[]): number {
  return processEntries.length;
}

// 执行中进度：completed = 已结束条目数，total 与完成态 N 同口径（design §5.2）。
export function resolveProcessProgress(
  processEntries: readonly ProcessPartEntry[],
  ctx?: StreamGatingContext,
): ProcessProgress {
  const total = countProcessSteps(processEntries);
  let completed = 0;
  for (const entry of processEntries) {
    if (isProcessEntryFinished(entry, ctx)) completed += 1;
  }
  return { completed, total };
}

// 过程条目 → 能力类别（design §5.3）。
// subagent 只占一位，不递归其内部 parts（design §5.3/R6.3）。
export function getEntryCapability(entry: ProcessPartEntry): ProcessCapabilityKind {
  const part = entry.part;
  switch (part.type) {
    case "thinking":
      return "thinking";
    case "summary":
      return "summary";
    case "sandbox":
      return "sandbox";
    case "subagent":
      return "subagent";
    case "tool": {
      switch (part.name) {
        case "read_file":
          return "read";
        case "write_file":
          return "write";
        case "edit_file":
          return "edit";
        case "grep":
          return "grep";
        case "glob":
          return "glob";
        case "ls":
          return "ls";
        case "execute":
          return "execute";
        default:
          return splitMcpToolName(part.name).isMcp ? "mcp" : "tool";
      }
    }
    default:
      return "tool";
  }
}

// 能力图标组：按顶层 parts 顺序取类别去重，上限 MAX_CAPABILITY_ICONS，超出计数 +N。
export function summarizeCapabilities(
  processEntries: readonly ProcessPartEntry[],
): CapabilitySummary {
  const kinds: ProcessCapabilityKind[] = [];
  let distinctTotal = 0;
  for (const entry of processEntries) {
    const kind = getEntryCapability(entry);
    if (!kinds.includes(kind)) {
      distinctTotal += 1;
      if (kinds.length < MAX_CAPABILITY_ICONS) kinds.push(kind);
    }
  }
  return { kinds, overflowCount: distinctTotal - kinds.length };
}

// 单步摘要参数（design §5.3：纯函数只返 kind + 摘要参数，组件层 t() 落文案）。
export function getStepSummary(entry: ProcessPartEntry): ProcessStepSummary {
  const part = entry.part;
  switch (part.type) {
    case "thinking":
      return { kind: "thinking" };
    case "summary":
      return { kind: "summary" };
    case "sandbox": {
      const sandbox = part as SandboxPart;
      return { kind: "sandbox", sandboxId: sandbox.sandbox_id };
    }
    case "subagent": {
      const subagent = part as SubagentPart;
      return { kind: "subagent", agentName: subagent.agent_name, input: subagent.input };
    }
    case "tool": {
      const tool = part as ToolPart;
      const args = tool.args ?? {};
      switch (tool.name) {
        case "read_file":
        case "write_file":
        case "edit_file":
          return {
            kind: tool.name === "read_file" ? "read" : tool.name === "write_file" ? "write" : "edit",
            filePath: asString(args.file_path),
          };
        case "grep":
          return {
            kind: "grep",
            pattern: asString(args.pattern),
            searchPath: asString(args.path),
          };
        case "glob":
          return {
            kind: "glob",
            pattern: asString(args.pattern),
            searchPath: asString(args.path),
          };
        case "ls": {
          const dirPath = asString(args.path) || "/";
          return { kind: "ls", dirPath, dirName: basenameOrSelf(dirPath) };
        }
        case "execute":
          return { kind: "execute", command: truncateCommand(asString(args.command)) };
        default: {
          const { isMcp, toolName } = splitMcpToolName(tool.name);
          return {
            kind: isMcp ? "mcp" : "tool",
            toolName,
            argSummary: summarizeFirstStringArg(args),
          };
        }
      }
    }
    default:
      return { kind: "tool", toolName: part.type };
  }
}

// 失败步数 F = success===false 的 tool 条目数 + error 状态的 sandbox 条目数（design §5.5）。
export function countFailedSteps(processEntries: readonly ProcessPartEntry[]): number {
  let failed = 0;
  for (const entry of processEntries) {
    const part = entry.part;
    if (part.type === "tool") {
      if ((part as ToolPart).success === false) failed += 1;
    } else if (part.type === "sandbox") {
      if ((part as SandboxPart).status === "error") failed += 1;
    }
  }
  return failed;
}

function toMs(value: string | number | undefined): number | undefined {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : undefined;
  }
  if (typeof value === "string" && value !== "") {
    const ms = new Date(value).getTime();
    return Number.isNaN(ms) ? undefined : ms;
  }
  return undefined;
}

// 耗时口径（design §5.6/prd R6.2）：
// 优先 message.duration（token:usage）；缺失时回退过程条目
// max(completedAt) - min(startedAt)；两者皆无返回 undefined（不显示假值）。
// thinking/summary/sandbox 无起止区间，忽略这些条目。
export function resolveProcessDurationMs(message: Pick<Message, "duration" | "parts">): number | undefined {
  if (typeof message.duration === "number" && Number.isFinite(message.duration)) {
    return message.duration;
  }
  const parts = message.parts ?? [];
  let minStart: number | undefined;
  let maxEnd: number | undefined;
  for (const part of parts) {
    if (part.type !== "tool" && part.type !== "subagent") continue;
    const start = toMs(part.startedAt);
    const end = toMs(part.completedAt);
    if (start !== undefined && (minStart === undefined || start < minStart)) {
      minStart = start;
    }
    if (end !== undefined && (maxEnd === undefined || end > maxEnd)) {
      maxEnd = end;
    }
  }
  if (minStart === undefined || maxEnd === undefined) return undefined;
  const ms = maxEnd - minStart;
  return ms >= 0 ? ms : undefined;
}

// 当前动作：最后一个未完成过程条目的摘要（design §5.4）。
// 无未完成条目时返回 null，组件层回退为分组标题本身，不另造文案。
export function getCurrentActionSummary(
  processEntries: readonly ProcessPartEntry[],
  ctx?: StreamGatingContext,
): CurrentActionSummary | null {
  for (let i = processEntries.length - 1; i >= 0; i -= 1) {
    const entry = processEntries[i];
    if (!entry) continue;
    if (!isProcessEntryFinished(entry, ctx)) {
      return { entry, summary: getStepSummary(entry) };
    }
  }
  return null;
}

// 段耗时（多段消息用）：min(startedAt) → max(completedAt)，
// 仅统计段内 tool / subagent 条目；thinking/summary/sandbox 无时间戳，忽略。
// 无可用区间时返回 undefined（不显示假值，不复用整消息耗时）。
export function resolveSegmentDurationMs(
  entries: readonly ProcessPartEntry[],
): number | undefined {
  let minStart: number | undefined;
  let maxEnd: number | undefined;
  for (const entry of entries) {
    const part = entry.part;
    if (part.type !== "tool" && part.type !== "subagent") continue;
    const start = toMs(part.startedAt);
    const end = toMs(part.completedAt);
    if (start !== undefined && (minStart === undefined || start < minStart)) {
      minStart = start;
    }
    if (end !== undefined && (maxEnd === undefined || end > maxEnd)) {
      maxEnd = end;
    }
  }
  if (minStart === undefined || maxEnd === undefined) return undefined;
  const ms = maxEnd - minStart;
  return ms >= 0 ? ms : undefined;
}

// —— 返工 R1/R5：按 turn/loop 切分多段 ——
//
// 顶层 parts 里的 body part（text / todo / reveal / cancelled / recommend…）
// 是叙述轮次边界。processEntries 已剔除 body，因此原始 partIndex 不连续处
// 即被 body 隔开 —— 每段最大连续过程条目独立成组，各组按首条目位置交错原序渲染。
// 单段消息退化为一段，行为与单组一致。
export type ProcessSegment = ProcessPartEntry[];

export function partitionProcessSegments(
  processEntries: readonly ProcessPartEntry[],
): ProcessSegment[] {
  const segments: ProcessSegment[] = [];
  let current: ProcessPartEntry[] = [];
  for (const entry of processEntries) {
    const prev = current.length > 0 ? current[current.length - 1] : undefined;
    if (prev !== undefined && entry.partIndex !== prev.partIndex + 1) {
      segments.push(current);
      current = [];
    }
    current.push(entry);
  }
  if (current.length > 0) segments.push(current);
  return segments;
}

// 段覆盖态 key：messageId + 段首原始 partIndex。
// parts 只追加不删除，段首下标在流式追加下稳定；段内新增步骤不改变 key。
export function makeProcessSegmentKey(
  messageId: string,
  segment: ProcessSegment,
): string {
  const first = segment.length > 0 ? segment[0]?.partIndex : undefined;
  return `${messageId}#${first ?? 0}`;
}

// —— 返工 R3：简洁行状态与单步耗时（纯函数，组件层只落样式/文案） ——

// 简洁行状态：与各 Item 的 pill 状态推导同口径
//（ToolCallItem.tsx:137-146、Read/Edit/Write/Grep/Ls/Glob/Execute 各 Item、
// ThinkingBlock/SummaryItem/SubagentBlocks），idle 表"未定"（成功标记缺失时
// 不谎报 error，countFailedSteps 仍以 success===false 为准）。
export type ProcessStepStatus =
  | "loading"
  | "success"
  | "error"
  | "cancelled"
  | "idle";

export function getStepStatus(
  entry: ProcessPartEntry,
  ctx?: StreamGatingContext,
): ProcessStepStatus {
  const part = entry.part;
  switch (part.type) {
    case "tool": {
      const tool = part as ToolPart;
      if (tool.isPending) return "loading";
      if (tool.cancelled) return "cancelled";
      if (tool.success === true) return "success";
      if (tool.success === false) return "error";
      return "idle";
    }
    case "thinking":
    case "summary":
      return isActivelyStreamingPart(entry, ctx) ? "loading" : "success";
    case "sandbox": {
      const sandbox = part as SandboxPart;
      if (sandbox.status === "starting") return "loading";
      if (sandbox.status === "ready") return "success";
      if (sandbox.status === "cancelled") return "cancelled";
      return "error";
    }
    case "subagent": {
      const subagent = part as SubagentPart;
      const effective =
        subagent.status ??
        (subagent.isPending
          ? "running"
          : subagent.success
            ? "complete"
            : "error");
      if (effective === "running") return "loading";
      if (effective === "complete") return "success";
      if (effective === "cancelled") return "cancelled";
      if (effective === "pending") return "idle";
      return "error";
    }
    default:
      return "idle";
  }
}

// 单步耗时（毫秒，整数）：仅 tool / subagent 有起止时间戳；
// thinking / summary / sandbox 无单步区间，返回 undefined（行右侧不显示耗时）。
export function computeStepDurationMs(
  entry: ProcessPartEntry,
): number | undefined {
  const part = entry.part;
  if (part.type !== "tool" && part.type !== "subagent") return undefined;
  const start = toMs(part.startedAt);
  const end = toMs(part.completedAt);
  if (start === undefined || end === undefined) return undefined;
  const ms = end - start;
  return ms >= 0 ? ms : undefined;
}

// 类 xlsx 技能工具的图标判定：tool / mcp 摘要的工具名或 args 摘要带
// .xls/.xlsx 后缀时，行图标与能力组图标用表格 glyph（FileSpreadsheet）。
// 描述文案仍走 display name（ToolCallItem 口径），只换图标。
export function isSpreadsheetStep(summary: ProcessStepSummary): boolean {
  if (summary.kind !== "tool" && summary.kind !== "mcp") return false;
  return (
    /\.xlsx?(\b|$)/i.test(summary.toolName) ||
    (typeof summary.argSummary === "string" &&
      /\.xlsx?(\b|$)/i.test(summary.argSummary))
  );
}

// —— 通用/MCP 工具按"做了什么"分类（用户定稿：按技能内容分类） ——
//
// 只用于图标展示：dedicated 工具保持原 kind（getEntryCapability 不动）；
// 通用/MCP 工具（kind mcp/tool）在图标层经本函数映射到内容 kind，
// 命中则用对应既有工具图标，未命中返回 null（mcp 回退 WandSparkles，
// 通用工具回退 Wrench）。规则=工具名小写子串，首个命中为准
//（数组顺序即优先级，deterministic；图标展示用途，宁可近似不求精确）。
export type GenericContentKind =
  | "read"
  | "write"
  | "edit"
  | "grep"
  | "glob"
  | "ls"
  | "execute";

const GENERIC_TOOL_KIND_RULES: ReadonlyArray<{
  kind: GenericContentKind;
  match: readonly string[];
}> = [
  { kind: "read", match: ["read", "get", "fetch", "load", "open", "view", "download", "file"] },
  { kind: "write", match: ["write", "create", "update", "save", "upload", "append"] },
  { kind: "edit", match: ["edit", "modify", "replace", "patch", "diff", "rename"] },
  { kind: "execute", match: ["execut", "exec", "shell", "bash", "terminal", "command", "script", "build", "deploy", "run"] },
  { kind: "grep", match: ["search", "query", "find", "grep", "lookup", "scan"] },
  { kind: "glob", match: ["glob", "pattern"] },
  { kind: "ls", match: ["list", "dir", "tree", "browse"] },
];

export function classifyGenericToolKind(toolName: string): GenericContentKind | null {
  const name = toolName.toLowerCase();
  for (const rule of GENERIC_TOOL_KIND_RULES) {
    for (const sub of rule.match) {
      if (name.includes(sub)) return rule.kind;
    }
  }
  return null;
}

// 段覆盖态 key：单段消息退化为 messageId 本身（与分组前旧覆盖态通道兼容）；
// 多段消息按段首原始下标隔离（parts 只追加不删除，段首下标在流式追加下稳定）。
export function resolveSegmentGroupKey(
  messageId: string,
  segment: ProcessSegment,
  totalSegments: number,
): string {
  if (totalSegments <= 1) return messageId;
  return makeProcessSegmentKey(messageId, segment);
}
