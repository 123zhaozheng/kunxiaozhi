import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

const KB_LIMIT_KEYS = [
  "kb_max_count",
  "kb_max_docs_per_kb",
  "kb_storage_quota_mb",
  "kb_max_file_size_mb",
];

test("RolesPanel keeps KB limits in form state and rebuilds them on submit", () => {
  const source = read("../RolesPanel.tsx");
  for (const key of KB_LIMIT_KEYS) {
    assert.match(source, new RegExp(`role\\?\\.limits\\?\\.${key}`), `${key} initial state`);
    assert.match(source, new RegExp(`\\["${key}", \\w+, [01]\\]`), `${key} in submit construction`);
  }
  assert.match(source, /limits: Object\.keys\(limits\)\.length > 0 \? limits : undefined/);
  assert.match(source, /showKbLimits/);
  // Count limits allow 0 ("none allowed"); size limits must stay positive.
  assert.match(source, /\["kb_max_count", \w+, 0\]/);
  assert.match(source, /\["kb_storage_quota_mb", \w+, 1\]/);
});

test("RoleLimits type and permission enum carry the new fields", () => {
  const types = read("../../../types/auth.ts");
  for (const key of KB_LIMIT_KEYS) assert.match(types, new RegExp(`${key}\\?: number \\| null`));
  assert.match(types, /KNOWLEDGE_BASE_ADMIN = "knowledge_base:admin"/);
});
