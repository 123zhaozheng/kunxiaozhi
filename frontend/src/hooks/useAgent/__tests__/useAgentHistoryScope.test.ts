import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(__dirname, "../../useAgent.ts"), "utf8");

function eventsRequestBlock(): string {
  const start = source.indexOf(
    "const eventsPromise = sessionApi.getAllEvents(targetSessionId, {",
  );
  assert.notEqual(start, -1, "history events request should be present");
  const end = source.indexOf("const statusPromise", start);
  assert.notEqual(end, -1, "status request should follow history request");
  return source.slice(start, end);
}

test("normal history does not inherit the metadata current run filter", () => {
  const block = eventsRequestBlock();

  assert.match(block, /\.\.\.\(targetRunId \? \{ run_id: targetRunId \} : \{\}\)/);
  assert.doesNotMatch(block, /currentRunId/);
});

test("status and reconnect remain scoped to the active run", () => {
  const statusStart = source.indexOf("const statusPromise");
  const block = source.slice(
    statusStart,
    source.indexOf("// Return sessionConfig", statusStart),
  );

  assert.match(block, /sessionApi\.getStatus\(targetSessionId, currentRunId\)/);
  assert.match(block, /connectToSSE\(\s*targetSessionId,\s*currentRunId/);
});
