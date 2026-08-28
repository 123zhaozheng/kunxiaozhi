import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const panel = readFileSync(join(here, "../analytics/AnalyticsPanel.tsx"), "utf8");
const drilldown = readFileSync(join(here, "../AnalyticsDrilldownList.tsx"), "utf8");

test("analytics panel uses the usage report APIs and filters", () => {
  assert.match(panel, /getUsageSummary\s*\(/);
  assert.match(panel, /getUsageTrend\s*\(/);
  assert.match(panel, /listUsageByUser\s*\(/);
  assert.match(panel, /exportUsageCsv\s*\(/);
  assert.match(panel, /getUsageSummary\(start, end, usageFilters\)/);
  assert.match(panel, /getUsageTrend\(start, end, usageFilters\)/);
  assert.match(panel, /listUsageByUser\(start, end, usageFilters/);
  assert.match(panel, /exportUsageCsv[\s\S]*usageFilters/);
  assert.doesNotMatch(panel, /OverviewResponse/);
  assert.doesNotMatch(panel, /analytics\.overview\.upVoteRate/);
});

test("analytics drilldown uses shared persona and agent option props", () => {
  assert.doesNotMatch(drilldown, /personaPlaceholder/);
  assert.doesNotMatch(drilldown, /<input[^>]+type=["']text["']/);
  assert.doesNotMatch(drilldown, /\[\s*["']fast["']\s*,\s*["']search["']\s*,\s*["']team["']\s*\]/);
  assert.match(drilldown, /personaOptions/);
  assert.match(drilldown, /agentOptions/);
});
