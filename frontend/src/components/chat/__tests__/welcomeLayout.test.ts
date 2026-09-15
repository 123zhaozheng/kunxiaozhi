import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  getRandomWelcomePersonaCards,
  getSelectedPersonaStarterPrompts,
  getSelectedTeamStarterPrompts,
  getWelcomePersonaCards,
  getWelcomePersonaCardClass,
  getWelcomePersonaSkeletonCount,
  getWelcomeSuggestionsContainerClass,
  getWelcomeSuggestionButtonClass,
} from "../welcomeLayout.ts";

const currentDir = dirname(fileURLToPath(import.meta.url));
const welcomeCss = readFileSync(
  resolve(currentDir, "../../../styles/welcome.css"),
  "utf8",
);
const tokensCss = readFileSync(
  resolve(currentDir, "../../../styles/tokens.css"),
  "utf8",
);

test("uses a dedicated accessible text token for unselected mode pills", () => {
  assert.match(tokensCss, /--theme-mode-track-text:\s*#696972;/);
  assert.match(
    tokensCss,
    /\.dark\s*\{[\s\S]*--theme-mode-track-text:\s*#d6d3d1;/,
  );
  assert.match(welcomeCss, /color:\s*var\(--theme-mode-track-text\);/);
});

test("does not apply hover feedback to locked mode pills", () => {
  assert.match(
    welcomeCss,
    /\.welcome-mode-pill:not\(:disabled\):not\(\[aria-disabled="true"\]\):hover/,
  );
  assert.match(
    welcomeCss,
    /\.welcome-mode-pill\[aria-disabled="true"\]\s*\{[\s\S]*opacity:\s*0\.58;/,
  );
});

test("keeps every welcome persona card reachable on mobile", () => {
  const className = getWelcomePersonaCardClass(3);

  assert.equal(className.includes("welcome-persona-card"), true);
  assert.equal(className.includes("hidden sm:flex"), false);
});

test("keeps later starter prompt pills reachable on narrow screens", () => {
  const className = getWelcomeSuggestionButtonClass(2);

  assert.equal(className.includes("welcome-suggestion-pill"), true);
  assert.equal(className.includes("hidden sm:flex"), false);
});

test("caps welcome suggestion prompts to two rows with vertical scrolling", () => {
  assert.match(
    welcomeCss,
    /\.welcome-suggestions-grid-wrapper\s*\{[\s\S]*--welcome-suggestion-row-height: 2\.5rem;[\s\S]*max-height: calc\(\s*var\(--welcome-suggestion-row-height\) \* 2 \+ var\(--welcome-suggestion-row-gap\)\s*\);[\s\S]*overflow-y: auto;/,
  );
});

test("caps welcome persona choices to two rows with vertical scrolling", () => {
  assert.match(
    welcomeCss,
    /@media \(min-width: 640px\) \{[\s\S]*\.welcome-persona-gallery\s*\{[\s\S]*--welcome-persona-card-height: 6rem;[\s\S]*max-height: calc\(\s*var\(--welcome-persona-card-height\) \* 2 \+ var\(--welcome-persona-row-gap\)\s*\);[\s\S]*overflow-y: auto;/,
  );
});

test("keeps welcome content centered on mobile when suggestions are visible", () => {
  assert.match(
    welcomeCss,
    /\.welcome-root\s*\{[\s\S]*justify-content: safe center;/,
  );
  assert.doesNotMatch(
    welcomeCss,
    /@media \(max-width: 639px\) \{[\s\S]*\.welcome-root\s*\{[\s\S]*justify-content: flex-start;/,
  );
});

test("enlarges only the welcome composer textarea", () => {
  assert.match(
    welcomeCss,
    /\.welcome-input textarea\s*\{[\s\S]*min-height:\s*3rem;/,
  );
});

test("keeps mobile welcome cards readable with stable touch targets", () => {
  assert.match(
    welcomeCss,
    /@media \(max-width: 639px\) \{[\s\S]*\.welcome-persona-gallery\s*\{[\s\S]*--welcome-persona-card-height: 4\.75rem;/,
  );
  assert.match(
    welcomeCss,
    /@media \(max-width: 639px\) \{[\s\S]*\.welcome-suggestions-grid-wrapper\s*\{[\s\S]*max-height: min\(\s*38dvh,/,
  );
});

test("keeps starter prompt container narrower than persona gallery", () => {
  assert.match(
    getWelcomeSuggestionsContainerClass("prompts"),
    /sm:max-w-\[38rem\]/,
  );
  assert.match(
    getWelcomeSuggestionsContainerClass("personas"),
    /sm:max-w-\[44rem\]/,
  );
});

test("shows persona cards before a welcome persona is selected", () => {
  const cards = getWelcomePersonaCards(
    [
      { id: "writer", name: "Writer", starter_prompts: [] },
      { id: "coder", name: "Coder", starter_prompts: [] },
      { id: "planner", name: "Planner", starter_prompts: [] },
    ],
    null,
    2,
  );

  assert.deepEqual(
    cards.map((card) => card.id),
    ["writer", "coder"],
  );
});

test("shows all welcome persona cards with pinned and favorite cards first", () => {
  const cards = getWelcomePersonaCards(
    [
      { id: "normal", name: "Normal", starter_prompts: [], usage_count: 10 },
      {
        id: "favorite",
        name: "Favorite",
        starter_prompts: [],
        is_favorite: true,
      },
      {
        id: "pinned",
        name: "Pinned",
        starter_prompts: [],
        is_pinned: true,
      },
    ],
    null,
  );

  assert.deepEqual(
    cards.map((card) => card.id),
    ["pinned", "favorite", "normal"],
  );
});

test("picks four random persona recommendations and rerolls to a different set", () => {
  const personas = Array.from({ length: 6 }, (_, index) => ({
    id: `persona-${index}`,
    name: `Persona ${index}`,
    starter_prompts: [],
  }));
  const first = getRandomWelcomePersonaCards(personas);
  const second = getRandomWelcomePersonaCards(
    personas,
    first.map((persona) => persona.id),
  );

  assert.equal(first.length, 4);
  assert.equal(new Set(first.map((persona) => persona.id)).size, 4);
  assert.equal(second.length, 4);
  assert.equal(
    second.some((persona) => !first.some((item) => item.id === persona.id)),
    true,
  );
});

test("returns every available persona when fewer than four exist", () => {
  const personas = [
    { id: "writer", name: "Writer", starter_prompts: [] },
    { id: "coder", name: "Coder", starter_prompts: [] },
  ];

  assert.deepEqual(
    getRandomWelcomePersonaCards(personas).map((persona) => persona.id).sort(),
    ["coder", "writer"],
  );
});

test("shows welcome choice skeletons only while the first page is loading", () => {
  assert.equal(getWelcomePersonaSkeletonCount(true, 0), 4);
  assert.equal(getWelcomePersonaSkeletonCount(true, 2), 0);
  assert.equal(getWelcomePersonaSkeletonCount(false, 0), 0);
});

test("uses only the selected persona starter prompts after a welcome persona is selected", () => {
  const prompts = getSelectedPersonaStarterPrompts(
    [
      {
        id: "writer",
        name: "Writer",
        starter_prompts: [{ icon: "✍️", text: "写一段开场白" }],
      },
      {
        id: "coder",
        name: "Coder",
        starter_prompts: [
          { text: { zh: "帮我审查这段代码", en: "Review this code" } },
        ],
      },
    ],
    "coder",
    "zh-CN",
  );

  assert.deepEqual(prompts, [{ icon: null, text: "帮我审查这段代码" }]);
});

test("falls back to default suggestions when selected persona has no starter prompts", () => {
  const prompts = getSelectedPersonaStarterPrompts(
    [{ id: "coder", name: "Coder", starter_prompts: [] }],
    "coder",
    "zh-CN",
    [{ icon: "🐍", text: "创建一个 Python 脚本" }],
  );

  assert.deepEqual(prompts, [{ icon: "🐍", text: "创建一个 Python 脚本" }]);
});

test("uses selected team starter prompts before default suggestions", () => {
  const prompts = getSelectedTeamStarterPrompts(
    [
      {
        id: "team-1",
        name: "Research Team",
        starter_prompts: [
          {
            icon: "🧭",
            text: { zh: "组织一次研究评审", en: "Review research" },
          },
        ],
      },
    ],
    "team-1",
    "zh-CN",
    [{ icon: "✨", text: "默认建议" }],
  );

  assert.deepEqual(prompts, [{ icon: "🧭", text: "组织一次研究评审" }]);
});
