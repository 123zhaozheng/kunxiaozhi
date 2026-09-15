import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const baseCss = readFileSync(
  new URL("../../../../styles/base.css", import.meta.url),
  "utf8",
);
const sessionListSource = readFileSync(
  new URL("../SessionListContent.tsx", import.meta.url),
  "utf8",
);

test("sidebar icon labels share one nav text style", () => {
  const navButtonRule = baseCss.match(/\.sidebar-nav-btn\s*\{[\s\S]*?\}/)?.[0];

  assert.ok(navButtonRule, "sidebar-nav-btn rule should exist");
  assert.match(navButtonRule, /font-size:\s*0\.875rem;/);
  assert.match(navButtonRule, /line-height:\s*1\.25rem;/);
  assert.match(navButtonRule, /font-weight:\s*500;/);
});

test("shared sidebar buttons expose a theme-aware focus ring", () => {
  const focusRule = baseCss.match(
    /\.sidebar-nav-btn:focus-visible,[\s\S]*?\.sidebar-rail-btn:focus-visible\s*\{[\s\S]*?\}/,
  )?.[0];

  assert.ok(focusRule, "sidebar focus-visible rule should exist");
  assert.match(focusRule, /outline:\s*2px solid var\(--theme-ring\)/);
  assert.match(focusRule, /outline-offset:\s*2px/);
  assert.match(
    sessionListSource,
    /onClick=\{onOpenSearch\}[\s\S]*className="sidebar-nav-btn flex h-8 w-8/,
  );
});
