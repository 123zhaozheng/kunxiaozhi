import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const currentDir = dirname(fileURLToPath(import.meta.url));
const localesDir = resolve(currentDir, "../locales");
const locales = ["en", "zh", "ja", "ko", "ru"];
const requiredKeys = [
  "analytics.filters.persona",
  "analytics.filters.agent",
  "analytics.filters.all",
  "analytics.overview.sessions",
  "analytics.overview.activeSessions",
  "analytics.overview.userMessages",
  "analytics.sessions.userMessages",
  "analytics.usage.title",
  "analytics.usage.subtitle",
  "analytics.usage.exportCsv",
  "analytics.usage.exporting",
  "analytics.usage.exportFailed",
  "analytics.usage.empty",
  "analytics.usage.columns.userId",
  "analytics.usage.columns.name",
  "analytics.usage.columns.role",
  "analytics.usage.columns.persona",
  "analytics.usage.columns.newSessions",
  "analytics.usage.columns.activeSessions",
  "analytics.usage.columns.userMessages",
  "analytics.usage.columns.tokens",
  "analytics.usage.columns.lastActive",
];

function readLocale(locale: string) {
  return JSON.parse(readFileSync(resolve(localesDir, `${locale}.json`), "utf8"));
}

function getPath(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (!current || typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, value);
}

test("usage report keys exist in every supported locale", () => {
  for (const localeName of locales) {
    const locale = readLocale(localeName);
    for (const keyPath of requiredKeys) {
      assert.equal(typeof getPath(locale, keyPath), "string", `${localeName}.json:${keyPath}`);
    }
  }
});
