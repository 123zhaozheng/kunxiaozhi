import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const analyticsTs = readFileSync(join(here, "../analytics.ts"), "utf8");

describe("analytics CSV export API client", () => {
  it("exposes exportSessionsCsv and exportUsersCsv", () => {
    assert.match(analyticsTs, /exportSessionsCsv\s*\(/);
    assert.match(analyticsTs, /exportUsersCsv\s*\(/);
    assert.match(analyticsTs, /sessions\/export\.csv/);
    assert.match(analyticsTs, /users\/export\.csv/);
  });

  it("reuses list filter params without skip/limit for export", () => {
    assert.match(analyticsTs, /includePagination:\s*false/);
    assert.match(analyticsTs, /persona_preset_id/);
    assert.match(analyticsTs, /agent_id/);
    assert.match(analyticsTs, /role_id/);
    assert.match(analyticsTs, /authenticatedRequest/);
  });
});
