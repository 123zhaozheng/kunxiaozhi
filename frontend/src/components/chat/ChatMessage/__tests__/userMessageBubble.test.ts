import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { getUserMessageActionButtonVisibilityClass } from "../userMessageBubbleState";

const userMessageBubbleSource = readFileSync(
  new URL("../UserMessageBubble.tsx", import.meta.url),
  "utf8",
);

test("keeps user message action buttons visible for the latest message", () => {
  const className = getUserMessageActionButtonVisibilityClass(true);

  assert.equal(className.includes("opacity-0"), false);
  assert.equal(className.includes("group-hover:opacity-100"), false);
});

test("hides older user message action buttons until hover", () => {
  const className = getUserMessageActionButtonVisibilityClass(false);

  assert.equal(className.includes("opacity-0"), true);
  assert.equal(className.includes("group-hover:opacity-100"), true);
});

test("emphasized user messages render a skill pill and copy visible text", () => {
  assert.match(userMessageBubbleSource, /parseEmphasizedUserMessage/);
  assert.match(userMessageBubbleSource, /showSkillPill/);
  assert.match(userMessageBubbleSource, /copyToClipboard\(visibleContent\)/);
  assert.match(userMessageBubbleSource, /<Boxes/);
  assert.match(userMessageBubbleSource, /text-blue-600 dark:text-blue-400/);
  assert.match(userMessageBubbleSource, /parsed\.skillNames\.join/);
  assert.match(userMessageBubbleSource, /parsed\.visibleContent/);
  assert.match(userMessageBubbleSource, /<MarkdownContent content=\{content!\} \/>/);
  assert.doesNotMatch(userMessageBubbleSource, /请必须使用/);
  assert.doesNotMatch(
    userMessageBubbleSource,
    /copyToClipboard\(content/,
  );
});
