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

test("enforces Unicode length, UTF-8 byte, and composition boundaries", () => {
  assert.equal(passwordPolicyError("Aa1!abc"), "length");
  assert.equal(passwordPolicyError("A1!" + "é".repeat(36)), "bytes");
  assert.equal(passwordPolicyError("alllowercasepassword"), "composition");
  assert.equal(passwordPolicyError("abcd1234!"), "composition");
  assert.equal(passwordPolicyError("ABCD1234!"), "composition");
  assert.equal(passwordPolicyError("Abcdefg!"), "composition");
  assert.equal(passwordPolicyError("Abcd12345"), "composition");
  assert.equal(passwordPolicyError("Aa1!securepass"), null);
});
