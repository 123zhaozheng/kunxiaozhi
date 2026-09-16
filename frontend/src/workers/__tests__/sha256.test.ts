import { createHash, randomBytes } from "node:crypto";
import assert from "node:assert/strict";
import test from "node:test";

import { sha256Hex } from "../sha256";

const cases: Array<[string, Buffer]> = [
  ["empty", Buffer.alloc(0)],
  ["abc", Buffer.from("abc")],
  ["block-boundary-55", Buffer.alloc(55, 0x62)],
  ["block-boundary-56", Buffer.alloc(56, 0x63)],
  ["full-block-64", Buffer.alloc(64, 0x64)],
  ["padding-edge-63", Buffer.alloc(63, 0x65)],
  ["padding-edge-119", Buffer.alloc(119, 0x66)],
  ["multi-block-1000", Buffer.alloc(1000, 0x61)],
  ["large-1MiB", randomBytes(1 << 20)],
];

test("sha256Hex matches node:crypto across block boundaries", () => {
  for (const [name, input] of cases) {
    const expected = createHash("sha256").update(input).digest("hex");
    assert.equal(
      sha256Hex(new Uint8Array(input)),
      expected,
      `mismatch on case: ${name}`,
    );
  }
});
