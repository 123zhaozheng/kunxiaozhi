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
  // Mode icons must come from AgentModeIcon (flat lucide set), never from
  // DynamicIcon/AgentIcon which resolve ASCII names to 3D emoji assets.
  assert.match(source, /<AgentModeIcon agentId=\{agent\.id\}/);
  assert.match(
    source,
    /resolveAgentDisplayName\(agent,\s*i18n\.language,\s*t\)/,
  );
  assert.match(source, /agents\.length <= 1/);
  assert.match(source, /onSelectAgent\?\.\(agent\.id\)/);
});

test("ChatView reuses the composer agent switch handler on the welcome page", () => {
  assert.match(
    chatViewSource,
    /agents=\{agents\}[\s\S]*onSelectAgent=\{chatInputProps\.onSelectAgent\}/,
  );
});
