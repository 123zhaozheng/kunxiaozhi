/**
 * Ranked bar rows: ordering, folding past the limit, KPI-denominated shares.
 *
 * Run: npx tsx --test src/components/panels/analytics/__tests__/analyticsRankedBars.test.ts
 */

import assert from "node:assert/strict";
import test from "node:test";
import { formatPercent, rankRows } from "../analyticsFormat";

const items = [
  { label: "search", value: 20 },
  { label: "fast", value: 60 },
  { label: "team", value: 20 },
  { label: "empty", value: 0 },
];

test("rows are ranked largest first with a stable label tiebreak", () => {
  const { rows } = rankRows(items, 100, 8);
  assert.deepEqual(
    rows.map((row) => row.item.label),
    ["fast", "search", "team"],
  );
});

test("zero-valued rows are dropped instead of drawing empty bars", () => {
  const { rows, rest } = rankRows(items, 100, 8);
  assert.ok(rows.every((row) => row.item.value > 0));
  assert.equal(rest, null);
});

test("shares use the KPI total, not the sum of visible rows", () => {
  const { rows } = rankRows(items, 200, 2);
  assert.equal(rows[0].share, 0.3);
  assert.equal(rows[1].share, 0.1);
});

test("unknown totals hide the share column", () => {
  const { rows } = rankRows(items, null, 8);
  assert.ok(rows.every((row) => row.share === null));
});

test("bar widths are relative to the largest row", () => {
  const { rows } = rankRows(items, 100, 8);
  assert.equal(rows[0].widthPct, 100);
  assert.ok(Math.abs(rows[1].widthPct - 100 / 3) < 1e-9);
});

test("rows past the limit fold into a rest summary", () => {
  const { rows, rest } = rankRows(items, 100, 1);
  assert.equal(rows.length, 1);
  assert.deepEqual(rest, { count: 2, value: 40 });
});

test("percent formatting never shows 0% for a tiny but present share", () => {
  assert.equal(formatPercent(0.123), "12.3%");
  assert.equal(formatPercent(0.0004), "<0.1%");
  assert.equal(formatPercent(0), "0%");
});
