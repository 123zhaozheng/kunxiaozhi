import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  THINKING_LEVELS,
  normalizeThinkingLevel,
  thinkingLevelFromIndex,
  thinkingLevelToIndex,
} from "../thinkingLevels";

const source = readFileSync(
  new URL("../ThinkingEffortSlider.tsx", import.meta.url),
  "utf8",
);

test("thinking slider uses the shared five-level contract", () => {
  assert.deepEqual(THINKING_LEVELS, ["off", "low", "medium", "high", "max"]);
  assert.equal(normalizeThinkingLevel(true), "medium");
  assert.equal(normalizeThinkingLevel("high"), "high");
  assert.equal(thinkingLevelToIndex("medium"), 2);
  assert.equal(thinkingLevelFromIndex(4), "max");
});

test("thinking slider is an accessible native range control", () => {
  assert.match(source, /type="range"/);
  assert.match(source, /aria-valuenow=\{index\}/);
  assert.match(source, /aria-valuetext=\{selectedLabel\}/);
  assert.match(source, /onChange=\{\(event\) =>/);
  assert.match(source, /thinkingLevelFromIndex/);
});
