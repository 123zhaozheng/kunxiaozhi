import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const editorSource = readFileSync(
  new URL("../PersonaEditorModal.tsx", import.meta.url),
  "utf8",
);

test("WeCom segmented reply exposes and persists a character target", () => {
  assert.match(
    editorSource,
    /wecomDraft\.segmented_reply\s*&&\s*\(/,
  );
  assert.match(
    editorSource,
    /value=\{wecomDraft\.segment_target_chars\}/,
  );
  assert.match(
    editorSource,
    /WECOM_SEGMENT_TARGET_CHAR_OPTIONS\s*=\s*\[300,\s*500,\s*600\]/,
  );
  assert.match(editorSource, /WECOM_SEGMENT_TARGET_CHAR_OPTIONS\.map/);
  assert.match(
    editorSource,
    /segment_target_chars:\s*wecomDraft\.segment_target_chars/,
  );
});
