import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const frontendSrc = resolve(currentDir, "../..");

const localeFiles = ["en", "zh", "ja", "ko", "ru"].map((locale) =>
  resolve(frontendSrc, "i18n", "locales", `${locale}.json`),
);

const retentionKeys = ["noticeLead", "noticeKept", "forkDisabled"] as const;

function readJson(path: string) {
  return JSON.parse(readFileSync(path, "utf8"));
}

test("checkpoint retention strings are available in every locale", () => {
  for (const localeFile of localeFiles) {
    const locale = readJson(localeFile);
    for (const key of retentionKeys) {
      assert.equal(
        typeof locale.chat.retention[key],
        "string",
        `${localeFile} missing chat.retention.${key}`,
      );
      assert.ok(
        locale.chat.retention[key].length > 0,
        `${localeFile} has empty chat.retention.${key}`,
      );
    }
  }
});

test("retention notice renders i18n keys instead of inline text", () => {
  const source = readFileSync(
    resolve(
      frontendSrc,
      "components",
      "layout",
      "AppContent",
      "ChatView.tsx",
    ),
    "utf8",
  );
  for (const key of ["noticeLead", "noticeKept"]) {
    assert.ok(
      source.includes(`chat.retention.${key}`),
      `ChatView should use chat.retention.${key}`,
    );
  }
  assert.ok(
    source.includes("retentionNotice"),
    "ChatView should pass the composed notice into ChatInput.retentionNotice",
  );
});
