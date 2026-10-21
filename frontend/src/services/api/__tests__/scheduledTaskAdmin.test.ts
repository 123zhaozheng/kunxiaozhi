import test from "node:test";
import assert from "node:assert/strict";

import { buildAdminListQuery } from "../scheduledTaskAdmin.ts";

test("buildAdminListQuery returns an empty string for empty params", () => {
  assert.equal(buildAdminListQuery({}), "");
  assert.equal(buildAdminListQuery(), "");
});

test("buildAdminListQuery includes every provided param", () => {
  const query = buildAdminListQuery({
    skip: 20,
    limit: 20,
    q: "日报",
    owner: "zhangsan",
    status: "paused",
    last_run_status: "failed",
  });
  const parsed = new URLSearchParams(query);
  assert.equal(parsed.get("skip"), "20");
  assert.equal(parsed.get("limit"), "20");
  assert.equal(parsed.get("q"), "日报");
  assert.equal(parsed.get("owner"), "zhangsan");
  assert.equal(parsed.get("status"), "paused");
  assert.equal(parsed.get("last_run_status"), "failed");
  assert.equal([...parsed.keys()].length, 6);
});

test("buildAdminListQuery ignores empty and whitespace-only strings", () => {
  const query = buildAdminListQuery({
    skip: 0,
    limit: 20,
    q: "",
    owner: "   ",
    status: "",
    last_run_status: "",
  });
  const parsed = new URLSearchParams(query);
  assert.equal(parsed.has("q"), false);
  assert.equal(parsed.has("owner"), false);
  assert.equal(parsed.has("status"), false);
  assert.equal(parsed.has("last_run_status"), false);
  assert.equal(parsed.get("limit"), "20");
});

test("buildAdminListQuery keeps skip=0 and drops undefined numbers", () => {
  assert.equal(buildAdminListQuery({ skip: 0 }), "skip=0");
  assert.equal(buildAdminListQuery({ skip: 0, limit: 0 }), "skip=0&limit=0");
  assert.equal(buildAdminListQuery({ skip: undefined, limit: undefined }), "");
});

test("buildAdminListQuery trims and url-encodes text values", () => {
  const query = buildAdminListQuery({ q: "  a&b c  " });
  assert.equal(query, "q=a%26b+c");
});
