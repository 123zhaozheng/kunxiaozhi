import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const chatInputSource = readFileSync(
  new URL("../ChatInput.tsx", import.meta.url),
  "utf8",
);

test("team agent mention switches teams instead of persona presets", () => {
  assert.match(chatInputSource, /useTeamMentionSearch/);
  assert.match(
    chatInputSource,
    /const mentionMode =[\s\S]*currentAgent === "team"[\s\S]*\? "team"[\s\S]*: "persona"/,
  );
  assert.match(
    chatInputSource,
    /function applyTeamMentionSelection|const applyTeamMentionSelection/,
  );
  assert.match(chatInputSource, /onSelectTeam\?\.\(team\.id\)/);
  assert.match(chatInputSource, /<TeamMentionPopup/);
  assert.match(chatInputSource, /mentionMode === "team"/);
  assert.match(chatInputSource, /mentionMode === "persona"/);
});

test("team agent placeholder says @ switches teams", () => {
  assert.match(chatInputSource, /chat\.teamPlaceholder/);
  assert.match(
    chatInputSource,
    /mentionMode === "team"[\s\S]*chat\.teamPlaceholder/,
  );
});

test("team agent requires a selected team before submitting", () => {
  assert.match(
    chatInputSource,
    /const requiresTeamSelection = currentAgent === "team" && !selectedTeamId/,
  );
  assert.match(
    chatInputSource,
    /if \(!canSend \|\| requiresTeamSelection\) return;/,
  );
  assert.match(chatInputSource, /!requiresTeamSelection/);
});
