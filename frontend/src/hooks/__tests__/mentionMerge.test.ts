import assert from "node:assert/strict";
import test from "node:test";
import {
  filterKnowledgeBases,
  getMergedMentionCount,
  isKbMentionCacheFresh,
  resolveMergedMention,
} from "../mentionMerge.ts";
import type { KnowledgeBase } from "../../services/api/knowledgeBase.ts";

const kb = (id: string, name: string) => ({ id, name }) as KnowledgeBase;
const kbs = [kb("1", "财务制度"), kb("2", "Product Wiki")];

test("merged index puts knowledge bases first, then items", () => {
  assert.equal(getMergedMentionCount(2, 3), 5);
  assert.deepEqual(resolveMergedMention(kbs, ["p1", "p2"], 1), { kind: "kb", kb: kbs[1], index: 1 });
  assert.deepEqual(resolveMergedMention(kbs, ["p1", "p2"], 2), { kind: "item", item: "p1", index: 0 });
  assert.deepEqual(resolveMergedMention(kbs, ["p1", "p2"], 3), { kind: "item", item: "p2", index: 1 });
  assert.equal(resolveMergedMention(kbs, ["p1"], 9), null);
  assert.equal(resolveMergedMention([], ["p1"], 0)?.kind, "item");
});

test("filters knowledge bases by name case-insensitively", () => {
  assert.equal(filterKnowledgeBases(kbs, "").length, 2);
  assert.deepEqual(filterKnowledgeBases(kbs, "wiki").map((k) => k.id), ["2"]);
  assert.deepEqual(filterKnowledgeBases(kbs, "财务").map((k) => k.id), ["1"]);
});

test("kb mention cache honours the 10s TTL", () => {
  const cache = { at: 1000, items: [] };
  assert.equal(isKbMentionCacheFresh(cache, 10_999), true);
  assert.equal(isKbMentionCacheFresh(cache, 11_000), false);
  assert.equal(isKbMentionCacheFresh(null, 0), false);
});
