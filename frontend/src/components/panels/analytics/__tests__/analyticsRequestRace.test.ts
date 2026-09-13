/** Regression coverage for analytics request ordering and partial loading. */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const sources = [
  readFileSync(join(here, "../AnalyticsPanel.tsx"), "utf8"),
  readFileSync(join(here, "../../AnalyticsDrilldownList.tsx"), "utf8"),
  readFileSync(join(here, "../../PresetAnalyticsModal.tsx"), "utf8"),
];

test("a slower old request cannot overwrite the newer result", async () => {
  let latestRequest = 0;
  let value = "initial";

  const request = (nextValue: string, delay: number): Promise<void> => {
    const requestId = ++latestRequest;
    return new Promise((resolve) => {
      setTimeout(() => {
        if (requestId === latestRequest) value = nextValue;
        resolve();
      }, delay);
    });
  };

  const oldRequest = request("old", 30);
  const newRequest = request("new", 5);
  await Promise.all([oldRequest, newRequest]);
  assert.equal(value, "new");
});

test("every analytics request owner guards state writes by request sequence", () => {
  for (const source of sources) {
    assert.match(source, /requestIdRef/);
    assert.match(source, /requestId (?:===|!==) requestIdRef\.current/);
  }
});

test("the dashboard settles independent blocks and keeps KPI loading skeletal", () => {
  const panelSource = sources[0];
  const kpiSource = readFileSync(join(here, "../AnalyticsKpiRow.tsx"), "utf8");
  assert.match(panelSource, /Promise\.allSettled\(/);
  assert.match(panelSource, /setSectionErrors\(/);
  assert.match(kpiSource, /animate-pulse/);
});

/**
 * Run: npx tsx --test src/components/panels/analytics/__tests__/analyticsRequestRace.test.ts
 */
