/**
 * Analytics KPI derivation tests (PRD acceptance criteria).
 *
 * Guards:
 *   - 人均消息 denominator is `using_users` (users that sent a message),
 *     never `active_users`;
 *   - zero denominators render "—", never NaN / Infinity;
 *   - a missing `previous` period hides the ±x% badge instead of showing 0%.
 *
 * Run: npx tsx --test src/components/panels/analytics/__tests__/analyticsKpiDerivation.test.ts
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  deltaPct,
  formatDeltaPct,
  formatRatio,
  kpiDeltaPct,
  kpiSessionsValue,
  kpiTotalTokensValue,
  kpiUserMessagesValue,
  kpiUsersValue,
  messagesPerUser,
  previousMetricValue,
  tokensPerSession,
  type KpiMetric,
} from "../analyticsKpi";
import type { UsageSummaryResponse } from "../../../../types/analytics";

function makeSummary(
  overrides: Partial<UsageSummaryResponse> = {},
): UsageSummaryResponse {
  return {
    active_users: 50,
    using_users: 8,
    new_sessions: 120,
    active_sessions: 90,
    user_messages: 100,
    total_tokens: 4500,
    previous: null,
    ...overrides,
  };
}

const ALL_METRICS: KpiMetric[] = [
  "users",
  "sessions",
  "userMessages",
  "totalTokens",
  "messagesPerUser",
  "tokensPerSession",
];

test("messages-per-user denominator is using_users, not active_users", () => {
  const summary = makeSummary({
    active_users: 50,
    using_users: 8,
    user_messages: 100,
  });
  assert.equal(messagesPerUser(summary), 100 / 8);
  assert.notEqual(messagesPerUser(summary), 100 / 50);
  // Null summary never produces a number.
  assert.equal(messagesPerUser(null), null);
});

test("zero denominators display — instead of NaN or Infinity", () => {
  const noUsing = makeSummary({ using_users: 0, user_messages: 100 });
  assert.equal(messagesPerUser(noUsing), null);
  assert.equal(formatRatio(messagesPerUser(noUsing)), "—");

  const noSessions = makeSummary({ active_sessions: 0, total_tokens: 4500 });
  assert.equal(tokensPerSession(noSessions), null);
  assert.equal(formatRatio(tokensPerSession(noSessions)), "—");

  // Defensive: non-finite inputs also format to "—".
  assert.equal(formatRatio(Number.NaN), "—");
  assert.equal(formatRatio(Number.POSITIVE_INFINITY), "—");
  assert.equal(formatRatio(null), "—");

  for (const text of [
    formatRatio(messagesPerUser(noUsing)),
    formatRatio(tokensPerSession(noSessions)),
  ]) {
    assert.doesNotMatch(text, /NaN|Infinity/);
  }

  // Real ratios keep one decimal.
  assert.equal(formatRatio(12.5), "12.5");
  assert.equal(formatRatio(50), "50");
});

test("missing previous period hides the delta badge (never 0%)", () => {
  const summary = makeSummary({ previous: null });
  for (const metric of ALL_METRICS) {
    assert.equal(kpiDeltaPct(summary, metric, false), null, metric);
    assert.equal(previousMetricValue(summary, metric, false), null, metric);
  }
  assert.equal(deltaPct(100, null), null);
  assert.equal(deltaPct(100, undefined), null);
  // Zero previous base is undefined growth, not a 0% change.
  assert.equal(deltaPct(100, 0), null);
});

test("previous period present yields signed percentage deltas", () => {
  const summary = makeSummary({
    new_sessions: 120,
    user_messages: 80,
    previous: {
      active_users: 40,
      using_users: 10,
      new_sessions: 100,
      active_sessions: 100,
      user_messages: 100,
      total_tokens: 5000,
    },
  });
  assert.equal(kpiDeltaPct(summary, "sessions", false), 20);
  assert.equal(kpiDeltaPct(summary, "userMessages", false), -20);
  assert.equal(formatDeltaPct(20), "+20.0%");
  assert.equal(formatDeltaPct(-20), "-20.0%");

  // Derived metrics compare against the derived previous value.
  // messagesPerUser: 80/8 = 10 now vs 100/10 = 10 before → no change.
  assert.equal(kpiDeltaPct(summary, "messagesPerUser", false), 0);
  // tokensPerSession: 4500/90 = 50 now vs 5000/100 = 50 before → no change.
  assert.equal(kpiDeltaPct(summary, "tokensPerSession", false), 0);

  // Zero denominator in the previous period hides the derived delta.
  const zeroPrevDenominator = makeSummary({
    previous: {
      active_users: 40,
      using_users: 0,
      new_sessions: 100,
      active_sessions: 0,
      user_messages: 100,
      total_tokens: 5000,
    },
  });
  assert.equal(
    kpiDeltaPct(zeroPrevDenominator, "messagesPerUser", false),
    null,
  );
  assert.equal(
    kpiDeltaPct(zeroPrevDenominator, "tokensPerSession", false),
    null,
  );
});

test("first card switches between active_users and using_users", () => {
  const summary = makeSummary({ active_users: 50, using_users: 8 });
  assert.equal(kpiUsersValue(summary, false), 50);
  assert.equal(kpiUsersValue(summary, true), 8);
  // Filtered delta follows the switched field.
  const withPrevious = makeSummary({
    active_users: 50,
    using_users: 8,
    previous: {
      active_users: 25,
      using_users: 4,
      new_sessions: 0,
      active_sessions: 0,
      user_messages: 0,
      total_tokens: 0,
    },
  });
  assert.equal(kpiDeltaPct(withPrevious, "users", false), 100);
  assert.equal(kpiDeltaPct(withPrevious, "users", true), 100);
});

test("plain KPI fields stay pinned to their summary fields", () => {
  const summary = makeSummary();
  assert.equal(kpiSessionsValue(summary), summary.new_sessions);
  assert.equal(kpiUserMessagesValue(summary), summary.user_messages);
  assert.equal(kpiTotalTokensValue(summary), summary.total_tokens);
  assert.equal(kpiSessionsValue(null), null);
});
