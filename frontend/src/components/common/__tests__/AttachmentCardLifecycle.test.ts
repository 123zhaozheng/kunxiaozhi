import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const cardSource = readFileSync(
  resolve(import.meta.dirname, "../AttachmentCard.tsx"),
  "utf8",
);
const converterSource = readFileSync(
  resolve(import.meta.dirname, "../../../hooks/useAgent/eventProcessor.ts"),
  "utf8",
);

test("deleted attachment cards are explicit and non-interactive", () => {
  assert.match(cardSource, /aria-disabled/);
  assert.match(cardSource, /line-through decoration-red-500/);
  assert.match(cardSource, /storage\.deleted/);
  assert.match(cardSource, /if \(!isUnavailable\) onClick/);
  assert.match(cardSource, /STORAGE_LIFECYCLE_EVENT/);
});

test("attachment conversion keeps legacy payloads while accepting lifecycle fields", () => {
  assert.match(converterSource, /file_id\?: string/);
  assert.match(converterSource, /lifecycle_status\?: string/);
  assert.match(converterSource, /lifecycleStatus: a\.lifecycle_status \|\| a\.lifecycleStatus \|\| a\.status/);
  assert.match(converterSource, /available: a\.available/);
});
