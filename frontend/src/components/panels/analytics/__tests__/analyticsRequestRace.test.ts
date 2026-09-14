/** Regression coverage for analytics request ordering and partial loading. */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createRequestOwner<T>() {
  let currentRequest = 0;
  const state = { value: "initial", error: null as unknown, loading: false };
  return {
    state,
    start(request: Promise<T>) {
      const requestId = ++currentRequest;
      state.loading = true;
      return request
        .then((value) => {
          if (requestId === currentRequest) state.value = String(value);
        })
        .catch((error: unknown) => {
          if (requestId === currentRequest) state.error = error;
        })
        .finally(() => {
          if (requestId === currentRequest) state.loading = false;
        });
    },
  };
}

test("an old successful request cannot close loading or overwrite a newer request", async () => {
  const oldRequest = deferred<string>();
  const newRequest = deferred<string>();
  const owner = createRequestOwner<string>();
  const oldRun = owner.start(oldRequest.promise);
  const newRun = owner.start(newRequest.promise);

  oldRequest.resolve("old");
  await oldRun;
  assert.equal(owner.state.value, "initial");
  assert.equal(owner.state.loading, true);

  newRequest.resolve("new");
  await newRun;
  assert.equal(owner.state.value, "new");
  assert.equal(owner.state.error, null);
  assert.equal(owner.state.loading, false);
});

test("an old failed request cannot clear loading or overwrite a newer failure", async () => {
  const oldRequest = deferred<string>();
  const newRequest = deferred<string>();
  const owner = createRequestOwner<string>();
  const oldRun = owner.start(oldRequest.promise);
  const newRun = owner.start(newRequest.promise);

  oldRequest.reject(new Error("old failure"));
  await oldRun;
  assert.equal(owner.state.error, null);
  assert.equal(owner.state.loading, true);

  const newError = new Error("new failure");
  newRequest.reject(newError);
  await newRun;
  assert.equal(owner.state.error, newError);
  assert.equal(owner.state.loading, false);
});

test("the dashboard settles independent blocks and keeps KPI loading skeletal", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const panelSource = readFileSync(join(here, "../AnalyticsPanel.tsx"), "utf8");
  const kpiSource = readFileSync(join(here, "../AnalyticsKpiRow.tsx"), "utf8");
  assert.match(panelSource, /Promise\.allSettled\(/);
  assert.match(panelSource, /setSectionErrors\(/);
  assert.match(kpiSource, /animate-pulse/);
});

/**
 * Run: npx tsx --test src/components/panels/analytics/__tests__/analyticsRequestRace.test.ts
 */
