import test from "node:test";
import assert from "node:assert/strict";

import { isCheckpointsCleaned } from "../sessionRetention";

test("no stamp means the session is resumable", () => {
  assert.equal(isCheckpointsCleaned(undefined, "2026-09-01T00:00:00Z"), false);
  assert.equal(isCheckpointsCleaned("", "2026-09-01T00:00:00Z"), false);
  assert.equal(isCheckpointsCleaned(null, "2026-09-01T00:00:00Z"), false);
});

test("stamp at or after last activity marks the session cleaned", () => {
  assert.equal(
    isCheckpointsCleaned("2026-09-10T00:00:00Z", "2026-09-01T00:00:00Z"),
    true,
  );
  assert.equal(
    isCheckpointsCleaned("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"),
    true,
  );
});

test("a newer turn makes an older stamp stale", () => {
  assert.equal(
    isCheckpointsCleaned("2026-09-01T00:00:00Z", "2026-09-10T00:00:00Z"),
    false,
  );
});

test("unparsable values degrade safely", () => {
  assert.equal(isCheckpointsCleaned("not-a-date", "2026-09-01T00:00:00Z"), false);
  assert.equal(isCheckpointsCleaned("2026-09-01T00:00:00Z", "bogus"), true);
  assert.equal(isCheckpointsCleaned("2026-09-01T00:00:00Z", null), true);
});
