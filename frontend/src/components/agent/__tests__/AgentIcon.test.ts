import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../AgentIcon.tsx", import.meta.url),
  "utf8",
);
const dynamicIconSource = readFileSync(
  new URL("../../common/DynamicIcon.tsx", import.meta.url),
  "utf8",
);

test("renders the default bot icon as the local robot emoji via DynamicIcon", () => {
  assert.match(source, /const DEFAULT_AGENT_ICON_EMOJI = "🤖"/);
  assert.match(
    source,
    /name=\{isDefaultBotIcon\(icon\) \? DEFAULT_AGENT_ICON_EMOJI : icon\}/,
  );
  // Agent icons no longer go through FluentEmoji CDN
  assert.doesNotMatch(source, /FluentEmoji|getFluentEmojiCDN|npmmirror/);
  assert.match(dynamicIconSource, /LocalFluentEmoji/);
  assert.doesNotMatch(
    dynamicIconSource,
    /from ["']@lobehub\/fluent-emoji["']|getFluentEmojiCDN/,
  );
});
