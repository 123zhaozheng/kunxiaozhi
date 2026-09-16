import assert from "node:assert/strict";
import test from "node:test";

import i18n from "../../../i18n/index.ts";
import { analyticsApi } from "../analytics.ts";
import { ApiRequestError, authFetch } from "../fetch.ts";
import { uploadApi } from "../upload.ts";

async function withChinese<T>(callback: () => Promise<T>): Promise<T> {
  const previousLanguage = i18n.language;
  await i18n.changeLanguage("zh");
  try {
    return await callback();
  } finally {
    await i18n.changeLanguage(previousLanguage);
  }
}

function installBrowserMocks(): {
  previousFetch: typeof globalThis.fetch;
  previousLocalStorage: Storage;
  previousWindow: Window & typeof globalThis;
} {
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

  return { previousFetch, previousLocalStorage, previousWindow };
}

function restoreBrowserMocks(mocks: ReturnType<typeof installBrowserMocks>): void {
  globalThis.fetch = mocks.previousFetch;
  globalThis.localStorage = mocks.previousLocalStorage;
  globalThis.window = mocks.previousWindow;
}

test("authFetch localizes a 500 fallback and includes the HTTP status", async () => {
  await withChinese(async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({}), {
        status: 500,
        statusText: "Internal Server Error",
        headers: { "Content-Type": "application/json" },
      });

    try {
      await assert.rejects(
        authFetch("/api/failing", { skipAuth: true }),
        (error: unknown) => {
          assert.ok(error instanceof ApiRequestError);
          assert.equal(error.status, 500);
          assert.equal(error.message, "请求失败（HTTP 500）");
          assert.notEqual(error.message, "Request failed: Internal Server Error");
          return true;
        },
      );
    } finally {
      globalThis.fetch = previousFetch;
    }
  });
});

test("authFetch preserves detail translation when the backend provides detail", async () => {
  await withChinese(async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: "Password must be 8-64 characters" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });

    try {
      await assert.rejects(
        authFetch("/api/failing", { skipAuth: true }),
        (error: unknown) => {
          assert.ok(error instanceof ApiRequestError);
          assert.equal(error.status, 400);
          assert.equal(error.message, "密码不符合密码策略要求");
          return true;
        },
      );
    } finally {
      globalThis.fetch = previousFetch;
    }
  });
});

test("upload and analytics fallbacks use localized status-aware messages", async () => {
  await withChinese(async () => {
    const mocks = installBrowserMocks();
    try {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({}), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });

      await assert.rejects(
        uploadApi.uploadAvatar(new Blob(["avatar"], { type: "image/png" }) as File),
        (error: unknown) => {
          assert.ok(error instanceof Error);
          assert.equal(error.message, "上传失败（HTTP 500）");
          return true;
        },
      );

      await assert.rejects(
        analyticsApi.exportSessionsCsv("2026-09-01", "2026-09-16"),
        (error: unknown) => {
          assert.ok(error instanceof Error);
          assert.equal(error.message, "导出失败（HTTP 500）");
          return true;
        },
      );
    } finally {
      restoreBrowserMocks(mocks);
    }
  });
});

test("upload and analytics keep backend detail messages unchanged", async () => {
  await withChinese(async () => {
    const mocks = installBrowserMocks();
    try {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ detail: "backend detail" }), {
          status: 502,
          headers: { "Content-Type": "application/json" },
        });

      await assert.rejects(
        uploadApi.uploadAvatar(new Blob(["avatar"], { type: "image/png" }) as File),
        (error: unknown) => {
          assert.ok(error instanceof Error);
          assert.equal(error.message, "backend detail");
          return true;
        },
      );

      await assert.rejects(
        analyticsApi.exportSessionsCsv("2026-09-01", "2026-09-16"),
        (error: unknown) => {
          assert.ok(error instanceof Error);
          assert.equal(error.message, "backend detail");
          return true;
        },
      );
    } finally {
      restoreBrowserMocks(mocks);
    }
  });
});
