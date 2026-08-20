import assert from "node:assert/strict";
import test from "node:test";

import {
  getIdsToSnooze,
  getPopupEligibleItems,
  shouldAutoOpenNotifications,
  localEndOfDayUtcIso,
} from "../loginPopup.ts";

test("should auto-open only when at least one item is popup-eligible", () => {
  assert.equal(shouldAutoOpenNotifications([]), false);
  assert.equal(
    shouldAutoOpenNotifications([{ id: "silent", should_popup: false }]),
    false,
  );
  assert.equal(shouldAutoOpenNotifications([{ id: "legacy" }]), false);
  assert.equal(
    shouldAutoOpenNotifications([
      { id: "silent", should_popup: false },
      { id: "popup", should_popup: true },
    ]),
    true,
  );
});

test("popup-eligible items ignore missing should_popup", () => {
  assert.deepEqual(
    getPopupEligibleItems([
      { id: "legacy" },
      { id: "silent", should_popup: false },
      { id: "popup", should_popup: true },
    ]),
    [{ id: "popup", should_popup: true }],
  );
});

test("ids to snooze are only the popup-eligible items", () => {
  assert.deepEqual(
    getIdsToSnooze([
      { id: "a", should_popup: true },
      { id: "b", should_popup: false },
      { id: "c", should_popup: true },
    ]),
    ["a", "c"],
  );
});

test("local end of day is encoded as UTC ISO", () => {
  const now = new Date(2026, 7, 20, 10, 15, 0);
  const iso = localEndOfDayUtcIso(now);
  const parsed = new Date(iso);
  assert.equal(parsed.getFullYear(), 2026);
  assert.equal(parsed.getMonth(), 7);
  assert.equal(parsed.getDate(), 20);
  assert.equal(parsed.getHours(), 23);
  assert.equal(parsed.getMinutes(), 59);
  assert.equal(parsed.getSeconds(), 59);
  assert.equal(parsed.getMilliseconds(), 999);
  assert.match(iso, /Z$/);
});
