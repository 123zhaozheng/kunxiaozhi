import test from "node:test";
import assert from "node:assert/strict";
import {
  readOaSsoToken,
  removeOaSsoTokenParams,
} from "../oaSsoToken.ts";

test("reads the OA portal Accesstoken parameter", () => {
  const params = new URLSearchParams("Accesstoken=oa-value");

  assert.equal(readOaSsoToken(params), "oa-value");
});

test("prefers Accesstoken while retaining legacy aliases", () => {
  assert.equal(
    readOaSsoToken(
      new URLSearchParams(
        "Accesstoken=official&token=legacy&oa_token=legacy-two",
      ),
    ),
    "official",
  );
  assert.equal(
    readOaSsoToken(new URLSearchParams("token=legacy")),
    "legacy",
  );
  assert.equal(
    readOaSsoToken(new URLSearchParams("oa_token=legacy-two")),
    "legacy-two",
  );
});

test("removes all OA token aliases from the browser URL parameters", () => {
  const params = new URLSearchParams(
    "Accesstoken=official&token=legacy&oa_token=legacy-two&from=oa",
  );

  removeOaSsoTokenParams(params);

  assert.equal(params.toString(), "from=oa");
});
