import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

function readSource(path: string): string {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("React Flow base styles are owned by the global entrypoint", () => {
  const main = readSource("../../../main.tsx");
  const outlinePanel = readSource(
    "../../layout/AppContent/MessageOutlinePanel.tsx",
  );

  assert.match(main, /import ["']@xyflow\/react\/dist\/style\.css["']/);
  assert.doesNotMatch(
    outlinePanel,
    /import ["']@xyflow\/react\/dist\/style\.css["']/,
  );
});
