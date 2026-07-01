import test from "node:test";
import assert from "node:assert/strict";

import {
  getWeComIndicatorTone,
  getWeComReasonI18nKey,
  getWeComStateI18nKey,
  mergeWeComStatusMaps,
  shouldShowWeComReconnect,
} from "../wecomConnectionPresentation.ts";
import type { PersonaWeComStatus } from "../../../types/personaPreset.ts";

test("getWeComIndicatorTone maps connection states to UI tones", () => {
  assert.equal(getWeComIndicatorTone("connected"), "success");
  assert.equal(getWeComIndicatorTone("connecting"), "warning");
  assert.equal(getWeComIndicatorTone("reconnecting"), "warning");
  assert.equal(getWeComIndicatorTone("disconnected"), "danger");
  assert.equal(getWeComIndicatorTone("failed"), "danger");
  assert.equal(getWeComIndicatorTone("unknown"), "muted");
  assert.equal(getWeComIndicatorTone(undefined), "muted");
});

test("shouldShowWeComReconnect only for failed-like states", () => {
  assert.equal(shouldShowWeComReconnect("connected"), false);
  assert.equal(shouldShowWeComReconnect("connecting"), false);
  assert.equal(shouldShowWeComReconnect("disconnected"), true);
  assert.equal(shouldShowWeComReconnect("failed"), true);
  assert.equal(shouldShowWeComReconnect("unknown"), true);
});

test("getWeComReasonI18nKey returns keys for known reason codes", () => {
  assert.equal(
    getWeComReasonI18nKey("replaced"),
    "personaPresets.wecom.connection.reason.replaced",
  );
  assert.equal(getWeComReasonI18nKey(null), null);
});

test("getWeComStateI18nKey defaults unknown when state missing", () => {
  assert.equal(
    getWeComStateI18nKey(undefined),
    "personaPresets.wecom.connection.state.unknown",
  );
});

test("mergeWeComStatusMaps updates and removes entries", () => {
  const prev: Record<string, PersonaWeComStatus | undefined> = {
    a: {
      preset_id: "a",
      state: "connected",
    },
  };
  const merged = mergeWeComStatusMaps(prev, {
    a: { preset_id: "a", state: "disconnected", reason_code: "lease_lost" },
    b: null,
  });
  assert.equal(merged.a?.state, "disconnected");
  assert.equal(merged.a?.reason_code, "lease_lost");
});