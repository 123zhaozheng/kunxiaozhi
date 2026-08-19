import test from "node:test";
import assert from "node:assert/strict";

import { ApiRequestError, authFetch, isSandboxCapacityError } from "../fetch.ts";

test("authFetch preserves structured sandbox capacity status and code", async () => {
  const previousFetch = globalThis.fetch;
  const previousLocalStorage = globalThis.localStorage;
  const previousWindow = globalThis.window;

  globalThis.localStorage = {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  } as unknown as Storage;
  globalThis.window = {
    dispatchEvent: () => true,
    location: { pathname: "/chat", search: "" },
  } as unknown as Window & typeof globalThis;
  globalThis.fetch = async () => new Response(
    JSON.stringify({
      detail: {
        error: "sandbox_capacity_unavailable",
        message: "现在有点太火热啦，沙盒席位暂时满了。请切换到 Fast 模式，或稍后再试。",
      },
    }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  );

  try {
    await assert.rejects(
      authFetch("/api/chat/stream", { method: "POST", body: "{}" }),
      (error: unknown) => {
        assert.ok(error instanceof ApiRequestError);
        assert.equal(error.status, 503);
        assert.equal(error.code, "sandbox_capacity_unavailable");
        assert.equal(
          error.message,
          "现在有点太火热啦，沙盒席位暂时满了。请切换到 Fast 模式，或稍后再试。",
        );
        assert.equal(isSandboxCapacityError(error), true);
        return true;
      },
    );
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.localStorage = previousLocalStorage;
    globalThis.window = previousWindow;
  }
});

test("isSandboxCapacityError matches duck-typed capacity rejections", () => {
  assert.equal(
    isSandboxCapacityError({ code: "sandbox_capacity_unavailable" }),
    true,
  );
  assert.equal(
    isSandboxCapacityError({
      detail: { error: "sandbox_capacity_unavailable" },
    }),
    true,
  );
  assert.equal(isSandboxCapacityError(new Error("other")), false);
  assert.equal(isSandboxCapacityError(null), false);
});
