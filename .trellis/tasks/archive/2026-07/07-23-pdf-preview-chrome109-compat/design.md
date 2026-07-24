# Design: PDF preview Chrome 109 compat

## Problem

`pdfjs-dist` modern build uses ES2024 APIs including `Promise.withResolvers`. Chrome 109 lacks it. Opening PDF preview loads `PdfPreview` → `react-pdf` → modern pdf.js → uncaught TypeError → top-level `ErrorBoundary` full-page crash.

## Solution

Route all pdf.js usage through the **legacy** build shipped with `pdfjs-dist@5.4.296`, which polyfills `Promise.withResolvers` (and related APIs).

### 1. Vite alias (primary)

In `frontend/vite.config.ts` `resolve.alias`, map:

| Find | Replacement |
|------|-------------|
| `pdfjs-dist/build/pdf.worker.min.mjs` | `pdfjs-dist/legacy/build/pdf.worker.min.mjs` |
| `pdfjs-dist` (package root used by `react-pdf`) | `pdfjs-dist/legacy/build/pdf.mjs` |

Order matters: more specific worker aliases before the bare `pdfjs-dist` alias if using string/prefix matching. Prefer precise `find` patterns (exact or regex) so we don't break unrelated subpath imports that don't exist under legacy.

Verify after alias that:

- `import * as pdfjs from 'pdfjs-dist'` (inside `react-pdf`) resolves to legacy main.
- Worker URL import in `PdfPreview` resolves to legacy worker.

### 2. Explicit worker import in PdfPreview

Change:

```ts
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
```

to:

```ts
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";
```

Even with Vite alias, explicit legacy path makes intent obvious and survives alias misconfiguration reviews.

### 3. Optional safety polyfill (only if needed)

If module evaluation still hits modern code before legacy polyfills run, add a tiny `Promise.withResolvers` polyfill in `PdfPreview` module top (before react-pdf import) or a dedicated `pdfCompat.ts` imported first:

```ts
if (typeof Promise.withResolvers !== "function") {
  Promise.withResolvers = function <T>() {
    let resolve!: (v: T | PromiseLike<T>) => void;
    let reject!: (r?: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };
}
```

Prefer legacy build alone first; add polyfill only if tests/manual prove still needed.

## Non-goals

- Feedback 403.
- Downgrading pdfjs major version.
- Changing DocumentPreview layout / UX beyond crash fix.

## Tradeoffs

| Approach | Pros | Cons |
|----------|------|------|
| Legacy build alias | Official path, polyfills match pdf.js needs, keeps current versions | Slightly larger bundle |
| Global polyfill only | Small change | Easy to miss worker (worker has separate global); incomplete if other modern APIs used |
| Downgrade pdfjs | Avoids modern APIs | Version skew with react-pdf peer, more risk |

**Chosen:** legacy build (alias + explicit worker path).

## Compatibility

- Chrome 109 baseline.
- Modern browsers still work via same legacy build.
- Main/worker must both be legacy.

## Rollback

Revert vite alias + `PdfPreview` worker import + tests. No backend/data migration.

## Validation strategy

- Source/unit tests assert worker import path contains `legacy`.
- Existing `pdfPreviewNative.test.ts` version pins stay valid.
- Optional: unit test that polyfill-compatible path is used (string assert on vite config / PdfPreview source).
