import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const source = readFileSync(
  resolve(import.meta.dirname, "../ProfileStorageTab.tsx"),
  "utf8",
);

test("space management exposes summary, filtering, pagination, and conversation files", () => {
  assert.match(source, /storageApi\.getUsage\(\)/);
  assert.match(source, /storageApi\.listFiles\(/);
  assert.match(source, /sourceFilter/);
  assert.match(source, /statusFilter/);
  assert.match(source, /nextCursor/);
  assert.match(source, /isProtectedStorageFile/);
  assert.match(source, /confirmBatchTitle/);
  assert.match(source, /partialDetails/);
  assert.match(source, /<option value="chat">/);
  assert.match(source, /<option value="wecom">/);
  assert.doesNotMatch(source, /<option value="(?:profile_avatar|skill)">/);
  assert.doesNotMatch(source, /Image|Wrench/);
});

test("storage rows keep server-marked undeletable files out of delete controls", () => {
  assert.match(source, /disabled=\{protectedFile \|\| file\.status !== "active"\}/);
  assert.match(source, /storage\.protectedHint/);
  assert.match(source, /storage\.deleteFile/);
});
