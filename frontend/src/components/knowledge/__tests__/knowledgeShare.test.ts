import assert from "node:assert/strict";
import test from "node:test";
import {
  SHARE_MAX_USERNAMES,
  canImportToKnowledgeBase,
  getKbCapabilities,
  getPublicStatusMeta,
  isKbCountQuotaReached,
  isPublicAllowed,
  mergeKbLists,
  parseUsernameInput,
  quotaPercent,
  resolveKbListTab,
  resolveKbSharedFilter,
} from "../knowledgeUtils.ts";

test("parseUsernameInput splits on commas, spaces and newlines", () => {
  const { usernames, overflow } = parseUsernameInput("alice, bob\ncarol  dave，erin、frank;gina");
  assert.deepEqual(usernames, ["alice", "bob", "carol", "dave", "erin", "frank", "gina"]);
  assert.equal(overflow, 0);
});

test("parseUsernameInput dedupes case-insensitively, including existing entries", () => {
  const { usernames } = parseUsernameInput("Alice bob ALICE bob2", ["alice"]);
  assert.deepEqual(usernames, ["alice", "bob", "bob2"]);
});

test("parseUsernameInput caps at 50 and counts the overflow", () => {
  const raw = Array.from({ length: 60 }, (_, i) => `u${i}`).join(",");
  const { usernames, overflow } = parseUsernameInput(raw);
  assert.equal(usernames.length, SHARE_MAX_USERNAMES);
  assert.equal(overflow, 10);
  assert.deepEqual(parseUsernameInput("   ,, \n").usernames, []);
});

test("role capabilities hide write actions for read-only roles", () => {
  assert.deepEqual(getKbCapabilities("owner"), {
    canWriteDocs: true,
    canManageSettings: true,
    canShare: true,
    canSubscribe: false,
    canUnsubscribe: false,
    canLeave: false,
  });
  assert.equal(getKbCapabilities("editor").canWriteDocs, true);
  assert.equal(getKbCapabilities("editor").canManageSettings, false);
  assert.equal(getKbCapabilities("editor").canLeave, true);
  for (const role of ["viewer", "subscriber", "visitor"] as const) {
    const caps = getKbCapabilities(role);
    assert.equal(caps.canWriteDocs, false, role);
    assert.equal(caps.canManageSettings, false, role);
    assert.equal(caps.canShare, false, role);
  }
  assert.equal(getKbCapabilities("visitor").canSubscribe, true);
  assert.equal(getKbCapabilities("subscriber").canUnsubscribe, true);
  assert.equal(getKbCapabilities("viewer").canLeave, true);
});

test("import menu condition: non-project files with a supported extension", () => {
  const exts = [".pdf", ".md", ".TXT"];
  assert.equal(canImportToKnowledgeBase({ file_type: "document", file_name: "a.PDF" }, exts), true);
  assert.equal(canImportToKnowledgeBase({ file_type: "code", file_name: "notes.txt" }, exts), true);
  assert.equal(canImportToKnowledgeBase({ file_type: "project", file_name: "a.pdf" }, exts), false);
  assert.equal(canImportToKnowledgeBase({ file_type: "image", file_name: "a.png" }, exts), false);
  assert.equal(canImportToKnowledgeBase({ file_type: "document", file_name: "README" }, exts), false);
  assert.equal(canImportToKnowledgeBase({ file_type: "document", file_name: "a.pdf" }, []), false);
  assert.equal(canImportToKnowledgeBase({ file_type: "document", file_name: "a.pdf" }, null), false);
});

const quotaOf = (used: number, max: number) => ({
  limits: { kb_max_count: max, kb_max_docs_per_kb: 500, kb_storage_quota_mb: 2048, kb_max_file_size_mb: 100 },
  usage: { kb_count: used, doc_count: 0, max_docs_in_kb: 0, storage_bytes: 0 },
});

test("quota helpers", () => {
  const q = quotaOf(10, 10);
  assert.equal(isKbCountQuotaReached(q), true);
  assert.equal(isKbCountQuotaReached(quotaOf(9, 10)), false);
  assert.equal(isKbCountQuotaReached(quotaOf(5, 0)), false);
  assert.equal(isKbCountQuotaReached(null), false);
  assert.equal(quotaPercent(50, 200), 25);
  assert.equal(quotaPercent(500, 200), 100);
  assert.equal(quotaPercent(1, 0), 0);
});

test("public status, allow_public and list tab helpers", () => {
  assert.equal(getPublicStatusMeta("none").visible, false);
  assert.equal(getPublicStatusMeta(undefined).visible, false);
  assert.equal(getPublicStatusMeta("approved").tone, "success");
  assert.equal(getPublicStatusMeta("delisted").tone, "danger");
  assert.equal(isPublicAllowed(null), true);
  assert.equal(isPublicAllowed({}), true);
  assert.equal(isPublicAllowed({ allow_public: false }), false);
  assert.equal(resolveKbListTab("plaza"), "plaza");
  assert.equal(resolveKbListTab("bogus"), "mine");
  assert.equal(resolveKbSharedFilter("subscribed"), "subscribed");
  assert.equal(resolveKbSharedFilter(null), "all");
  assert.deepEqual(
    mergeKbLists([{ id: "a" }, { id: "b" }], [{ id: "b" }, { id: "c" }]).map((x) => x.id),
    ["a", "b", "c"],
  );
});

import { readFileSync } from "node:fs";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

test("admin route, tab, menu entry and error mappings are wired", () => {
  assert.match(read("../../../App.tsx"), /path="\/admin\/knowledge-bases"/);
  assert.match(read("../../../App.tsx"), /Permission\.KNOWLEDGE_BASE_ADMIN/);
  assert.match(read("../../layout/AppContent/TabContent.tsx"), /knowledgeAdmin: KnowledgeBaseAdminPanel/);
  assert.match(read("../../layout/UserMenu.tsx"), /\/admin\/knowledge-bases/);
  const errors = read("../../../utils/backendErrors.ts");
  for (const code of [
    "kb_quota_count",
    "kb_quota_docs",
    "kb_quota_storage",
    "kb_file_too_large",
    "kb_public_disabled",
  ]) {
    assert.match(errors, new RegExp(`${code}:`), code);
  }
});

test("detail page gates write actions by role and read-only documents tab hides them", () => {
  const detail = read("../KnowledgeBaseDetail.tsx");
  assert.match(detail, /readOnly=\{!caps\.canWriteDocs\}/);
  assert.match(detail, /caps\.canManageSettings/);
  assert.match(detail, /caps\.canShare/);
  const docs = read("../DocumentsTab.tsx");
  assert.match(docs, /readOnly = false/);
  assert.match(docs, /uploadOpen && !readOnly/);
});

test("list page supports ?tab= with mine/shared/plaza and disables create at quota", () => {
  const list = read("../KnowledgeBaseListPage.tsx");
  assert.match(list, /searchParams\.get\("tab"\)/);
  assert.match(list, /disabled=\{quotaReached\}/);
  assert.match(list, /role="tablist"/);
});
