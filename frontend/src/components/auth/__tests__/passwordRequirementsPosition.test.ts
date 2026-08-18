import test from "node:test";
import assert from "node:assert/strict";
import { calculatePasswordRequirementsPosition } from "../passwordRequirementsPosition.ts";

const viewport = { width: 800, height: 600 };

test("clamps password requirements popover to horizontal viewport margins", () => {
  const position = calculatePasswordRequirementsPosition(
    { left: 780, top: 120, right: 800, bottom: 140 },
    viewport,
    { width: 320, height: 200 },
  );

  assert.equal(position.left, 468);
  assert.equal(position.width, 320);
  assert.equal(position.placement, "below");
});

test("places password requirements above the trigger when below space is insufficient", () => {
  const position = calculatePasswordRequirementsPosition(
    { left: 200, top: 520, right: 220, bottom: 540 },
    viewport,
    { width: 320, height: 240 },
  );

  assert.equal(position.placement, "above");
  assert.equal(position.top, 272);
});

test("shrinks the panel on narrow viewports", () => {
  const position = calculatePasswordRequirementsPosition(
    { left: 0, top: 20, right: 10, bottom: 40 },
    { width: 320, height: 480 },
    { width: 320, height: 200 },
  );

  assert.equal(position.width, 296);
  assert.equal(position.left, 12);
});
