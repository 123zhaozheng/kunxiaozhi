import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPersonaPresetListUrl,
  buildPersonaPresetPreferenceUrl,
  personaPresetApi,
} from "../personaPreset.ts";

test("buildPersonaPresetPreferenceUrl encodes preset ids", () => {
  assert.equal(
    buildPersonaPresetPreferenceUrl("preset/1"),
    "/api/persona-presets/preset%2F1/preference",
  );
});

test("buildPersonaPresetListUrl keeps page-sized pagination params", () => {
  assert.equal(
    buildPersonaPresetListUrl({ skip: 12, limit: 12, q: "planner" }),
    "/api/persona-presets/?q=planner&skip=12&limit=12",
  );
});

test("personaPresetApi.list reuses in-flight and fresh identical list requests", async () => {
  const previousFetch = globalThis.fetch;
  const previousLocalStorage = globalThis.localStorage;
  const previousWindow = globalThis.window;
  let fetchCount = 0;

  globalThis.localStorage = {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  } as unknown as Storage;
  globalThis.window = {
    dispatchEvent: () => true,
    location: { pathname: "/chat", search: "" },
  } as unknown as Window & typeof globalThis;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return new Response(
      JSON.stringify({
        presets: [],
        total: 0,
        skip: 0,
        limit: 20,
      }),
      { status: 200 },
    );
  };

  try {
    const params = { skip: 0, limit: 20 };
    const [first, second] = await Promise.all([
      personaPresetApi.list(params),
      personaPresetApi.list(params),
    ]);
    const third = await personaPresetApi.list(params);

    assert.equal(fetchCount, 1);
    assert.equal(first.total, 0);
    assert.equal(second.total, 0);
    assert.equal(third.total, 0);
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.localStorage = previousLocalStorage;
    globalThis.window = previousWindow;
  }
});

test("personaPresetApi.getWeComNotifyTargets returns empty targets on 404", async () => {
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
  globalThis.fetch = async () => new Response("{}", { status: 404 });

  try {
    const result = await personaPresetApi.getWeComNotifyTargets("preset-1");
    assert.deepEqual(result, { targets: [] });
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.localStorage = previousLocalStorage;
    globalThis.window = previousWindow;
  }
});

test("personaPresetApi.updateWeComNotifyTargets sends PUT with full target list", async () => {
  const previousFetch = globalThis.fetch;
  const previousLocalStorage = globalThis.localStorage;
  const previousWindow = globalThis.window;
  let capturedRequest: RequestInit | undefined;

  globalThis.localStorage = {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  } as unknown as Storage;
  globalThis.window = {
    dispatchEvent: () => true,
    location: { pathname: "/chat", search: "" },
  } as unknown as Window & typeof globalThis;
  globalThis.fetch = async (_input: RequestInfo | URL, init?: RequestInit) => {
    capturedRequest = init;
    return new Response(
      JSON.stringify({
        targets: [
          { username: "10001", bound: true },
          { username: "10002", bound: false },
        ],
      }),
      { status: 200 },
    );
  };

  try {
    const result = await personaPresetApi.updateWeComNotifyTargets("preset-1", [
      "10001",
      "10002",
    ]);
    assert.equal(capturedRequest?.method, "PUT");
    assert.equal(
      capturedRequest?.body,
      JSON.stringify({ targets: ["10001", "10002"] }),
    );
    assert.deepEqual(
      result.targets.map((item) => item.username),
      ["10001", "10002"],
    );
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.localStorage = previousLocalStorage;
    globalThis.window = previousWindow;
  }
});
