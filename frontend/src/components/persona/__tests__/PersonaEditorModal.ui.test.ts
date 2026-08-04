import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const componentSource = readFileSync(
  join(import.meta.dirname, "../PersonaEditorModal.tsx"),
  "utf8",
);

const personaCss = readFileSync(
  join(import.meta.dirname, "../../../styles/persona.css"),
  "utf8",
);

test("skill dropdown clear action renders as a labeled soft button", () => {
  assert.match(
    componentSource,
    /className="ppe-skill-dropdown__clear-all"[\s\S]*>\s*\{\s*t\("common\.clearAll", "清除全部"\)\s*\}\s*<\/button>/,
  );
  assert.doesNotMatch(
    componentSource,
    /className="ppe-skill-dropdown__clear-all"[\s\S]{0,260}<X size=\{14\}/,
  );
});

test("skill dropdown loads more skills when scrolled near the bottom", () => {
  assert.match(componentSource, /const PERSONA_SKILL_PAGE_SIZE = 20;/);
  assert.match(componentSource, /appendPages: true/);
  assert.match(
    componentSource,
    /const distanceToBottom =\s*target\.scrollHeight - target\.scrollTop - target\.clientHeight;/,
  );
  assert.match(componentSource, /onScroll=\{handleSkillListScroll\}/);
  assert.match(componentSource, /setSkillPage\(\(page\) => page \+ 1\)/);
});

test("skill dropdown header uses the soft professional search treatment", () => {
  assert.match(
    personaCss,
    /\.ppe-skill-search\s*\{[\s\S]*border:\s*1px solid transparent;/,
  );
  assert.match(personaCss, /\.ppe-skill-search\s*\{[\s\S]*height:\s*2\.25rem;/);
  assert.match(
    personaCss,
    /\.ppe-skill-dropdown__clear-all\s*\{[\s\S]*padding:\s*0 0\.625rem;/,
  );
  assert.match(
    personaCss,
    /\.ppe-skill-dropdown__clear-all\s*\{[\s\S]*font-weight:\s*600;/,
  );
  assert.match(
    personaCss,
    /\.ppe-skill-dropdown__loading\s*\{[\s\S]*display:\s*flex;/,
  );
});

test("skill dropdown options use structured professional rows", () => {
  assert.match(componentSource, /className=\{`ppe-skill-option \$\{/);
  assert.match(componentSource, /className="ppe-skill-option__check-ring"/);
  assert.match(componentSource, /className="ppe-skill-option__check-icon"/);
  assert.match(componentSource, /className="ppe-skill-option__plus-icon"/);
  assert.match(
    personaCss,
    /\.ppe-skill-option\s*\{[\s\S]*min-height:\s*2\.75rem;/,
  );
  assert.match(
    personaCss,
    /\.ppe-skill-option--selected\s+\.ppe-skill-option__check-ring\s*\{[\s\S]*border-color:\s*var\(--theme-primary\);/,
  );
});

test("WeCom entry config (including notify targets) is admin-only and gated on an existing preset", () => {
  assert.match(
    componentSource,
    /showWeComSection\s*=\s*editorScope === "global" && canManageChannels && !!editingPreset;/,
  );
  assert.match(componentSource, /personaPresets\.wecom\.notifyTargets/);
  assert.match(
    componentSource,
    /getWeComNotifyTargets\(\s*editingPreset\.id,?\s*\)/,
  );
  assert.match(
    componentSource,
    /updateWeComNotifyTargets\(\s*editingPreset\.id,\s*notifyTargets\.map\(/,
  );
});
