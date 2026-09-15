import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../SkillsHubPanel.tsx", import.meta.url), "utf8");

test("skills hub header uses its own title, not the workspace hub title", () => {
  assert.match(source, /title=\{t\("skillsHub\.title"\)\}/);
  assert.match(source, /subtitle=\{t\("skillsHub\.subtitle"\)\}/);
  // The workspace-level title belongs to the outer tab list / route meta only.
  assert.ok(
    !/PanelHeader[\s\S]{0,400}workspaceHub\.title/.test(source),
    "PanelHeader must not reuse workspaceHub.title",
  );
});

test("every supported locale translates the skills hub header", () => {
  for (const locale of ["zh", "en", "ja", "ko", "ru"]) {
    const messages = JSON.parse(
      readFileSync(
        new URL(`../../../i18n/locales/${locale}.json`, import.meta.url),
        "utf8",
      ),
    ) as { skillsHub?: { title?: unknown; subtitle?: unknown } };

    assert.equal(
      typeof messages.skillsHub?.title,
      "string",
      `${locale} is missing skillsHub.title`,
    );
    assert.equal(
      typeof messages.skillsHub?.subtitle,
      "string",
      `${locale} is missing skillsHub.subtitle`,
    );
  }
});
