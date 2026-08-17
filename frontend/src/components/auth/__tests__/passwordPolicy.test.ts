import test from "node:test";
import assert from "node:assert/strict";
import { passwordPolicyError } from "../passwordPolicy.ts";

test("accepts the same compliant value regardless of input path", () => {
  assert.equal(passwordPolicyError("TypedOrPasted123!"), null);
});

test("rejects clipboard whitespace and control content without mutation", () => {
  const compliant = "TypedOrPasted123!";

  assert.equal(passwordPolicyError(`${compliant}\n`), "whitespace");
  assert.equal(passwordPolicyError(`${compliant} `), "whitespace");
  assert.equal(passwordPolicyError(`${compliant}\u0000`), "whitespace");
});
