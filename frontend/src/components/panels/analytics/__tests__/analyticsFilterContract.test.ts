/**
 * Analytics filter contract — every request sees the same filter batch.
 *
 * Guards PRD R2/R3: switching the time preset / Persona / agent must result
 * in all eight usage requests (summary, trend, insights, by-agent,
 * by-persona, by-model, by-user, export) receiving identical `start`/`end`
 * (pure YYYY-MM-DD, no `T`/`Z`) and filter params, serialized by one helper.
 *
 * Run: npx tsx --test src/components/panels/analytics/__tests__/analyticsFilterContract.test.ts
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { buildUsageQuery } from "../../../../services/api/analyticsQuery";
import {
  addDaysString,
  effectiveRangeFor,
  formatRangeLabel,
  isValidDateString,
  normalizeRangeInput,
  rangeForPreset,
  todayString,
} from "../analyticsDates";

const here = dirname(fileURLToPath(import.meta.url));
const apiSource = readFileSync(
  join(here, "../../../../services/api/analytics.ts"),
  "utf8",
);
const panelSource = readFileSync(join(here, "../AnalyticsPanel.tsx"), "utf8");
const filterBarSource = readFileSync(
  join(here, "../AnalyticsFilterBar.tsx"),
  "utf8",
);
const presetModalSource = readFileSync(
  join(here, "../../PresetAnalyticsModal.tsx"),
  "utf8",
);

/** Slice one method body out of the API client source. */
function methodSource(name: string): string {
  const marker = `async ${name}(`;
  const from = apiSource.indexOf(marker);
  assert.notEqual(from, -1, `analyticsApi.${name} must exist`);
  const next = apiSource.indexOf("\n  async ", from + marker.length);
  return apiSource.slice(from, next === -1 ? apiSource.length : next);
}

const USAGE_METHODS = [
  "getUsageSummary",
  "getUsageTrend",
  "getUsageInsights",
  "getSessionsByAgent",
  "getSessionsByPersona",
  "getTokensByModel",
  "listUsageByUser",
  "exportUsageCsv",
] as const;

const USAGE_PATHS = [
  "/usage/summary",
  "/usage/trend",
  "/usage/insights",
  "/sessions/by-agent",
  "/sessions/by-persona",
  "/tokens/by-model",
  "/usage/by-user",
  "/usage/export.csv",
] as const;

test("buildUsageQuery serializes pure dates without time components", () => {
  const query = buildUsageQuery("2026-08-22", "2026-08-28");
  assert.equal(query, "?start=2026-08-22&end=2026-08-28");
  const params = new URLSearchParams(query);
  assert.match(params.get("start") ?? "", /^\d{4}-\d{2}-\d{2}$/);
  assert.match(params.get("end") ?? "", /^\d{4}-\d{2}-\d{2}$/);
  assert.doesNotMatch(query, /T|Z/);
});

test("buildUsageQuery is deterministic for a given filter state", () => {
  const filters = { personaPresetId: "p1", agentId: "a1", roleId: "r1" };
  const first = buildUsageQuery("2026-08-22", "2026-08-28", filters);
  // Switching presets/persona/agent and back must re-serialize identically.
  for (let i = 0; i < 5; i += 1) {
    assert.equal(
      buildUsageQuery("2026-08-22", "2026-08-28", { ...filters }),
      first,
    );
  }
  assert.equal(
    first,
    "?start=2026-08-22&end=2026-08-28&persona_preset_id=p1&agent_id=a1&role_id=r1",
  );
  // Empty-string filter values are treated as "no filter" by the panel, but
  // the serializer itself must also stay stable when fields are absent.
  assert.equal(
    buildUsageQuery("2026-08-22", "2026-08-28", {}),
    "?start=2026-08-22&end=2026-08-28",
  );
});

test("all eight usage requests receive identical parameters", () => {
  const filters = { personaPresetId: "persona-1", agentId: "agent-x" };
  const query = buildUsageQuery("2026-08-01", "2026-08-28", filters);
  const expected = {
    start: "2026-08-01",
    end: "2026-08-28",
    persona_preset_id: "persona-1",
    agent_id: "agent-x",
  };
  for (const path of USAGE_PATHS) {
    const params = new URL(`http://localhost${path}${query}`).searchParams;
    assert.equal(params.get("start"), expected.start, path);
    assert.equal(params.get("end"), expected.end, path);
    assert.equal(
      params.get("persona_preset_id"),
      expected.persona_preset_id,
      path,
    );
    assert.equal(params.get("agent_id"), expected.agent_id, path);
  }
});

test("every usage endpoint serializes through the single helper", () => {
  for (const name of USAGE_METHODS) {
    const body = methodSource(name);
    assert.match(
      body,
      /buildUsageQuery\(/,
      `${name} must serialize via buildUsageQuery`,
    );
    assert.doesNotMatch(
      body,
      /rangeQuery\(|appendUsageFilters\(/,
      `${name} must not use a separate serializer`,
    );
  }
  assert.match(apiSource, /usage\/insights/);
  assert.doesNotMatch(apiSource, /appendUsageFilters/);
});

test("date presets produce pure YYYY-MM-DD ranges", () => {
  const now = new Date("2026-08-28T12:00:00Z");
  const oneDay = rangeForPreset("1d", now);
  assert.deepEqual(oneDay, { start: "2026-08-28", end: "2026-08-28" });

  const week = rangeForPreset("7d", now);
  assert.deepEqual(week, { start: "2026-08-22", end: "2026-08-28" });

  const month = rangeForPreset("30d", now);
  assert.deepEqual(month, { start: "2026-07-30", end: "2026-08-28" });

  for (const range of [oneDay, week, month]) {
    for (const value of [range.start, range.end]) {
      assert.ok(isValidDateString(value));
      assert.doesNotMatch(value, /T|Z/);
    }
  }
});

test("UTC+8 date stays stable across browser timezone representations", () => {
  const sameInstant = [
    ["UTC", "2026-08-28T16:30:00Z"],
    ["UTC-5", "2026-08-28T11:30:00-05:00"],
    ["UTC+8", "2026-08-29T00:30:00+08:00"],
    ["UTC+9", "2026-08-29T01:30:00+09:00"],
  ] as const;

  for (const [browserTimezone, instant] of sameInstant) {
    const now = new Date(instant);
    assert.equal(todayString(now), "2026-08-29", browserTimezone);
    assert.deepEqual(rangeForPreset("7d", now), {
      start: "2026-08-23",
      end: "2026-08-29",
    });
  }
});

test("date arithmetic crosses month, year and leap boundaries", () => {
  assert.equal(addDaysString("2026-01-05", -7), "2025-12-29");
  assert.equal(addDaysString("2026-03-01", -1), "2026-02-28");
  assert.equal(addDaysString("2024-02-28", 1), "2024-02-29");
  assert.equal(addDaysString("2026-12-31", 1), "2027-01-01");

  const yearEdge = rangeForPreset("7d", new Date("2026-01-03T12:00:00Z"));
  assert.deepEqual(yearEdge, { start: "2025-12-28", end: "2026-01-03" });
});

test("effectiveRangeFor honors custom ranges and falls back safely", () => {
  const now = new Date(2026, 7, 28);
  const custom = { start: "2026-07-01", end: "2026-07-15" };
  assert.deepEqual(effectiveRangeFor("custom", custom, now), custom);
  // Custom preset without an applied range falls back to the 7-day window.
  assert.deepEqual(
    effectiveRangeFor("custom", null, now),
    rangeForPreset("7d", now),
  );
  assert.deepEqual(
    effectiveRangeFor("30d", custom, now),
    rangeForPreset("30d", now),
  );
  assert.equal(
    formatRangeLabel(custom),
    "2026-07-01 → 2026-07-15",
  );
});

test("normalizeRangeInput validates and orders custom picks", () => {
  assert.deepEqual(
    normalizeRangeInput("2026-08-10", "2026-08-01"),
    { start: "2026-08-01", end: "2026-08-10" },
  );
  assert.equal(normalizeRangeInput("", "2026-08-01"), null);
  assert.equal(normalizeRangeInput("2026-13-01", "2026-08-01"), null);
  assert.equal(normalizeRangeInput("2026-02-30", "2026-08-01"), null);
  assert.equal(normalizeRangeInput("2026-08-01T00:00:00Z", "2026-08-02"), null);
});

test("legacy local-day-boundary arithmetic is gone", () => {
  for (const source of [panelSource, filterBarSource, presetModalSource]) {
    assert.doesNotMatch(source, /setHours\(/);
    assert.doesNotMatch(source, /toISOString\(/);
    assert.doesNotMatch(source, /endOfDayCST|startOfDayCST/);
  }
});

test("panel wires one filter batch into every usage request", () => {
  assert.match(panelSource, /getUsageSummary\(start, end, usageFilters\)/);
  assert.match(panelSource, /getUsageTrend\(start, end, usageFilters\)/);
  assert.match(panelSource, /getUsageInsights\(start, end, usageFilters\)/);
  assert.match(panelSource, /getSessionsByAgent\(start, end, usageFilters/);
  assert.match(panelSource, /getSessionsByPersona\(start, end, usageFilters/);
  assert.match(panelSource, /getTokensByModel\(start, end, usageFilters\)/);
  assert.match(panelSource, /listUsageByUser\(start, end, usageFilters/);
  assert.match(panelSource, /exportUsageCsv\(start, end, usageFilters\)/);
  // Both dropdowns feed the same usageFilters object.
  assert.match(panelSource, /personaPresetId: personaPresetId \|\| undefined/);
  assert.match(panelSource, /agentId: agentId \|\| undefined/);
});

test("filter bar exposes persona and agent controls to the parent", () => {
  assert.match(filterBarSource, /onPersonaChange\(event\.target\.value\)/);
  assert.match(filterBarSource, /onAgentChange\(event\.target\.value\)/);
  assert.match(filterBarSource, /value=\{personaPresetId\}/);
  assert.match(filterBarSource, /value=\{agentId\}/);
  // Preset group is shared with PresetAnalyticsModal (single implementation).
  assert.match(presetModalSource, /AnalyticsRangePresetPicker/);
  assert.match(filterBarSource, /export function AnalyticsRangePresetPicker/);
});
