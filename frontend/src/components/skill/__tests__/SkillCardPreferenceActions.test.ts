import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const componentSource = readFileSync(
  join(import.meta.dirname, "../SkillCard.tsx"),
  "utf8",
);

test("skill cards expose pin and favorite banner actions", () => {
  assert.match(componentSource, /Pin,/);
  assert.match(componentSource, /Star,/);
  assert.match(componentSource, /onTogglePreference\?:/);
  assert.match(componentSource, /pps-card__icon-action--active-pin/);
  assert.match(componentSource, /pps-card__icon-action--active-fav/);
  assert.match(componentSource, /t\("personaPresets\.pin", "置顶"\)/);
  assert.match(componentSource, /t\("personaPresets\.favorite", "收藏"\)/);
});

test("builtin skill cards are marked read-only and excluded from write actions", () => {
  assert.match(componentSource, /builtin: <ShieldCheck/);
  assert.match(componentSource, /onTogglePreference && !skill\.is_builtin/);
  assert.match(componentSource, /!skill\.is_builtin && \(/);
  assert.match(componentSource, /onSelect && !skill\.is_builtin/);
});

test("builtin skill cards keep a read-only file viewer entry point", () => {
  assert.match(componentSource, /skill\.is_builtin \? \(/);
  assert.match(componentSource, /onEdit\(skill\)/);
  assert.match(componentSource, /aria-label=.*View files/);
});
