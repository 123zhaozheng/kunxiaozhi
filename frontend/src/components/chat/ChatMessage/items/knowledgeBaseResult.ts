// 知识库工具结果：工具名判定与各 entity_type 的容错解析（design PR3 §4/§6）。

export const KB_TOOL_NAMES = [
  "list_knowledge_bases",
  "search_knowledge_base",
  "open_kb_document",
  "find_in_kb_document",
  "search_kb_files",
  "save_to_knowledge_base",
] as const;

export type KnowledgeBaseToolName = (typeof KB_TOOL_NAMES)[number];

type ToolResult = string | Record<string, unknown> | undefined | null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 去掉 `server:tool` 前缀后返回裸工具名。 */
export function bareToolName(name: string | undefined | null): string {
  if (!name) return "";
  const idx = name.lastIndexOf(":");
  return idx >= 0 ? name.substring(idx + 1) : name;
}

export function isKnowledgeBaseToolName(name: string | undefined | null): boolean {
  return (KB_TOOL_NAMES as readonly string[]).includes(bareToolName(name));
}

export function isKbSaveDraftToolName(name: string | undefined | null): boolean {
  return bareToolName(name) === "save_to_knowledge_base";
}

export function isKbSearchToolName(name: string | undefined | null): boolean {
  return bareToolName(name) === "search_knowledge_base";
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function toRecord(result: ToolResult): Record<string, unknown> | null {
  if (typeof result === "string") {
    const text = result.trim();
    if (!text.startsWith("{")) return null;
    try {
      const parsed: unknown = JSON.parse(text);
      return isRecord(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }
  return isRecord(result) ? result : null;
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

// —— 结果类型 ——

export interface KbListItem {
  kb_id: string;
  name: string;
  description: string;
  role: string;
  doc_count: number | null;
  chat_enabled: boolean | null;
}
export interface KbListResult {
  entity_type: "kb_list";
  knowledge_bases: KbListItem[];
  mentioned: boolean;
}

export interface KbSearchHit {
  ref: number | null;
  kb_id: string;
  kb_name: string;
  doc_id: string;
  filename: string;
  start_line: number | null;
  end_line: number | null;
  chunk_count: number | null;
  score: number | null;
  content: string;
}
export interface KbSearchResult {
  entity_type: "kb_search";
  query: string;
  results: KbSearchHit[];
  warnings: string[];
}

export interface KbDocumentWindowResult {
  entity_type: "kb_document_window";
  kb_id: string;
  doc_id: string;
  filename: string;
  start_line: number | null;
  end_line: number | null;
  total_lines: number | null;
  has_more_before: boolean;
  has_more_after: boolean;
  next_offset: number | null;
  content: string;
}

export interface KbFindWindow {
  start_line: number | null;
  end_line: number | null;
  matched_lines: number[];
  content: string;
}
export interface KbDocumentFindResult {
  entity_type: "kb_document_find";
  kb_id: string;
  doc_id: string;
  filename: string;
  match_mode: string;
  total_matches: number | null;
  windows: KbFindWindow[];
}

export interface KbFileItem {
  kb_id: string;
  kb_name: string;
  doc_id: string;
  filename: string;
  status: string;
  chunk_count: number | null;
  size: number | null;
  created_at: string | null;
}
export interface KbFilesResult {
  entity_type: "kb_files";
  files: KbFileItem[];
  total: number | null;
  has_more: boolean;
}

export interface KbSaveDraftResult {
  entity_type: "kb_save_draft";
  draft_id: string;
  title: string;
  preview: string;
  char_count: number | null;
  suggested_kb_id: string | null;
  suggested_kb_name: string | null;
  expires_at: string | null;
  message: string | null;
}

export type KnowledgeBaseResult =
  | KbListResult
  | KbSearchResult
  | KbDocumentWindowResult
  | KbDocumentFindResult
  | KbFilesResult
  | KbSaveDraftResult;

// —— 解析器（字段缺失时给默认值，不抛出）——

export function parseKbListResult(result: ToolResult): KbListResult | null {
  const p = toRecord(result);
  if (!p || p.entity_type !== "kb_list") return null;
  return {
    entity_type: "kb_list",
    knowledge_bases: records(p.knowledge_bases).map((r) => ({
      kb_id: str(r.kb_id),
      name: str(r.name),
      description: str(r.description),
      role: str(r.role),
      doc_count: num(r.doc_count),
      chat_enabled: bool(r.chat_enabled),
    })),
    mentioned: p.mentioned === true,
  };
}

export function parseKbSearchResult(result: ToolResult): KbSearchResult | null {
  const p = toRecord(result);
  if (!p || p.entity_type !== "kb_search") return null;
  return {
    entity_type: "kb_search",
    query: str(p.query),
    results: records(p.results)
      .map((r) => ({
        ref: num(r.ref),
        kb_id: str(r.kb_id),
        kb_name: str(r.kb_name),
        doc_id: str(r.doc_id),
        filename: str(r.filename),
        start_line: num(r.start_line),
        end_line: num(r.end_line),
        chunk_count: num(r.chunk_count),
        score: num(r.score),
        content: str(r.content),
      }))
      .filter((r) => r.doc_id !== "" && r.kb_id !== ""),
    warnings: Array.isArray(p.warnings)
      ? p.warnings.filter((w): w is string => typeof w === "string")
      : [],
  };
}

export function parseKbDocumentWindowResult(
  result: ToolResult,
): KbDocumentWindowResult | null {
  const p = toRecord(result);
  if (!p || p.entity_type !== "kb_document_window") return null;
  return {
    entity_type: "kb_document_window",
    kb_id: str(p.kb_id),
    doc_id: str(p.doc_id),
    filename: str(p.filename),
    start_line: num(p.start_line),
    end_line: num(p.end_line),
    total_lines: num(p.total_lines),
    has_more_before: p.has_more_before === true,
    has_more_after: p.has_more_after === true,
    next_offset: num(p.next_offset),
    content: str(p.content),
  };
}

export function parseKbDocumentFindResult(
  result: ToolResult,
): KbDocumentFindResult | null {
  const p = toRecord(result);
  if (!p || p.entity_type !== "kb_document_find") return null;
  return {
    entity_type: "kb_document_find",
    kb_id: str(p.kb_id),
    doc_id: str(p.doc_id),
    filename: str(p.filename),
    match_mode: str(p.match_mode),
    total_matches: num(p.total_matches),
    windows: records(p.windows).map((w) => ({
      start_line: num(w.start_line),
      end_line: num(w.end_line),
      matched_lines: Array.isArray(w.matched_lines)
        ? w.matched_lines.map(num).filter((n): n is number => n !== null)
        : [],
      content: str(w.content),
    })),
  };
}

export function parseKbFilesResult(result: ToolResult): KbFilesResult | null {
  const p = toRecord(result);
  if (!p || p.entity_type !== "kb_files") return null;
  return {
    entity_type: "kb_files",
    files: records(p.files).map((r) => ({
      kb_id: str(r.kb_id),
      kb_name: str(r.kb_name),
      doc_id: str(r.doc_id),
      filename: str(r.filename),
      status: str(r.status),
      chunk_count: num(r.chunk_count),
      size: num(r.size),
      created_at: typeof r.created_at === "string" ? r.created_at : null,
    })),
    total: num(p.total),
    has_more: p.has_more === true,
  };
}

export function parseKbSaveDraftResult(
  result: ToolResult,
): KbSaveDraftResult | null {
  const p = toRecord(result);
  if (!p || p.entity_type !== "kb_save_draft") return null;
  if (typeof p.draft_id !== "string" || !p.draft_id) return null;
  return {
    entity_type: "kb_save_draft",
    draft_id: p.draft_id,
    title: str(p.title),
    preview: str(p.preview),
    char_count: num(p.char_count),
    suggested_kb_id:
      typeof p.suggested_kb_id === "string" && p.suggested_kb_id
        ? p.suggested_kb_id
        : null,
    suggested_kb_name:
      typeof p.suggested_kb_name === "string" ? p.suggested_kb_name : null,
    expires_at: typeof p.expires_at === "string" ? p.expires_at : null,
    message: typeof p.message === "string" ? p.message : null,
  };
}

export function parseKnowledgeBaseResult(
  result: ToolResult,
): KnowledgeBaseResult | null {
  const p = toRecord(result);
  if (!p) return null;
  switch (p.entity_type) {
    case "kb_list":
      return parseKbListResult(p);
    case "kb_search":
      return parseKbSearchResult(p);
    case "kb_document_window":
      return parseKbDocumentWindowResult(p);
    case "kb_document_find":
      return parseKbDocumentFindResult(p);
    case "kb_files":
      return parseKbFilesResult(p);
    case "kb_save_draft":
      return parseKbSaveDraftResult(p);
    default:
      return null;
  }
}

// —— 消息级「参考来源」——

export const MAX_CITATIONS = 8;

export interface KbCitation {
  kb_id: string;
  kb_name: string;
  doc_id: string;
  filename: string;
  start_line: number | null;
  end_line: number | null;
  ref: number | null;
}

/**
 * 汇总多个 kb_search 结果为引用列表：按 doc+起始行去重，最多 MAX_CITATIONS 条。
 * 正文含 `[n]` 且命中某些 ref 时只保留被引用的，否则保留全部。
 */
export function collectKbCitations(
  searches: readonly KbSearchResult[],
  bodyText = "",
): KbCitation[] {
  const cited = new Set<number>();
  for (const m of bodyText.matchAll(/\[(\d{1,2})\]/g)) cited.add(Number(m[1]));

  const build = (onlyCited: boolean): KbCitation[] => {
    const seen = new Set<string>();
    const out: KbCitation[] = [];
    for (const search of searches) {
      for (const hit of search.results) {
        if (onlyCited && (hit.ref === null || !cited.has(hit.ref))) continue;
        const key = `${hit.kb_id}:${hit.doc_id}:${hit.start_line ?? ""}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          kb_id: hit.kb_id,
          kb_name: hit.kb_name,
          doc_id: hit.doc_id,
          filename: hit.filename,
          start_line: hit.start_line,
          end_line: hit.end_line,
          ref: hit.ref,
        });
        if (out.length >= MAX_CITATIONS) return out;
      }
    }
    return out;
  };

  if (cited.size > 0) {
    const onlyCited = build(true);
    if (onlyCited.length > 0) return onlyCited;
  }
  return build(false);
}
