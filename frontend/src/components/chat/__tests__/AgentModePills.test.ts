import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../AgentModePills.tsx", import.meta.url), "utf8");
const chatViewSource = readFileSync(
  new URL("../../layout/AppContent/ChatView.tsx", import.meta.url),
  "utf8",
);

test("renders the API agent list with shared presentation and catalog resolvers", () => {
  assert.match(source, /sortAgentModes\(agents\)/);
  // Mode icons must come from AgentModeIcon (flat lucide line icons), not from
  // AgentIcon/DynamicIcon, which resolves any ASCII name to a 3D emoji asset.
  assert.match(source, /<AgentModeIcon\s+agentId=\{agent\.id\}/);
  assert.doesNotMatch(source, /resolveAgentModeIcon/);
  assert.match(
    source,
    /resolveAgentDisplayName\(agent,\s*i18n\.language,\s*t\)/,
  );
  assert.match(source, /agents\.length <= 1/);
  assert.match(source, /onSelectAgent\?\.\(agent\.id\)/);
});

test("keeps locked mode pills focusable and explains the lock", () => {
  assert.match(source, /lockedReason\?: string \| null/);
  assert.match(source, /aria-disabled=\{isLocked \? true : undefined\}/);
  assert.match(source, /disabled=\{!onSelectAgent && !isLocked\}/);
  assert.match(source, /title=\{isLocked \? lockTitle : undefined\}/);
  assert.match(source, /welcomeModes\.lockedReason/);
  assert.match(source, /if \(!isLocked\) onSelectAgent\?\.\(agent\.id\)/);
});

test("every supported locale translates the locked mode explanation", () => {
  for (const locale of ["zh", "en", "ja", "ko", "ru"]) {
    const messages = JSON.parse(
      readFileSync(
        new URL(`../../../i18n/locales/${locale}.json`, import.meta.url),
        "utf8",
      ),
    ) as { welcomeModes?: { lockedReason?: unknown } };

    assert.equal(
      typeof messages.welcomeModes?.lockedReason,
      "string",
      `${locale} is missing welcomeModes.lockedReason`,
    );
  }
});

test("ChatView reuses the composer agent switch handler on the welcome page", () => {
  assert.match(
    chatViewSource,
    /agents=\{agents\}[\s\S]*onSelectAgent=\{chatInputProps\.onSelectAgent\}/,
  );
});
