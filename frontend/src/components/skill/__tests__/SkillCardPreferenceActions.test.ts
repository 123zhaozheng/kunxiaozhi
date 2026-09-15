import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const componentSource = readFileSync(
  join(import.meta.dirname, "../SkillCard.tsx"),
  "utf8",
);

test("skill cards expose pin and favorite actions", () => {
  assert.match(componentSource, /Pin,/);
  assert.match(componentSource, /Star,/);
  assert.match(componentSource, /onTogglePreference\?:/);
  assert.match(componentSource, /pps-card__icon-action--active-pin/);
  assert.match(componentSource, /pps-card__icon-action--active-fav/);
  assert.match(componentSource, /t\("personaPresets\.pin", "置顶"\)/);
  assert.match(componentSource, /t\("personaPresets\.favorite", "收藏"\)/);
});

test("copied builtin skill cards expose the same write actions as personal skills", () => {
  assert.match(componentSource, /builtin: <ShieldCheck/);
  assert.match(componentSource, /onTogglePreference \? \(/);
  assert.match(componentSource, /selectionMode=\{selectionMode\}/);
  assert.match(componentSource, /onSelect=\{onSelect \? \(\) => onSelect\(skill\.name\) : undefined\}/);
  assert.doesNotMatch(componentSource, /skill\.is_builtin \? \(/);
  assert.doesNotMatch(componentSource, /!skill\.is_builtin &&/);
});

test("skill cards keep an edit entry point for all sources", () => {
  assert.match(componentSource, /onEdit\(skill\)/);
  assert.match(componentSource, /t\("skills\.card\.edit"\)/);
});
