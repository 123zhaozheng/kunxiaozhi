// 执行过程简洁行 → ToolResultPanel 详情 payload（返工 R3）。
//
// 选择说明（见 ExecutionProcessGroup.tsx 头注释）：Item 组件把 open 面板
// 用的 children/footer 封在内部 const 里，外部无法调用；且 Item 只能渲染
// CollapsiblePill 行。本模块逐 Item 镜像其 onPanelOpen 传参（title/icon/
// status/subtitle/children/footer），拼装逻辑与各 Item 的 detailContent 同源
//（extractText / stripLineNumbers / DeferredCodeMirrorViewer / ToolResultContent
// / CopyButton / CollapsibleSection），保证详情零损失。
//
// 约束：
// - 只读 part 数据 + t 文案，无 hooks、无本地状态，纯函数返回 payload。
// - 状态推导与各 Item 同口径（isPending→loading；cancelled；success→success；
//   否则 error；thinking/summary 用 isStreaming；sandbox 用 status 映射）。
// - canOpen=false（对应各 Item 的 expandable/canExpand=false，如空目录、
//   无内容 read）时行不可点。
// - ToolCallItem 详情复用其 SubagentBlocks.CollapsibleSection（panel 版，不是
//   common/CollapsiblePill 的遗留内联分支）。
// - 移动端分流沿用 openPersistentToolPanel 既有行为（auto 丢弃 + bottom-sheet）。
import type { ReactNode } from "react";
import { Clock } from "lucide-react";
import { CopyButton } from "../../common";
import { DeferredCodeMirrorViewer } from "../../common/DeferredCodeMirrorViewer";
import { CollapsibleSection } from "./SubagentBlocks";
import { MarkdownContent } from "./MarkdownContent";
import {
  extractPaths,
  extractText,
  stripLineNumbers,
  type McpContentBlock,
  type McpMultiModalResult,
} from "./items/toolUtils";
import { McpBlockPreview, ToolResultContent } from "./items/McpBlockPreview";
import {
  isPersistentToolPanelOpen,
  updatePersistentToolPanel,
} from "./items/persistentToolPanelState";
import { formatSubagentName } from "./SubagentBlocks";
import type { ProcessStepStatus } from "./executionProcess";
import type {
  SandboxPart,
  SubagentPart,
  SummaryPart,
  ThinkingPart,
  ToolPart,
} from "../../../types/message";

export type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

export interface ProcessStepPanelPayload {
  title: string;
  icon: ReactNode;
  status: ProcessStepStatus;
  subtitle?: string;
  panelKey?: string;
  children: ReactNode;
  footer?: ReactNode;
  // 对应各 Item 的 expandable/canExpand 门控；false 时简洁行不可点。
  canOpen: boolean;
}

function durationFooter(
  startedAt: string | undefined,
  completedAt: string | undefined,
): ReactNode {
  if (!startedAt || !completedAt) return undefined;
  const ms = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  if (ms < 0) return undefined;
  const seconds = Math.round(ms / 1000);
  const text =
    seconds < 60
      ? `${seconds}s`
      : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return (
    <div className="flex items-center gap-1.5 px-4 py-2 text-xs text-stone-400 dark:text-stone-500 border-t border-stone-100 dark:border-stone-800">
      <Clock size={11} className="shrink-0" />
      <span className="tabular-nums">{text}</span>
    </div>
  );
}

function toolStatus(part: ToolPart): ProcessStepStatus {
  if (part.isPending) return "loading";
  if (part.cancelled) return "cancelled";
  if (part.success === true) return "success";
  if (part.success === false) return "error";
  return "idle";
}

function fileNameOf(filePath: string): string {
  return filePath.split("/").pop() || filePath;
}

function pathRow(filePath: string, extra?: ReactNode): ReactNode {
  return (
    <div className="group/args relative flex items-center gap-2 px-3 py-2 rounded-lg bg-stone-100 dark:bg-stone-800 text-sm text-stone-500 dark:text-stone-400 font-mono">
      <span className="truncate">{filePath}</span>
      {extra}
      <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/args:opacity-100 transition-opacity">
        <CopyButton text={filePath} size={12} />
      </div>
    </div>
  );
}

function codeBlock(value: string, filePath?: string): ReactNode {
  return (
    <div className="relative group rounded-lg border border-stone-200/60 dark:border-stone-700/50 overflow-hidden">
      <DeferredCodeMirrorViewer
        value={value}
        filePath={filePath}
        lineNumbers={true}
        fontSize="0.8rem"
      />
      <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
        <CopyButton
          text={value}
          size={14}
          className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md !border !border-stone-200 dark:!border-stone-700"
        />
      </div>
    </div>
  );
}

function resultPre(text: string): ReactNode {
  return (
    <pre className="group/result relative text-xs text-stone-500 dark:text-stone-400 whitespace-pre-wrap break-words p-3 rounded-lg bg-stone-50 dark:bg-stone-900 border border-stone-200/60 dark:border-stone-700/50">
      {text}
      <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/result:opacity-100 transition-opacity">
        <CopyButton
          text={text}
          size={12}
          className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md"
        />
      </div>
    </pre>
  );
}

function resultTextNode(result: ToolPart["result"]): ReactNode {
  if (!result) return null;
  const text = extractText(result);
  return text ? resultPre(text) : null;
}

// —— read_file（镜像 ReadFileItem.tsx detailContent/panel 传参） ——

// 图片块检测（镜像 ReadFileItem.tsx:59-84 imageBlocks 逻辑）：
function extractReadImageBlocks(
  result: ToolPart["result"],
): McpContentBlock[] {
  if (
    typeof result === "object" &&
    result !== null &&
    "blocks" in result &&
    Array.isArray((result as McpMultiModalResult).blocks)
  ) {
    return (result as McpMultiModalResult).blocks!.filter(
      (b: McpContentBlock) => b.type === "image",
    );
  }
  if (
    Array.isArray(result) &&
    result.length > 0 &&
    typeof result[0] === "object" &&
    result[0] !== null &&
    "type" in result[0]
  ) {
    return (result as McpContentBlock[]).filter((b) => b.type === "image");
  }
  return [];
}

function buildReadDetail(args: Record<string, unknown>, result: ToolPart["result"]): { filePath: string; fileName: string; children: ReactNode; canOpen: boolean } {
  const filePath = (args.file_path as string) || "";
  const fileName = fileNameOf(filePath);
  const offset = args.offset as number | undefined;
  const limit = args.limit as number | undefined;
  const raw = extractText(result);
  const displayContent = raw ? stripLineNumbers(raw) : "";
  const imageBlocks = extractReadImageBlocks(result);
  const hasContent = !!displayContent || imageBlocks.length > 0;
  const children = hasContent && (
    <div className="p-4 sm:p-5 space-y-3">
      {pathRow(
        filePath,
        (offset !== undefined || limit !== undefined) && (
          <span className="shrink-0 text-stone-400 dark:text-stone-500">
            :L{offset ?? 1}
            {limit ? `-${(offset ?? 1) + limit}` : ""}
          </span>
        ),
      )}
      {imageBlocks.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {imageBlocks.map((block, i) => (
            <McpBlockPreview key={i} block={block} />
          ))}
        </div>
      )}
      {displayContent &&
        codeBlock(displayContent, filePath)}
    </div>
  );
  return { filePath, fileName, children, canOpen: hasContent };
}

// —— write_file（镜像 WriteFileItem.tsx） ——

function buildWriteDetail(args: Record<string, unknown>, result: ToolPart["result"]): { filePath: string; fileName: string; children: ReactNode; canOpen: boolean } {
  const filePath = (args.file_path as string) || "";
  const fileName = fileNameOf(filePath);
  const content = (args.content as string) || "";
  const canOpen = !!content || !!result;
  const children = canOpen && (
    <div className="p-4 sm:p-5 space-y-3">
      {pathRow(filePath)}
      {content && codeBlock(content, filePath)}
      {resultTextNode(result)}
    </div>
  );
  return { filePath, fileName, children, canOpen };
}

// —— edit_file（镜像 EditFileItem.tsx 红/绿 diff 块） ——

function buildEditDetail(args: Record<string, unknown>, result: ToolPart["result"], t: TranslateFn): { filePath: string; fileName: string; children: ReactNode; canOpen: boolean } {
  const filePath = (args.file_path as string) || "";
  const fileName = fileNameOf(filePath);
  const oldString = (args.old_string as string) || "";
  const newString = (args.new_string as string) || "";
  const canOpen = !!oldString || !!newString || !!result;
  const children = canOpen && (
    <div className="p-4 sm:p-5 space-y-3">
      {pathRow(filePath)}
      {oldString && (
        <div>
          <div className="text-xs text-red-500 dark:text-red-400 mb-1.5 font-semibold uppercase tracking-wider">
            {t("chat.message.toolEditRemoved")}
          </div>
          <div className="relative group rounded-lg border border-red-200/60 dark:border-red-800/40 bg-red-50 dark:bg-red-950/30 overflow-hidden">
            <DeferredCodeMirrorViewer
              value={oldString}
              filePath={filePath}
              lineNumbers={false}
              fontSize="0.8rem"
              className="[&_.cm-editor]:bg-transparent dark:[&_.cm-editor]:bg-transparent"
            />
            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <CopyButton
                text={oldString}
                size={14}
                className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md !border !border-red-200 dark:!border-red-800"
              />
            </div>
          </div>
        </div>
      )}
      {newString && (
        <div>
          <div className="text-xs text-emerald-500 dark:text-emerald-400 mb-1.5 font-semibold uppercase tracking-wider">
            {t("chat.message.toolEditAdded")}
          </div>
          <div className="relative group rounded-lg border border-emerald-200/60 dark:border-emerald-800/40 bg-emerald-50 dark:bg-emerald-950/30 overflow-hidden">
            <DeferredCodeMirrorViewer
              value={newString}
              filePath={filePath}
              lineNumbers={false}
              fontSize="0.8rem"
              className="[&_.cm-editor]:bg-transparent dark:[&_.cm-editor]:bg-transparent"
            />
            <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
              <CopyButton
                text={newString}
                size={14}
                className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md !border !border-emerald-200 dark:!border-emerald-800"
              />
            </div>
          </div>
        </div>
      )}
      {resultTextNode(result)}
    </div>
  );
  return { filePath, fileName, children, canOpen };
}

// —— grep（镜像 GrepItem.tsx：pattern 头 + 文件 chips + content 行） ——

interface GrepParsed {
  files: string[];
  lines: string[];
}

function parseGrepResult(result: ToolPart["result"]): GrepParsed {
  if (!result) return { files: [], lines: [] };
  const raw = extractText(result);
  try {
    const obj: unknown = JSON.parse(raw);
    if (Array.isArray(obj)) {
      const files: string[] = [];
      const lines: string[] = [];
      for (const item of obj) {
        if (typeof item === "string") {
          files.push(item);
        } else if (item && typeof item === "object") {
          const file = (item as Record<string, unknown>).file as string;
          if (file) files.push(file);
          const matches = (item as Record<string, unknown>).matches;
          if (Array.isArray(matches)) {
            for (const m of matches) {
              const match = m as Record<string, unknown>;
              lines.push(`${file}:${(match.line as number) ?? ""}:${(match.text as string) ?? ""}`);
            }
          }
        }
      }
      return { files, lines };
    }
  } catch {
    // 非 JSON，按行解析 ripgrep 风格输出
  }
  const lines: string[] = raw.split("\n").filter(Boolean);
  const files = [
    ...new Set(lines.map((l) => l.split(":")[0]).filter(Boolean) as string[]),
  ];
  return { files, lines };
}

function buildGrepDetail(
  args: Record<string, unknown>,
  result: ToolPart["result"],
  t: TranslateFn,
): { pattern: string; searchPath: string; glob: string; children: ReactNode; canOpen: boolean } {
  const pattern = (args.pattern as string) || "";
  const searchPath = (args.path as string) || "";
  const glob = (args.glob as string) || "";
  const outputMode = (args.output_mode as string) || "files_with_matches";
  const parsed = parseGrepResult(result);
  const canOpen = !!pattern || parsed.files.length > 0 || parsed.lines.length > 0;
  const children = canOpen && (
    <div className="p-4 sm:p-5 space-y-3">
      <div className="group/args relative flex items-center gap-2 px-3 py-2 rounded-lg bg-stone-100 dark:bg-stone-800 text-sm text-stone-500 dark:text-stone-400 font-mono flex-wrap">
        <span className="text-violet-600 dark:text-violet-400 font-semibold">
          {pattern}
        </span>
        {searchPath && (
          <span className="text-stone-400 dark:text-stone-500">
            {t("chat.message.toolInPath", { path: searchPath })}
          </span>
        )}
        {glob && (
          <span className="shrink-0 px-2 py-0.5 rounded-md bg-stone-200 dark:bg-stone-700 text-stone-600 dark:text-stone-300 text-xs">
            {glob}
          </span>
        )}
        <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/args:opacity-100 transition-opacity">
          <CopyButton text={pattern} size={12} />
        </div>
      </div>
      {parsed.files.length > 0 && (
        <div>
          <div className="text-xs text-stone-400 dark:text-stone-500 mb-2">
            {t("chat.message.toolFileCount", { count: parsed.files.length })}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {parsed.files.slice(0, 20).map((f, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-stone-100 dark:bg-stone-800 text-xs text-stone-600 dark:text-stone-300 font-mono"
              >
                {f.split("/").pop() || f}
              </span>
            ))}
            {parsed.files.length > 20 && (
              <span className="text-xs text-stone-400 dark:text-stone-500 px-1 py-1">
                {t("chat.message.toolMoreFiles", {
                  count: parsed.files.length - 20,
                })}
              </span>
            )}
          </div>
        </div>
      )}
      {outputMode === "content" && parsed.lines.length > 0 && (
        <div className="relative group rounded-lg border border-stone-200/60 dark:border-stone-700/50 overflow-hidden">
          <DeferredCodeMirrorViewer
            value={parsed.lines.join("\n")}
            lineNumbers={false}
            fontSize="0.8rem"
          />
          <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <CopyButton
              text={parsed.lines.join("\n")}
              size={14}
              className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md !border !border-stone-200 dark:!border-stone-700"
            />
          </div>
        </div>
      )}
      {(() => {
        if (!result) return null;
        const text = extractText(result);
        return text &&
          parsed.lines.length === 0 &&
          parsed.files.length === 0
          ? resultPre(text)
          : null;
      })()}
    </div>
  );
  return { pattern, searchPath, glob, children, canOpen };
}

// —— ls / glob（镜像 LsItem.tsx / GlobItem.tsx：条目列表） ——

function entryRows(entries: string[]): ReactNode {
  return entries.map((entry, i) => {
    const isDir = entry.endsWith("/") || entry.endsWith("\\");
    const name = isDir
      ? entry.slice(0, -1).split("/").filter(Boolean).pop() || entry.slice(0, -1)
      : entry.split("/").filter(Boolean).pop() || entry;
    return (
      <div
        key={i}
        className="flex items-center gap-2.5 px-4 py-2 text-sm font-mono border-b border-stone-100 dark:border-stone-800 last:border-b-0 hover:bg-stone-100 dark:hover:bg-stone-800 transition-colors"
      >
        <span
          className={
            isDir
              ? "truncate text-stone-700 dark:text-stone-200 font-medium"
              : "truncate text-stone-600 dark:text-stone-300"
          }
        >
          {name}
        </span>
      </div>
    );
  });
}

function buildLsDetail(
  args: Record<string, unknown>,
  result: ToolPart["result"],
  t: TranslateFn,
): { dirPath: string; displayLabel: string; children: ReactNode; canOpen: boolean } {
  const dirPath = (args.path as string) || "/";
  const displayLabel =
    dirPath === "/" ? "/" : dirPath.split("/").filter(Boolean).pop() || dirPath;
  const entries = extractPaths(result);
  const canOpen = entries.length > 0;
  const children = canOpen && (
    <div className="p-4 sm:p-5 space-y-3">
      <div className="group/args relative flex items-center gap-2 px-3 py-2 rounded-lg bg-stone-100 dark:bg-stone-800 text-sm text-stone-500 dark:text-stone-400 font-mono">
        <span className="truncate">{dirPath}</span>
        <span className="shrink-0 text-stone-400 dark:text-stone-500">
          {t("chat.message.toolItemCount", { count: entries.length })}
        </span>
        <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/args:opacity-100 transition-opacity">
          <CopyButton text={dirPath} size={12} />
        </div>
      </div>
      <div className="relative group rounded-lg border border-stone-200/60 dark:border-stone-700/50 bg-stone-50 dark:bg-stone-900 overflow-auto max-h-[60dvh]">
        <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
          <CopyButton
            text={entries.join("\n")}
            size={14}
            className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md !border !border-stone-200 dark:!border-stone-700"
          />
        </div>
        {entryRows(entries)}
      </div>
    </div>
  );
  return { dirPath, displayLabel, children, canOpen };
}

function buildGlobDetail(
  args: Record<string, unknown>,
  result: ToolPart["result"],
  t: TranslateFn,
): { pattern: string; searchPath: string; children: ReactNode; canOpen: boolean } {
  const pattern = (args.pattern as string) || "";
  const searchPath = (args.path as string) || "";
  const paths = extractPaths(result);
  const canOpen = !!pattern || paths.length > 0;
  const children = canOpen && (
    <div className="p-4 sm:p-5 space-y-3">
      <div className="group/args relative flex items-center gap-2 px-3 py-2 rounded-lg bg-stone-100 dark:bg-stone-800 text-sm text-stone-500 dark:text-stone-400 font-mono flex-wrap">
        <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
          {pattern}
        </span>
        {searchPath && (
          <span className="text-stone-400 dark:text-stone-500">
            {t("chat.message.toolInPath", { path: searchPath })}
          </span>
        )}
        <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/args:opacity-100 transition-opacity">
          <CopyButton text={pattern} size={12} />
        </div>
      </div>
      {paths.length > 0 && (
        <div className="relative group rounded-lg border border-stone-200/60 dark:border-stone-700/50 bg-stone-50 dark:bg-stone-900 overflow-auto max-h-[60dvh]">
          <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
            <CopyButton
              text={paths.join("\n")}
              size={14}
              className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md !border !border-stone-200 dark:!border-stone-700"
            />
          </div>
          {entryRows(paths)}
        </div>
      )}
    </div>
  );
  return { pattern, searchPath, children, canOpen };
}

// —— execute（镜像 ExecuteItem.tsx：$ 命令块 + 输出 + exit 条） ——

// execute 输出尾部标记解析（ExecuteItem 同源逻辑，纯数据无 JSX）：
// 剥掉 "[Command succeeded/failed …]" 与 "[Output was truncated …]" 尾行，
// 提取 exitCode / truncated / 纯输出。canOpen 门控与 payload 共用同一输出。
export function parseExecuteOutput(
  result: ToolPart["result"],
): { output: string; exitCode: number | null; truncated: boolean } {
  const raw = result ? extractText(result) : "";
  const lines = raw ? raw.split("\n") : [];
  let exitCode: number | null = null;
  let truncated = false;
  const outputLines = [...lines];
  for (let i = outputLines.length - 1; i >= 0; i -= 1) {
    const line = outputLines[i] ?? "";
    if (/^\[Command (succeeded|failed)/.test(line)) {
      outputLines.splice(i, 1);
      const codeMatch = line.match(/exit code (\d+)/);
      if (codeMatch) exitCode = parseInt(codeMatch[1] ?? "", 10);
    } else if (/^\[Output was truncated/.test(line)) {
      outputLines.splice(i, 1);
      truncated = true;
    } else if (line.startsWith("[")) {
      break;
    } else {
      break;
    }
  }
  return { output: outputLines.join("\n").trim(), exitCode, truncated };
}

function buildExecuteDetail(
  args: Record<string, unknown>,
  result: ToolPart["result"],
  success: boolean | undefined,
  t: TranslateFn,
): { command: string; children: ReactNode; canOpen: boolean } {
  const command = (args.command as string) || "";
  const timeout = args.timeout as number | undefined;
  const { output, exitCode, truncated } = parseExecuteOutput(result);
  const canOpen = !!command || !!output;
  const children = canOpen && (
    <div className="p-4 sm:p-5 space-y-3">
      <div className="group/args relative px-3 py-2.5 rounded-lg bg-stone-900 dark:bg-stone-950 text-sm font-mono flex items-center gap-2 flex-wrap">
        <span className="text-emerald-400 font-semibold">$</span>
        <span className="text-stone-200 break-all min-w-0">{command}</span>
        {timeout && (
          <span className="shrink-0 px-2 py-0.5 rounded-md bg-stone-700 text-stone-300 text-xs">
            {timeout}s
          </span>
        )}
        <div className="absolute top-1.5 right-1.5 opacity-0 group-hover/args:opacity-100 transition-opacity">
          <CopyButton
            text={command}
            size={12}
            className="!bg-white/10 hover:!bg-white/20 !text-stone-300 !border !border-stone-600"
          />
        </div>
      </div>
      {output && (
        <div className="relative group">
          <pre className="text-sm rounded-lg p-4 min-w-0 bg-stone-50 dark:bg-stone-900 border border-stone-200/60 dark:border-stone-700/50 text-stone-700 dark:text-stone-300 whitespace-pre-wrap break-words font-mono">
            {output}
          </pre>
          <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <CopyButton
              text={output}
              size={14}
              className="!bg-white/80 dark:!bg-stone-800/80 !rounded-md !border !border-stone-200 dark:!border-stone-700"
            />
          </div>
        </div>
      )}
      {result && (
        <div className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg text-stone-600 dark:text-stone-300 bg-stone-100 dark:bg-stone-800">
          <span>
            {exitCode !== null
              ? t("chat.message.toolExitCode", { code: exitCode })
              : success
                ? t("chat.message.toolSuccess")
                : t("chat.message.toolFailed")}
          </span>
          {truncated && <span className="shrink-0 text-amber-500">!</span>}
        </div>
      )}
    </div>
  );
  return { command, children, canOpen };
}

// —— 通用/MCP 工具（镜像 ToolCallItem.tsx panelContent：args JSON + result） ——

function displayNameOf(name: string): { isMcp: boolean; toolName: string; serverName: string | null; formatted: string } {
  const colonIdx = name.indexOf(":");
  const isMcp = colonIdx > 0;
  const serverName = isMcp ? name.substring(0, colonIdx) : null;
  const toolName = isMcp ? name.substring(colonIdx + 1) : name;
  const formatted = toolName
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
  return { isMcp, toolName, serverName, formatted };
}

export function parseDisplayArgs(
  args: Record<string, unknown>,
): Record<string, unknown> {
  if (args.partial !== undefined) {
    try {
      return JSON.parse(args.partial as string);
    } catch {
      return { partial: args.partial };
    }
  }
  return args;
}

function buildGenericDetail(
  name: string,
  args: Record<string, unknown>,
  result: ToolPart["result"],
  isPending: boolean | undefined,
  elapsedText: string | undefined,
  t: TranslateFn,
): { formatted: string; serverName: string | null; children: ReactNode; canOpen: boolean; isMcp: boolean } {
  const { isMcp, serverName, formatted } = displayNameOf(name);
  const displayArgs = parseDisplayArgs(args);
  const hasArgs = Object.keys(displayArgs).length > 0;
  const hasResult = result !== undefined;
  const canOpen = hasArgs || hasResult;
  const argsJson = JSON.stringify(displayArgs, null, 2);
  const children = canOpen && (
    <div className="space-y-3 max-h-full overflow-y-auto p-2 sm:p-4 [&_pre]:!text-sm">
      {hasArgs && (
        <CollapsibleSection
          title={t("chat.message.args")}
          action={<CopyButton text={argsJson} size={12} />}
        >
          <pre className="text-sm text-stone-600 dark:text-stone-300 overflow-x-auto overflow-y-auto min-w-0 font-mono">
            {argsJson}
          </pre>
        </CollapsibleSection>
      )}
      {hasResult && (
        <CollapsibleSection
          title={t("chat.message.result")}
          action={
            <CopyButton
              text={
                typeof result === "string"
                  ? result
                  : JSON.stringify(result, null, 2)
              }
              size={12}
            />
          }
        >
          <ToolResultContent result={result} hideCopyButton />
        </CollapsibleSection>
      )}
      {isPending && (
        <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
          <span>{t("chat.message.running")}</span>
          {elapsedText && <span className="tabular-nums">{elapsedText}</span>}
        </div>
      )}
    </div>
  );
  return { formatted, serverName, children, canOpen, isMcp };
}

// —— 主入口：按 part 类型拼面板 payload ——

export interface StepPanelInput {
  Icon16: ReactNode;
  t: TranslateFn;
}

function formatCommandPreview(command: string): string {
  return command.length > 80 ? `${command.slice(0, 77)}…` : command;
}

export type StepPanelPart =
  | ToolPart
  | ThinkingPart
  | SummaryPart
  | SandboxPart
  | SubagentPart;

export function buildStepPanelPayload(
  // isStreaming：thinking/summary 经 isMessageStreaming && isLast 门控后的状态；
  // 不传时回退 part.isStreaming（历史消息可能残留 true）。
  entry: { part: StepPanelPart; isStreaming?: boolean },
  { Icon16, t }: StepPanelInput,
): ProcessStepPanelPayload | null {
  const part = entry.part;
  if (part.type === "tool") {
    const args = part.args ?? {};
    const status = toolStatus(part);
    const footer = durationFooter(part.startedAt, part.completedAt);
    switch (part.name) {
      case "read_file": {
        const detail = buildReadDetail(args, part.result);
        return {
          title: `${t("chat.message.toolRead")} ${detail.fileName || detail.filePath}`,
          icon: Icon16,
          status,
          subtitle: detail.filePath,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
      case "write_file": {
        const detail = buildWriteDetail(args, part.result);
        return {
          title: `${t("chat.message.toolWrite")} ${detail.fileName || detail.filePath}`,
          icon: Icon16,
          status,
          subtitle: detail.filePath,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
      case "edit_file": {
        const detail = buildEditDetail(args, part.result, t);
        return {
          title: `${t("chat.message.toolEdit")} ${detail.fileName || detail.filePath}`,
          icon: Icon16,
          status,
          subtitle: detail.filePath,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
      case "grep": {
        const detail = buildGrepDetail(args, part.result, t);
        return {
          title: `${t("chat.message.toolSearch")} ${detail.pattern}`,
          icon: Icon16,
          status,
          subtitle: detail.searchPath || detail.glob || undefined,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
      case "ls": {
        const detail = buildLsDetail(args, part.result, t);
        return {
          title: `${t("chat.message.toolLs")} ${detail.displayLabel}`,
          icon: Icon16,
          status,
          subtitle: detail.dirPath,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
      case "glob": {
        const detail = buildGlobDetail(args, part.result, t);
        return {
          title: `${t("chat.message.toolGlob")} ${detail.pattern}`,
          icon: Icon16,
          status,
          subtitle: detail.searchPath || undefined,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
      case "execute": {
        const detail = buildExecuteDetail(args, part.result, part.success, t);
        return {
          title: t("chat.message.toolExecute"),
          icon: Icon16,
          status,
          subtitle: detail.command
            ? detail.command.length > 120
              ? `${detail.command.slice(0, 117)}…`
              : detail.command
            : undefined,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
      default: {
        const detail = buildGenericDetail(
          part.name,
          args,
          part.result,
          part.isPending,
          undefined,
          t,
        );
        return {
          title: detail.formatted,
          icon: Icon16,
          status,
          subtitle: detail.serverName || undefined,
          children: detail.children,
          footer,
          canOpen: detail.canOpen,
        };
      }
    }
  }
  if (part.type === "thinking") {
    const streaming = entry.isStreaming ?? !!part.isStreaming;
    const status: ProcessStepStatus = streaming ? "loading" : "success";
    const children = (
      <div className="p-3 sm:p-4 [&_.markdown-preview]:thinking-content">
        <MarkdownContent content={part.content} isStreaming={streaming} />
      </div>
    );
    return {
      title: t("chat.message.thought"),
      icon: Icon16,
      status,
      panelKey: stepPanelKey(part),
      children,
      canOpen: !!part.content,
    };
  }
  if (part.type === "summary") {
    const streaming = entry.isStreaming ?? !!part.isStreaming;
    const status: ProcessStepStatus = streaming ? "loading" : "success";
    const panelKey = stepPanelKey(part);
    const children = (
      <div className="p-3 sm:p-4">
        <MarkdownContent content={part.content} isStreaming={streaming} />
      </div>
    );
    return {
      title: t("chat.message.summary"),
      icon: Icon16,
      status,
      panelKey,
      children,
      canOpen: !!part.content,
    };
  }
  if (part.type === "sandbox") {
    const status: ProcessStepStatus =
      part.status === "starting"
        ? "loading"
        : part.status === "ready"
          ? "success"
          : part.status === "cancelled"
            ? "cancelled"
            : "error";
    const hasDetails =
      (part.status === "ready" && part.sandbox_id) ||
      (part.status === "error" && part.error) ||
      part.status === "cancelled";
    const children = (
      <div className="space-y-2 max-h-full overflow-y-auto p-2 sm:p-4">
        {part.status === "ready" && part.sandbox_id && (
          <div className="text-xs text-stone-600 dark:text-stone-300 font-mono">
            {t("chat.sandboxId", { id: part.sandbox_id })}
          </div>
        )}
        {part.status === "error" && part.error && (
          <div className="text-xs text-red-600 dark:text-red-400">{part.error}</div>
        )}
        {part.status === "cancelled" && (
          <div className="text-xs text-amber-600 dark:text-amber-400">
            {t("chat.cancelled")}
          </div>
        )}
      </div>
    );
    return {
      title: t("chat.sandbox.name"),
      icon: Icon16,
      status,
      subtitle: part.sandbox_id || undefined,
      children,
      canOpen: !!hasDetails,
    };
  }
  if (part.type === "subagent") {
    // 子代理卡片保持原样渲染（组内一行卡片），点击走 SubagentBlock 自带逻辑，
    // payload 仅供头图标/状态使用 —— 本函数返回 null 表示"不走简洁行"。
    return null;
  }
  return null;
}

// thinking/summary 面板 key，与 ThinkingBlock / SummaryItem 同口径。
export function stepPanelKey(part: StepPanelPart): string | undefined {
  if (part.type === "thinking") return part.thinking_id || undefined;
  if (part.type === "summary") {
    return `summary:${part.agent_id || "root"}:${part.depth || 0}:${part.summary_id || "default"}`;
  }
  return undefined;
}

// 已打开的 thinking/summary 面板随流式内容刷新（替代 ThinkingBlock/SummaryItem
// 的 useEffect 刷新，组内简洁行没有这两个组件）。无 key 时不刷新，避免改写其它面板。
export function refreshOpenStepPanel(
  part: ThinkingPart | SummaryPart,
  isStreaming: boolean,
  t: TranslateFn,
): boolean {
  const panelKey = stepPanelKey(part);
  if (!panelKey || !isPersistentToolPanelOpen(panelKey)) return false;
  const payload = buildStepPanelPayload({ part, isStreaming }, { Icon16: null, t });
  if (!payload) return false;
  updatePersistentToolPanel(
    (prev) => ({ ...prev, status: payload.status, children: payload.children }),
    panelKey,
  );
  return true;
}

export function formatStepCommandPreview(command: string): string {
  return formatCommandPreview(command);
}

// —— 轻量可点判定（ExecutionProcessGroup 行渲染用，纯数据无 JSX） ——
//
// 行渲染每 render 每行都需判定可点性；buildStepPanelPayload 会拼装全部
// children JSX（DeferredCodeMirrorViewer、ToolResultContent 等），逐行调用
// 造成双倍 JSX 构建。本函数只做与各 Item expandable/canExpand 门控同源的
// 布尔检查（content/result/entries 存在性），点击时才拼 payload。
// 规则与各 build*Detail 的 canOpen 口径逐一对齐（改任一处必须同步另一处）。
export function canOpenStep(part: StepPanelPart): boolean {
  if (part.type === "thinking" || part.type === "summary") {
    return !!part.content;
  }
  if (part.type === "sandbox") {
    return !!(
      (part.status === "ready" && part.sandbox_id) ||
      (part.status === "error" && part.error) ||
      part.status === "cancelled"
    );
  }
  if (part.type === "subagent") {
    // 子代理走 SubagentBlock 卡片原样渲染，不走简洁行可点判定。
    return false;
  }
  const args = part.args ?? {};
  switch (part.name) {
    case "read_file": {
      const raw = extractText(part.result);
      return !!raw || extractReadImageBlocks(part.result).length > 0;
    }
    case "write_file":
    case "edit_file":
      return (
        !!(args.content as string) ||
        !!(args.old_string as string) ||
        !!(args.new_string as string) ||
        !!part.result
      );
    case "grep":
      return !!(
        (args.pattern as string) ||
        extractText(part.result)
      );
    case "ls":
      return extractPaths(part.result).length > 0;
    case "glob":
      return !!(
        (args.pattern as string) || extractPaths(part.result).length > 0
      );
    case "execute": {
      const command = (args.command as string) || "";
      return !!command || !!parseExecuteOutput(part.result).output;
    }
    default: {
      const displayArgs = parseDisplayArgs(args);
      return Object.keys(displayArgs).length > 0 || part.result !== undefined;
    }
  }
}

export function subagentDisplayName(agentName: string): string {
  return formatSubagentName(agentName);
}
