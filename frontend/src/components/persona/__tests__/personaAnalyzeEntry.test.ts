import assert from "node:assert/strict";
import test from "node:test";
import {
  isGlobalAnalyticsPath,
  resolvePersonaAnalyzeSurface,
} from "../personaAnalyzeEntry";

test("plaza analyze surface is scoped modal (not global dashboard)", () => {
  assert.equal(resolvePersonaAnalyzeSurface(), "scoped_modal");
});

test("global /analytics path is operator dashboard only", () => {
  assert.equal(isGlobalAnalyticsPath("/analytics"), true);
  assert.equal(isGlobalAnalyticsPath("/analytics?x=1"), true);
  assert.equal(isGlobalAnalyticsPath("/personas"), false);
  assert.equal(isGlobalAnalyticsPath(null), false);
});
