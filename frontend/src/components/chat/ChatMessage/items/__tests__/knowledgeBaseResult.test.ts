import assert from "node:assert/strict";
import test from "node:test";
import {
  collectKbCitations,
  isKnowledgeBaseToolName,
  MAX_CITATIONS,
  parseKbDocumentFindResult,
  parseKbDocumentWindowResult,
  parseKbFilesResult,
  parseKbListResult,
  parseKbSaveDraftResult,
  parseKbSearchResult,
  parseKnowledgeBaseResult,
} from "../knowledgeBaseResult.ts";

test("isKnowledgeBaseToolName supports bare and server-prefixed names", () => {
  for (const n of [
    "list_knowledge_bases",
    "search_knowledge_base",
    "open_kb_document",
    "find_in_kb_document",
    "search_kb_files",
    "save_to_knowledge_base",
    "srv:search_knowledge_base",
  ]) {
    assert.equal(isKnowledgeBaseToolName(n), true, n);
  }
  assert.equal(isKnowledgeBaseToolName("grep"), false);
  assert.equal(isKnowledgeBaseToolName(undefined), false);
});

test("search parser is tolerant of strings, objects, missing and bad fields", () => {
  const payload = {
    entity_type: "kb_search",
    query: "q",
    results: [
      { ref: 1, kb_id: "k", kb_name: "KB", doc_id: "d", filename: "a.md", start_line: "3", end_line: 9, score: 0.5, content: "x" },
      { kb_id: "k" },
      "junk",
    ],
  };
  for (const input of [payload, JSON.stringify(payload)]) {
    const r = parseKbSearchResult(input);
    assert.ok(r);
    assert.equal(r.results.length, 1);
    assert.equal(r.results[0].start_line, 3);
    assert.equal(r.results[0].chunk_count, null);
    assert.deepEqual(r.warnings, []);
  }
  assert.equal(parseKbSearchResult("not json"), null);
  assert.equal(parseKbSearchResult({ entity_type: "kb_files" }), null);
  assert.equal(parseKbSearchResult(undefined), null);
});

test("other parsers fill defaults and reject wrong entity types", () => {
  assert.deepEqual(parseKbListResult({ entity_type: "kb_list" })?.knowledge_bases, []);
  const win = parseKbDocumentWindowResult({ entity_type: "kb_document_window", filename: "a", start_line: 1, has_more_after: true });
  assert.equal(win?.has_more_after, true);
  assert.equal(win?.has_more_before, false);
  assert.equal(win?.total_lines, null);
  const find = parseKbDocumentFindResult({ entity_type: "kb_document_find", windows: [{ start_line: 1, matched_lines: [1, "x", 2] }] });
  assert.deepEqual(find?.windows[0].matched_lines, [1, 2]);
  assert.equal(parseKbFilesResult({ entity_type: "kb_files", files: [{ doc_id: "d" }] })?.files.length, 1);
  assert.equal(parseKbListResult({ entity_type: "kb_search" }), null);
});

test("save draft parser requires draft_id and keeps suggested kb optional", () => {
  assert.equal(parseKbSaveDraftResult({ entity_type: "kb_save_draft" }), null);
  const d = parseKbSaveDraftResult({ entity_type: "kb_save_draft", draft_id: "x", title: "t", suggested_kb_id: "" });
  assert.equal(d?.draft_id, "x");
  assert.equal(d?.suggested_kb_id, null);
  assert.equal(parseKnowledgeBaseResult({ entity_type: "kb_save_draft", draft_id: "x" })?.entity_type, "kb_save_draft");
  assert.equal(parseKnowledgeBaseResult({ entity_type: "nope" }), null);
});

function search(hits: Array<[number, string, number]>) {
  return parseKbSearchResult({
    entity_type: "kb_search",
    query: "q",
    results: hits.map(([ref, doc, line]) => ({ ref, kb_id: "k", kb_name: "K", doc_id: doc, filename: `${doc}.md`, start_line: line, end_line: line + 1 })),
  })!;
}

test("citations dedupe by doc+line, cap at 8, and prefer cited refs", () => {
  const s = search([[1, "a", 1], [2, "a", 1], [3, "b", 5]]);
  assert.equal(collectKbCitations([s]).length, 2);
  assert.deepEqual(collectKbCitations([s], "see [3]").map((c) => c.doc_id), ["b"]);
  assert.equal(collectKbCitations([s], "see [9]").length, 2);
  const many = search(Array.from({ length: 12 }, (_, i) => [i + 1, `d${i}`, 1] as [number, string, number]));
  assert.equal(collectKbCitations([many]).length, MAX_CITATIONS);
});
