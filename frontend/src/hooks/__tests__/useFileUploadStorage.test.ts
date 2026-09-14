import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const source = readFileSync(
  resolve(import.meta.dirname, "../useFileUpload.ts"),
  "utf8",
);
const appContentSource = readFileSync(
  resolve(import.meta.dirname, "../../components/layout/AppContent/index.tsx"),
  "utf8",
);

test("upload performs a best-effort quota preflight and opens management when full", () => {
  assert.match(source, /storageApi\.getUsage\(\)/);
  assert.match(source, /requestStorageManagement\(\)/);
  assert.match(source, /storage_quota_exceeded/);
  assert.match(appContentSource, /STORAGE_OPEN_MANAGEMENT_EVENT/);
  assert.match(appContentSource, /setShowProfileModal\(true\)/);
});

test("authoritative quota rejection preserves the draft for retry", () => {
  assert.match(source, /isStorageQuotaError\(error\)/);
  assert.match(source, /uploadError: message/);
  assert.match(source, /pendingFilesRef/);
  assert.match(source, /const retryUpload/);
});
