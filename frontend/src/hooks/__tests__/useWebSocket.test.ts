import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const hooksDir = resolve(import.meta.dirname, "..");
const source = readFileSync(resolve(hooksDir, "useWebSocket.ts"), "utf8").replace(
  /\r\n/g,
  "\n",
);

test("useWebSocket exports FeedbackNotification with the feedback event contract", () => {
  assert.match(
    source,
    /export interface FeedbackNotification \{\n\s{2}type: "notification:feedback";\n\s{2}data: \{\n\s{4}preset_id: string;\n\s{4}preset_name: string;\n\s{4}rating: "up" \| "down";\n\s{4}operator: string;\n\s{4}comment: string \| null;\n\s{4}ts: string;\n\s{2}\};\n\}/,
  );
});

test("useWebSocket exposes onFeedbackNotification option mirroring onTaskComplete", () => {
  assert.match(
    source,
    /onFeedbackNotification\?: \(notification: FeedbackNotification\) => void;/,
  );
  assert.match(
    source,
    /const onFeedbackNotificationRef = useRef\(onFeedbackNotification\);/,
  );
  assert.match(
    source,
    /onFeedbackNotificationRef\.current = onFeedbackNotification;/,
  );
});

test("useWebSocket dispatches notification:feedback messages to the callback", () => {
  assert.match(
    source,
    /message\.type === "notification:feedback" &&\n\s+onFeedbackNotificationRef\.current/,
  );
  assert.match(
    source,
    /onFeedbackNotificationRef\.current\(message\);/,
  );
});

test("useWebSocket does not crash on unknown messages (dispatch is type-guarded inside try/catch)", () => {
  // Dispatch only happens for known types; the whole handler is wrapped in try/catch.
  assert.match(
    source,
    /const message = JSON\.parse\(event\.data\);[\s\S]*?if \(\n\s+message\.type === "notification:feedback"/,
  );
  assert.match(
    source,
    /} catch \(e\) \{\n\s+console\.error\("\[WebSocket\] Failed to parse message:"/,
  );
});
