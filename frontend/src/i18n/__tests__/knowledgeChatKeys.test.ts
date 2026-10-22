import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dir = dirname(fileURLToPath(import.meta.url));
type Json = { [key: string]: Json | string };
const load = (l: string): Json =>
  JSON.parse(readFileSync(resolve(dir, `../locales/${l}.json`), "utf8"));
const flatten = (n: Json | string, p = ""): Record<string, string> =>
  typeof n === "string"
    ? { [p]: n }
    : Object.entries(n).reduce<Record<string, string>>(
        (a, [k, v]) => Object.assign(a, flatten(v, p ? `${p}.${k}` : k)),
        {},
      );

const groups = ["chat", "tools", "citations", "draft"] as const;

test("knowledge chat/tools/citations/draft keys match between zh and en", () => {
  const zh = load("zh").knowledge as Json;
  const en = load("en").knowledge as Json;
  for (const g of groups) {
    const z = flatten(zh[g], g);
    const e = flatten(en[g], g);
    assert.ok(Object.keys(z).length > 0, g);
    assert.deepEqual(Object.keys(z).sort(), Object.keys(e).sort(), g);
    for (const k of Object.keys(z)) {
      const ph = (s: string) => [...s.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
      assert.deepEqual(ph(z[k]), ph(e[k]), k);
    }
  }
});
