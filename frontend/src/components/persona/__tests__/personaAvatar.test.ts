import test from "node:test";
import assert from "node:assert/strict";

import {
  getEmojiAvatarSrcCandidates,
  getEmojiAvatarUrl,
  getPersonaAvatarIcon,
  getPersonaAvatarIconValue,
  isPersonaImageAvatar,
} from "../personaAvatar.ts";

test("stores built-in persona avatars as compact icon keys", () => {
  const value = getPersonaAvatarIconValue("sparkles");

  assert.equal(value, "icon:sparkles");
  assert.equal(getPersonaAvatarIcon(value)?.key, "sparkles");
  assert.equal(isPersonaImageAvatar(value), false);
});

test("treats uploaded avatar urls as image avatars", () => {
  assert.equal(isPersonaImageAvatar("/api/upload/file/avatar.png"), true);
  assert.equal(getPersonaAvatarIcon("/api/upload/file/avatar.png"), null);
});

test("emoji avatar urls use same-origin anim paths with FE0F candidates", () => {
  assert.equal(getEmojiAvatarUrl("🤖"), "/emoji-assets/1f916.webp");
  assert.deepEqual(getEmojiAvatarSrcCandidates("🛡️"), [
    "/emoji-assets/1f6e1-fe0f.webp",
    "/emoji-assets/1f6e1.webp",
  ]);
  assert.deepEqual(getEmojiAvatarSrcCandidates("✨"), [
    "/emoji-assets/2728.webp",
    "/emoji-assets/2728-fe0f.webp",
  ]);
});
