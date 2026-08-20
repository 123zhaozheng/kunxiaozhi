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

test("mention popup is not gated by welcome-page onMentionQueryChange", () => {
  assert.match(
    chatInputSource,
    /mention\.isActive && mentionMode === "persona"/,
  );
  assert.match(chatInputSource, /mention\.isActive && mentionMode === "team"/);
  assert.doesNotMatch(
    chatInputSource,
    /mention\.isActive &&\s*!onMentionQueryChange/,
  );
});

test("IME composition does not select slash or mention items", () => {
  assert.match(chatInputSource, /e\.nativeEvent\.isComposing/);
  assert.match(chatInputSource, /e\.keyCode === 229/);
  assert.match(chatInputSource, /if \(isComposing\) return;/);
});

test("slash menu consumes Enter and Tab even when there are no matches", () => {
  assert.match(
    chatInputSource,
    /if \(slashCommandOpen\) \{[\s\S]*if \(e\.key === "Enter" \|\| e\.key === "Tab"\) \{\s*e\.preventDefault\(\);/,
  );
});

test("slash menu closes on outside click without sending", () => {
  assert.match(chatInputSource, /setSlashMenuDismissed\(true\)/);
  assert.match(chatInputSource, /slashMenuRef/);
});

test("slash skill emphasis prefixes onSend content without changing enabled_skills", () => {
  assert.match(chatInputSource, /buildEmphasizedUserMessage/);
  assert.match(chatInputSource, /chat\.skillEmphasis\.mustUse/);
  assert.doesNotMatch(chatInputSource, /enabled_skills/);
});

test("slash skill chips sit inside the composer and clear after send", () => {
  assert.match(
    chatInputSource,
    /px-2\.5 pt-1[\s\S]*emphasizedSkillNames\.length > 0/,
  );
  assert.match(chatInputSource, /onClearEmphasizedSkills\?\.\(\)/);
  assert.match(chatInputSource, /onRestoreEmphasizedSkills\?\.\(draftSkills\)/);
  assert.doesNotMatch(chatInputSource, /ToolbarChip/);
});
