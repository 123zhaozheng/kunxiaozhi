import assert from "node:assert/strict";
import test from "node:test";
import {
  buildStorageFilesUrl,
  getStorageErrorMessage,
  normalizeDeleteResponse,
  normalizeList,
  normalizeUsage,
} from "../storage.ts";
import { isStorageQuotaError } from "../../../types/storage.ts";

test("normalizes the backend warning-level usage contract", () => {
  const usage = normalizeUsage({
    used_bytes: 80,
    pending_bytes: 4,
    quota_bytes: 100,
    warning_level: "notice",
    state: "ready",
  });

  assert.equal(usage.status, "warning");
  assert.equal(usage.remaining_bytes, 20);
  assert.equal(usage.usage_percent, 80);
  assert.equal(usage.warning_percent, 80);
});

test("normalizes file-list _id aliases and backend files envelope", () => {
  const page = normalizeList({
    files: [
      {
        _id: "file-1",
        source: "chat",
        name: "notes.txt",
        mime_type: "text/plain",
        size: 12,
        status: "active",
        is_user_deletable: true,
        created_at: "2026-09-14T00:00:00Z",
      },
    ],
    next_cursor: "file-1",
    has_more: true,
  });

  assert.equal(page.items[0]?.file_id, "file-1");
  assert.equal(page.items[0]?.is_user_deletable, true);
  assert.equal(page.next_cursor, "file-1");
  assert.equal(page.has_more, true);
});

test("keeps logical and physical delete results distinct", () => {
  const result = normalizeDeleteResponse({
    file_id: "file-1",
    logical_status: "deleted",
    released_bytes: 12,
    physical_status: "shared",
    status: "deleted",
  });

  assert.equal(result.status, "deleted");
  assert.equal(result.logical_status, "deleted");
  assert.equal(result.physical_cleanup, "shared");
  assert.equal(result.released_bytes, 12);
});

test("builds bounded, owner-scoped list filters", () => {
  const url = buildStorageFilesUrl({
    cursor: "cursor-1",
    limit: 1000,
    source: "chat",
    status: "active",
    order: "asc",
  });
  assert.match(url, /limit=100/);
  assert.match(url, /source=chat/);
  assert.match(url, /status=active/);
  assert.match(url, /descending=false/);
});

test("accepts both historical string and typed storage error details", () => {
  assert.equal(
    getStorageErrorMessage({ detail: "old quota message" }, "fallback"),
    "old quota message",
  );
  assert.equal(
    getStorageErrorMessage(
      { detail: { code: "storage_quota_exceeded", message: "typed quota" } },
      "fallback",
    ),
    "typed quota",
  );
  assert.equal(
    isStorageQuotaError({
      status: 413,
      detail: { code: "storage_quota_exceeded" },
    }),
    true,
  );
});
