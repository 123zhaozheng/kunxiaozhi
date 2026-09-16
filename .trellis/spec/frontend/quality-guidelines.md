# Quality Guidelines

> Linting, testing, and accessibility standards for the frontend.

---

## Overview

The frontend uses Vitest for testing and follows consistent patterns for
code quality, accessibility, and responsive design.

---

## PDF preview (Chrome 109 / pdf.js)

**Problem**: Intranet baseline is Chrome 109. `pdfjs-dist` modern build (`build/pdf*.mjs`) calls `Promise.withResolvers()`, which exists only in Chrome ≥119. Opening sidebar PDF preview then throws and hits the top-level `ErrorBoundary` ("出了点问题").

**Rule**: Always load **legacy** pdf.js for both main library and worker. Keep main + worker on the same build (never mix modern main with legacy worker or the reverse). Do not load workers from CDN.

| Piece | Correct path |
|-------|----------------|
| Vite bare `pdfjs-dist` (used by `react-pdf`) | alias `/^pdfjs-dist$/` → `pdfjs-dist/legacy/build/pdf.mjs` |
| Modern worker paths | alias `pdfjs-dist/build/pdf.worker(.min).mjs` → `legacy/build/...` |
| `PdfPreview` worker URL | `import ... from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url"` |

**Why bare package is exact regex**: `find: /^pdfjs-dist$/` so subpaths (`types/*`, `legacy/*`) still resolve normally. Worker aliases must be listed **before** the bare package alias.

**Don't**:
```ts
// Wrong — modern build on Chrome 109
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import * as pdfjs from "pdfjs-dist"; // without Vite legacy alias
```

```ts
// Correct — explicit legacy worker + Vite aliases for react-pdf
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";
pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
```

**Tests**: `pdfPreviewNative.test.ts` must lock the legacy worker import and the Vite aliases.

**Out of scope for this rule**: `/api/feedback` 403 when listing feedback without `feedback:read` (separate permission issue).

---

## Emoji icons (intranet / offline)

**Problem**: Chrome 109 intranet cannot reach `registry.npmmirror.com`. `@lobehub/fluent-emoji` with `type="3d"` loads assets from that CDN at runtime, so agent / subagent emoji icons appear blank.

**Rule**: Business UI must not import `FluentEmoji` or `getFluentEmojiCDN` for rendering.

| Use case | Correct path |
|----------|----------------|
| Persona emoji avatar | `getEmojiAvatarUrl` / `PersonaAvatarIcon` → `/emoji-assets/{cp}.webp` |
| Agent selector / DynamicIcon | `LocalFluentEmoji` → same origin anim webp |
| Subagent default emoji | `LocalFluentEmoji` |
| Role chrome (search/code/…) | Lucide (monochrome; not CDN) |
| Model / vendor logos | `@lobehub/icons-static-svg` (bundled) |

**Contracts**:
- URL: `/emoji-assets/{codepoints}.webp` (hex lowercase, hyphen-joined; FE0F may be present or omitted).
- `getEmojiAvatarSrcCandidates(emoji)` returns exact → `+fe0f` / strip-fe0f variants.
- `LocalFluentEmoji` walks candidates on `img.onError`, then falls back to the unicode glyph (never blank).
- Assets are produced by `frontend/scripts/fetch-emoji-assets.mjs` from allowlist + npmmirror **anim** packages; Docker frontend-builder runs that script when the builder has network/mirror.
- Some common glyphs are **missing from fluent-emoji-anim** (permanent 404 on CDN): ✨ `2728`, ⭐ `2b50`, ⚡ `26a1`, ✍️ `270d`. Prefer near-equivalents in pickers (💫 / 🌟 / 💡 / 📝). Do not re-add those codepoints to allowlist without teaching the fetch script to ignore permanent 404s.

**Don't**:
```tsx
// Wrong — runtime CDN
import { FluentEmoji } from "@lobehub/fluent-emoji";
<FluentEmoji emoji="🤖" type="3d" size={22} />
```

```tsx
// Correct — same-origin
import { LocalFluentEmoji } from "../common/LocalFluentEmoji";
<LocalFluentEmoji emoji="🤖" size={22} />
```

**Intranet check**: DevTools Network → filter `npmmirror` / `fluent-emoji` → expect **0** requests for agent/subagent emoji; icons should load as `/emoji-assets/*.webp`.

### Scenario: serving packaged emoji assets through FastAPI

#### 1. Scope / Trigger

- Trigger: adding or changing same-origin UI assets rendered by `<img>` in the packaged frontend.
- Why: image requests do not carry the Bearer token stored by the SPA, so an asset can exist in `dist/` and still render blank when `AuthMiddleware` rejects it.

#### 2. Signatures

- Browser request: `GET /emoji-assets/{codepoints}.webp` (no `Authorization` header required).
- Packaged source: `frontend/dist/emoji-assets/{codepoints}.webp`.
- Runtime route: `app.mount("/emoji-assets", StaticFiles(...), name="emoji-assets")` in `src/api/main.py`.

#### 3. Contracts

- Docker runs `node scripts/fetch-emoji-assets.mjs` before `pnpm run build`; Vite copies `public/emoji-assets/` into `dist/emoji-assets/`.
- `AuthMiddleware.PUBLIC_PREFIXES` must contain `/emoji-assets/` because browser image requests are anonymous static requests.
- A present WebP returns `200` with `Content-Type: image/webp`; a missing asset must not be replaced by the SPA HTML.

#### 4. Validation & Error Matrix

| Condition | Expected result |
|-----------|-----------------|
| File exists; no auth header | `200`, exact WebP bytes |
| File missing | static `404`; `LocalFluentEmoji` tries variants then Unicode fallback |
| `dist/emoji-assets/` absent | route is not mounted; packaging/build validation fails |
| Public auth prefix absent | `401`; visible symptom is a blank agent/persona icon |

#### 5. Good / Base / Bad Cases

- Good: packaged image contains the directory, FastAPI mounts it, and an anonymous request returns WebP bytes.
- Base: an uncommon emoji file is missing and `LocalFluentEmoji` renders the Unicode glyph.
- Bad: verifying only the React `src` URL or local Vite dev server; this misses packaged FastAPI authentication and routing.

#### 6. Tests Required

- API regression: build a temporary `dist/emoji-assets/1f916.webp`, call it without auth, and assert status `200`, exact bytes, and `image/webp`.
- Frontend build check: assert `dist/emoji-assets/1f916.webp` (default Agent icon) exists and is non-empty.
- Runtime source scan: no business import of `FluentEmoji` / `getFluentEmojiCDN` and no runtime CDN URL.

#### 7. Wrong vs Correct

```python
# Wrong: the file exists in dist, but anonymous <img> requests are intercepted.
PUBLIC_PREFIXES = ("/assets/", "/icons/")

# Correct: mount the directory and exempt its URL prefix from Bearer auth.
PUBLIC_PREFIXES = ("/assets/", "/icons/", "/emoji-assets/")
app.mount("/emoji-assets", StaticFiles(directory=str(emoji_dir)), name="emoji-assets")
```

---

## Web Crypto (`crypto.subtle`) is secure-context only

**Problem**: `crypto.subtle` is `undefined` outside secure contexts (HTTPS or `http://localhost`). The intranet k8s deployment is plain `http://<node-ip>:30080`, so any code path touching `crypto.subtle` throws there (e.g. upload hashing crashed with `Cannot read properties of undefined (reading 'digest')` for months because dev/prod验证 all ran on HTTPS/localhost).

**Rule**: Guard every `crypto.subtle` usage: `if (typeof crypto !== "undefined" && crypto.subtle) { native } else { fallback }`. The upload hash worker falls back to `src/workers/sha256.ts` (pure-JS FIPS 180-4, pinned by `src/workers/__tests__/sha256.test.ts` against `node:crypto`). Same caution applies to `crypto.randomUUID()` — use the `uuid()` util in insecure contexts.

---

## Testing

### Framework

- Prefer `tsx --test` for TSX source tests that import React markup helpers (plain `node --test` may fail module resolution on `.tsx`).
- Tests are placed in `__tests__/` subdirectories next to the component/hook
- Test files follow `<name>.test.ts` or `<name>.test.tsx` naming

### Test patterns

```tsx
// Component test
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Checkbox } from "../Checkbox";

describe("Checkbox", () => {
  it("renders checked state", () => {
    render(<Checkbox checked={true} />);
    expect(screen.getByRole("checkbox")).toHaveAttribute("aria-checked", "true");
  });
});
```

### Coverage

- Critical UI flows have test coverage (auth, chat, PWA, routing)
- Panel stores have unit tests (`blockPreviewStore.test.ts`, `persistentToolPanelState.test.ts`)
- Utility functions have unit tests (`selectorPagination.test.ts`, `goalCommands.test.ts`)
- Many components lack tests — this is an area for improvement

---

## Accessibility

### ARIA attributes

Interactive elements use proper ARIA:

```tsx
<div role="checkbox" aria-checked={checked}>
<button aria-label={t("common.previous")}>
```

### Keyboard support

- `PanelSearchInput` handles composition events for CJK input
- Tab navigation works for all interactive elements
- Focus management for modals and panels

### Semantic HTML

- `<nav>` for navigation bars
- `<main>` / `<article>` where appropriate
- `<h1>`-`<h3>` hierarchy in panels

---

## Responsive Design

### Breakpoints

| Breakpoint | Min Width | Target |
|-----------|-----------|--------|
| default | 0 | Mobile |
| `sm:` | 640px | Small tablets |
| `lg:` | 1024px | Desktop |
| `xl:` | 1280px | Large desktop |
| `2xl:` | 1536px | Ultra-wide |

### Mobile-specific patterns

```tsx
// Safe area handling for native builds
<div className="safe-area-top safe-area-bottom">

// Dynamic viewport height
<div className="min-h-[100svh] min-h-[100dvh]">

// Keyboard-aware layout
const isKeyboardOpen = useMobileKeyboardAware();

// Mobile device detection
import { isMobileDevice } from "../../utils/mobile";
```

---

## Code Style

- **TypeScript strict mode** — no implicit any, strict null checks
- **Named exports** — avoid default exports
- **TailwindCSS** — no inline styles, no CSS modules
- **i18next** — all user-facing strings use `t("key")`, no hardcoded strings
- **`clsx`** for conditional class composition (not `classnames`)

---

## Common Mistakes

- ❌ Don't hardcode user-facing strings — use `t("key")` with i18next
- ❌ Don't forget `dark:` variants for dark mode support
- ❌ Don't use CSS modules or inline styles — use TailwindCSS
- ❌ Don't forget `aria-*` attributes on custom interactive elements
- ❌ Don't forget safe area classes for native builds (Capacitor/Tauri)
- ❌ Don't use `default` exports — use named exports
