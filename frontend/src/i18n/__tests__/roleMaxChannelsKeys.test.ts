import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const localesDir = resolve(currentDir, "../locales");
const maintainedLocales = ["en", "zh"];
const roleLimitKeys = [
  "maxChannels",
  "maxChannelsHint",
  "maxChannelsPlaceholder",
];
const generatedPlaceholder = /^(?:\[TODO\]|【待翻译】|roles\.)/;

function readLocale(locale: string) {
  return JSON.parse(
    readFileSync(resolve(localesDir, `${locale}.json`), "utf8"),
  );
}

test("role max-channel strings are translated in maintained locales", () => {
  for (const localeName of maintainedLocales) {
    const locale = readLocale(localeName);

    for (const key of roleLimitKeys) {
      const keyPath = `roles.${key}`;
      const value = locale.roles?.[key];
      assert.equal(
        typeof value,
        "string",
        `${localeName}.json:${keyPath} must be a string`,
      );
      assert.doesNotMatch(
        value,
        generatedPlaceholder,
        `${localeName}.json:${keyPath} must not contain a generated placeholder`,
      );
    }
  }
});
