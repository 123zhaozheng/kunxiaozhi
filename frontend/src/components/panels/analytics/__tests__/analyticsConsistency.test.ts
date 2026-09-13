/**
 * Analytics consistency contract (PRD R3.1): donut center numbers and KPI
 * card numbers come from the same field.
 *
 * Both `AnalyticsKpiRow` and `AnalyticsTopRow` must read their values from
 * the shared accessors in `analyticsKpi.ts`:
 *   - agent donut center   == sessions KPI card == kpiSessionsValue
 *   - persona donut center == sessions KPI card == kpiSessionsValue
 *   - model donut center   == total tokens card == kpiTotalTokensValue
 *
 * Runtime assertions pin the accessors to the summary fields, and source
 * assertions pin both components to the shared accessors (no component may
 * re-derive the centers from slice sums or other fields).
 *
 * Run: npx tsx --test src/components/panels/analytics/__tests__/analyticsConsistency.test.ts
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  kpiSessionsValue,
  kpiTotalTokensValue,
} from "../analyticsKpi";
import type { UsageSummaryResponse } from "../../../../types/analytics";

const here = dirname(fileURLToPath(import.meta.url));
const kpiRowSource = readFileSync(join(here, "../AnalyticsKpiRow.tsx"), "utf8");
const topRowSource = readFileSync(join(here, "../AnalyticsTopRow.tsx"), "utf8");
const primitivesSource = readFileSync(
  join(here, "../analyticsPrimitives.tsx"),
  "utf8",
);

const summary: UsageSummaryResponse = {
  active_users: 50,
  using_users: 8,
  new_sessions: 123,
  active_sessions: 91,
  user_messages: 456,
  total_tokens: 78900,
  previous: null,
};

test("donut center accessors are pinned to the KPI summary fields", () => {
  // Sessions KPI card and both session donut centers: active sessions.
  assert.equal(kpiSessionsValue(summary), summary.active_sessions);
  assert.equal(kpiSessionsValue(summary), 91);
  // Model-token donut center: total tokens.
  assert.equal(kpiTotalTokensValue(summary), summary.total_tokens);
  assert.equal(kpiTotalTokensValue(summary), 78900);
  // No summary → no numbers anywhere ("—").
  assert.equal(kpiSessionsValue(null), null);
  assert.equal(kpiTotalTokensValue(null), null);
});

test("KPI row renders the session/token cards from the shared accessors", () => {
  assert.match(kpiRowSource, /from "\.\/analyticsKpi"/);
  assert.match(kpiRowSource, /kpiSessionsValue\(summary\)/);
  assert.match(kpiRowSource, /kpiTotalTokensValue\(summary\)/);
});

test("session KPI uses active sessions as the main and trend value", () => {
  assert.match(kpiRowSource, /kpiSessionsValue\(summary\)/);
  assert.match(kpiRowSource, /point\.active_sessions/);
  assert.match(kpiRowSource, /analytics\.overview\.newSessions/);
});

test("first KPI card follows the PRD filter-dependent user metric", () => {
  // Unfiltered the headline is 活跃用户 (logins) with a using-user subline;
  // a persona/agent filter switches it to 使用用户. Hardcoding either side
  // was a real regression, so assert the conditional wiring itself.
  assert.match(kpiRowSource, /kpiUsersValue\(summary, isFiltered\)/);
  assert.match(kpiRowSource, /analytics\.overview\.usingUsers/);
  assert.match(kpiRowSource, /analytics\.overview\.activeUsers/);
  assert.match(kpiRowSource, /analytics\.overview\.usingHint/);
  // The `/users/active` series counts users with messages, so it may only
  // trend the headline when the headline is also the using-user metric.
  assert.match(kpiRowSource, /isFiltered \? activeTrend\.map/);
});

test("both session donut centers use the sessions KPI accessor", () => {
  assert.match(topRowSource, /from "\.\/analyticsKpi"/);
  // One shared value feeds both the agent and the persona donut holes.
  assert.match(topRowSource, /const sessionsCenter = kpiSessionsValue\(summary\)/);
  const centerUses = topRowSource.match(/centerValue=\{sessionsCenter\}/g) ?? [];
  assert.equal(centerUses.length, 2, "agent + persona donuts share the center");
});

test("model-token donut center uses the total-tokens KPI accessor", () => {
  assert.match(topRowSource, /const tokensCenter = kpiTotalTokensValue\(summary\)/);
  const centerUses = topRowSource.match(/centerValue=\{tokensCenter\}/g) ?? [];
  assert.equal(centerUses.length, 1);
});

test("donut centers are never recomputed from slice values", () => {
  // The centers must not be derived by summing the (Top5-truncated) slices.
  assert.doesNotMatch(topRowSource, /centerValue=\{[^}]*reduce/);
  assert.doesNotMatch(topRowSource, /centerValue=\{[^}]*sessionsByAgent/);
  assert.doesNotMatch(topRowSource, /centerValue=\{[^}]*sessionsByPersona/);
  assert.doesNotMatch(topRowSource, /centerValue=\{[^}]*tokensByModel/);
});

test("donuts render the Top5 slices", () => {
  assert.match(primitivesSource, /slice\(0, 5\)/);
  assert.match(topRowSource, /DonutBlock/);
  // Legacy full pies are gone from the top row.
  assert.doesNotMatch(topRowSource, /PieBlock/);
});
