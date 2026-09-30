import assert from "node:assert/strict";
import test from "node:test";
import { formatBytes } from "../mongoStorageFormat";

test("formats bytes with adaptive binary units", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1023), "1023 B");
  assert.equal(formatBytes(1024), "1 KB");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(1024 ** 2), "1 MB");
  assert.equal(formatBytes(1024 ** 3), "1 GB");
  assert.equal(formatBytes(1024 ** 4), "1 TB");
});

test("uses a placeholder for missing or invalid values", () => {
  assert.equal(formatBytes(null), "-");
  assert.equal(formatBytes(undefined), "-");
  assert.equal(formatBytes(Number.NaN), "-");
  assert.equal(formatBytes(Number.POSITIVE_INFINITY), "-");
  assert.equal(formatBytes(-1), "-");
});
