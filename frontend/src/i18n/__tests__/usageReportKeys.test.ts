import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const currentDir = dirname(fileURLToPath(import.meta.url));
const localesDir = resolve(currentDir, "../locales");
const locales = ["en", "zh", "ja", "ko", "ru"];

// Every analytics key referenced by the dashboard (S5 sync): each key must
// exist as a string in all five locales. Orphan keys of the removed legacy
// Users/Tokens sections (analytics.users.*, analytics.tokens.title/...) are
// asserted absent below.
const requiredKeys = [
  "analytics.title",
  "analytics.subtitle",
  "analytics.empty",
  // Time range presets (dynamic keys via `analytics.timeRange.${preset}`)
  "analytics.timeRange.label",
  "analytics.timeRange.start",
  "analytics.timeRange.end",
  "analytics.timeRange.apply",
  "analytics.timeRange.custom",
  "analytics.timeRange.1d",
  "analytics.timeRange.7d",
  "analytics.timeRange.30d",
  // Filters
  "analytics.filters.persona",
  "analytics.filters.agent",
  "analytics.filters.all",
  "analytics.filters.personaLocked",
  "analytics.filters.sort",
  "analytics.filters.sortFrequency",
  "analytics.filters.sortRecent",
  "analytics.filters.userRole",
  // KPI row
  "analytics.overview.activeUsers",
  "analytics.overview.usingUsers",
  "analytics.overview.usingHint",
  "analytics.overview.activeSessions",
  "analytics.overview.userMessages",
  "analytics.overview.totalTokens",
  "analytics.overview.messagesPerUser",
  "analytics.overview.messagesPerUserHint",
  "analytics.overview.tokensPerSession",
  "analytics.overview.vsPrev",
  "analytics.sessions.sessions",
  "analytics.sessions.userMessages",
  // Core trend chart
  "analytics.trend.title",
  "analytics.trend.hint",
  "analytics.trend.activeUsers",
  // Insight panel
  "analytics.insights.title",
  "analytics.insights.subtitle",
  "analytics.insights.empty",
  "analytics.insights.peak",
  "analytics.insights.peakValue",
  "analytics.insights.topTokenUsers",
  "analytics.insights.userDrillHint",
  "analytics.insights.fastestGrowingPersona",
  "analytics.insights.personaFilterHint",
  "analytics.insights.newUsers",
  "analytics.insights.newUsersDrillHint",
  // Dimension donuts
  "analytics.dimensions.agentTop5",
  "analytics.dimensions.personaTop5",
  "analytics.dimensions.modelTokenTop5",
  "analytics.dimensions.byAgentHint",
  "analytics.dimensions.byPersonaHint",
  // Token labels shared by trend chart / donuts
  "analytics.tokens.byModelHint",
  "analytics.tokens.total",
  "analytics.tokens.unit",
  // Feedback overview
  "analytics.feedback.title",
  "analytics.feedback.total",
  "analytics.feedback.upCount",
  "analytics.feedback.downCount",
  "analytics.feedback.upRate",
  "analytics.feedback.byPreset",
  "analytics.feedback.byPresetHint",
  "analytics.feedback.reasonDistribution",
  "analytics.feedback.reasonDistributionHint",
  "analytics.feedback.up",
  "analytics.feedback.down",
  "analytics.feedback.noReasons",
  // Usage detail table
  "analytics.usage.title",
  "analytics.usage.subtitle",
  "analytics.usage.exportCsv",
  "analytics.usage.exporting",
  "analytics.usage.exportFailed",
  "analytics.usage.exportHint",
  "analytics.usage.empty",
  "analytics.usage.usingCount",
  "analytics.usage.loggedInCount",
  "analytics.usage.searchPlaceholder",
  "analytics.usage.searchHint",
  "analytics.usage.searchEmpty",
  // Dynamic keys via `analytics.usage.columns.${key}`
  "analytics.usage.columns.userId",
  "analytics.usage.columns.name",
  "analytics.usage.columns.role",
  "analytics.usage.columns.persona",
  "analytics.usage.columns.newSessions",
  "analytics.usage.columns.activeSessions",
  "analytics.usage.columns.userMessages",
  "analytics.usage.columns.tokens",
  "analytics.usage.columns.lastActive",
  // Drilldown lists
  "analytics.drilldown.back",
  "analytics.drilldown.agent",
  "analytics.drilldown.persona",
  "analytics.drilldown.user",
  "analytics.drilldown.sessionName",
  "analytics.drilldown.sessionCount",
  "analytics.drilldown.startedAt",
  "analytics.drilldown.createdAt",
  "analytics.drilldown.lastActive",
  "analytics.drilldown.status",
  "analytics.drilldown.tokens",
  "analytics.drilldown.eventCount",
  "analytics.drilldown.runId",
  "analytics.drilldown.rating",
  "analytics.drilldown.reason",
  "analytics.drilldown.comment",
  "analytics.drilldown.empty",
  "analytics.drilldown.sessionsTitle",
  "analytics.drilldown.usersTitle",
  "analytics.drilldown.feedbackTitle",
  "analytics.drilldown.runsTitle",
  "analytics.drilldown.exportCsv",
  "analytics.drilldown.exporting",
  "analytics.drilldown.exportFailed",
  "analytics.drilldown.exportHint",
  // Persona preset modal
  "analytics.preset.title",
  "analytics.preset.titleNamed",
  "analytics.preset.subtitle",
  "analytics.preset.analyze",
  "analytics.preset.totalSessions",
  "analytics.preset.totalMessages",
  "analytics.preset.totalTokens",
  "analytics.preset.activeUsers",
  "analytics.preset.upVoteRate",
  "analytics.preset.downReasons",
  "analytics.preset.noDownReasons",
];

// Keys of the removed legacy sections must not linger in any locale.
const removedKeys = [
  "analytics.users.title",
  "analytics.users.active",
  "analytics.users.activeTrend",
  "analytics.users.activeTrendHint",
  "analytics.users.heatmap",
  "analytics.users.heatmapHint",
  "analytics.sessions.title",
  "analytics.sessions.messages",
  "analytics.sessions.trend",
  "analytics.sessions.trendHint",
  "analytics.overview.totalSessions",
  "analytics.overview.sessions",
  "analytics.tokens.title",
  "analytics.tokens.byModel",
  "analytics.tokens.byPreset",
  "analytics.tokens.byPresetHint",
  "analytics.tokens.trend",
  "analytics.tokens.trendHint",
  "analytics.dimensions.byAgent",
  "analytics.dimensions.byPersona",
];

// "vs previous period" must stay period-neutral (never "last week").
const vsPrevForbidden = [/last week/i, /上周/, /先週/, /지난주/, /прошл(ая|ой) недел/i];

function readLocale(locale: string) {
  return JSON.parse(readFileSync(resolve(localesDir, `${locale}.json`), "utf8"));
}

function getPath(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    if (!current || typeof current !== "object") return undefined;
    return (current as Record<string, unknown>)[key];
  }, value);
}

test("usage report keys exist in every supported locale", () => {
  for (const localeName of locales) {
    const locale = readLocale(localeName);
    for (const keyPath of requiredKeys) {
      assert.equal(typeof getPath(locale, keyPath), "string", `${localeName}.json:${keyPath}`);
    }
  }
});

test("weekday labels exist in every supported locale", () => {
  for (const localeName of locales) {
    const locale = readLocale(localeName);
    const weekdays = getPath(locale, "analytics.weekdays");
    assert.ok(Array.isArray(weekdays), `${localeName}.json:analytics.weekdays`);
    assert.equal(weekdays.length, 7, `${localeName}.json:analytics.weekdays length`);
    for (const label of weekdays) {
      assert.equal(typeof label, "string", `${localeName}.json:analytics.weekdays item`);
      assert.ok(label.length > 0, `${localeName}.json:analytics.weekdays non-empty`);
    }
  }
});

test("orphan keys of removed legacy sections are gone", () => {
  for (const localeName of locales) {
    const locale = readLocale(localeName);
    for (const keyPath of removedKeys) {
      assert.equal(getPath(locale, keyPath), undefined, `${localeName}.json:${keyPath}`);
    }
  }
});

test("vsPrev wording stays period-neutral across locales", () => {
  for (const localeName of locales) {
    const locale = readLocale(localeName);
    const vsPrev = getPath(locale, "analytics.overview.vsPrev");
    assert.equal(typeof vsPrev, "string", `${localeName}.json:analytics.overview.vsPrev`);
    for (const pattern of vsPrevForbidden) {
      assert.doesNotMatch(vsPrev as string, pattern, `${localeName}.json:analytics.overview.vsPrev`);
    }
  }
});
