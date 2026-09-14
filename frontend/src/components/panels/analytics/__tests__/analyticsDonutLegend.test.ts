/**
 * Donut legend label tests.
 *
 * Guards the regression where every donut legend row rendered "— (0)":
 * recharts calls `<Legend formatter>` with `(name, entry, index)` where `name`
 * is the `nameKey` **string**, so reading `.label` / `.value` off the first
 * argument always yielded `undefined`. The value must be resolved from the
 * chart's own data by label instead.
 *
 * Run: npx tsx --test src/components/panels/analytics/__tests__/analyticsDonutLegend.test.ts
 */

import assert from "node:assert/strict";
import test from "node:test";
import { formatDonutLegendLabel } from "../analyticsFormat";

const items = [
  { label: "core-agent", value: 128 },
  { label: "search-agent", value: 42 },
  { label: "unknown", value: 0 },
];

test("resolves the value from the chart data using the legend name string", () => {
  assert.equal(formatDonutLegendLabel("core-agent", items), "core-agent (128)");
  assert.equal(
    formatDonutLegendLabel("search-agent", items),
    "search-agent (42)",
  );
});

test("does not degrade to the placeholder when a real label is supplied", () => {
  const rendered = formatDonutLegendLabel("core-agent", items);
  assert.notEqual(rendered, "— (0)");
  assert.ok(!rendered.startsWith("—"));
});

test("keeps a zero-valued slice showing its own label", () => {
  assert.equal(formatDonutLegendLabel("unknown", items), "unknown (0)");
});

test("falls back to the placeholder only when the name is missing", () => {
  assert.equal(formatDonutLegendLabel(undefined, items), "— (0)");
  assert.equal(formatDonutLegendLabel("", items), "— (0)");
  assert.equal(formatDonutLegendLabel(null, items), "— (0)");
});

test("renders zero when the label is absent from the data", () => {
  assert.equal(formatDonutLegendLabel("ghost", items), "ghost (0)");
});

test("truncates labels longer than 12 characters", () => {
  const long = [{ label: "a-very-long-agent-identifier", value: 7 }];
  assert.equal(
    formatDonutLegendLabel("a-very-long-agent-identifier", long),
    "a-very-long-… (7)",
  );
});

test("applies a custom unit formatter to the resolved value", () => {
  assert.equal(
    formatDonutLegendLabel("core-agent", items, (value) => `${value} tok`),
    "core-agent (128 tok)",
  );
});

test("formats large values with the shared compact notation", () => {
  const big = [{ label: "gpt", value: 2_500_000 }];
  assert.equal(formatDonutLegendLabel("gpt", big), "gpt (2.5M)");
});
