import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

function readSource(path: string): string {
  return readFileSync(resolve(import.meta.dirname, path), "utf8");
}

test("safe-area utility classes map to native inset variables", () => {
  const utilities = readSource("../styles/utilities.css");

  assert.match(
    utilities,
    /\.safe-area-top\s*\{[\s\S]*padding-top:\s*calc\(\s*var\(--app-safe-area-top, 0px\) \+ var\(--safe-area-top-extra, 0px\)/,
  );
  assert.match(
    utilities,
    /\.safe-area-bottom\s*\{[\s\S]*padding-bottom:\s*calc\(\s*var\(--app-safe-area-bottom, 0px\) \+ var\(--safe-area-bottom-extra, 0px\)/,
  );
});

test("the authenticated app shell reserves both status bar and home indicator areas", () => {
  const shell = readSource("../components/layout/AppContent/AppShell.tsx");

  assert.match(
    shell,
    /const appSafeAreaTop =\s*"max\(var\(--app-safe-area-top, 0px\), var\(--app-fullscreen-safe-area-top, 0px\)\)"/,
  );
  assert.match(
    shell,
    /const appSafeAreaBottom =\s*"max\(var\(--app-safe-area-bottom, 0px\), var\(--app-fullscreen-safe-area-bottom, 0px\)\)"/,
  );
  assert.match(shell, /paddingTop:\s*appSafeAreaTop/);
  assert.match(shell, /paddingBottom:\s*appSafeAreaBottom/);
  assert.match(
    shell,
    /height:\s*`calc\(var\(--app-viewport-height, 100dvh\) - \$\{appSafeAreaTop\} - \$\{appSafeAreaBottom\}\)`/,
  );
});

test("auth and shared public pages protect their fixed headers and bottom bars", () => {
  const auth = readSource("../components/auth/AuthPage.tsx");
  const shared = readSource("../components/share/SharedPage.tsx");

  assert.match(auth, /className="[^"]*\bsafe-area-top\b/);
  assert.match(auth, /className="[^"]*\bsafe-area-bottom\b/);
  assert.match(shared, /className="[^"]*\bsafe-area-top\b/);
  assert.match(shared, /className="[^"]*\bsafe-area-bottom\b/);
});
