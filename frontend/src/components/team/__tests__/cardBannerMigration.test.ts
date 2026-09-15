import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const migratedSources = [
  "../TeamBuilderWrapper.tsx",
  "../TeamPickerModal.tsx",
  "../../persona/PersonaPresetSelector.tsx",
  "../../skeletons/PanelSkeletons.tsx",
  "../../skeletons/PersonaSkeletons.tsx",
].map((path) => [path, readFileSync(new URL(path, import.meta.url), "utf8")]);

test("card components no longer render inline gradient banners", () => {
  for (const [path, source] of migratedSources) {
    assert.ok(
      !source.includes("linear-gradient"),
      `${path} still renders a gradient banner`,
    );
    assert.ok(
      !source.includes("__banner"),
      `${path} still references a banner class`,
    );
  }
});

test("banner layout-only CSS hooks are gone now that no card renders banners", () => {
  const cardBase = readFileSync(
    new URL("../../../styles/card-base.css", import.meta.url),
    "utf8",
  );
  const persona = readFileSync(
    new URL("../../../styles/persona.css", import.meta.url),
    "utf8",
  );
  assert.ok(!cardBase.includes(".scb__banner"));
  assert.ok(!persona.includes(".pps-card__banner"));
});

test("team card no longer paints a gradient strip via ::before", () => {
  const team = readFileSync(
    new URL("../../../styles/team.css", import.meta.url),
    "utf8",
  );
  assert.ok(!team.includes(".team-card::before"));
});

test("banner controls moved into the card title row keep working", () => {
  const teamBuilder = readFileSync(
    new URL("../TeamBuilderWrapper.tsx", import.meta.url),
    "utf8",
  );
  const selector = readFileSync(
    new URL("../../persona/PersonaPresetSelector.tsx", import.meta.url),
    "utf8",
  );
  // Pin/star buttons and status pills must survive the migration.
  assert.match(teamBuilder, /pps-card__icon-action--active-pin/);
  assert.match(teamBuilder, /scb__status-pill--installed/);
  assert.match(selector, /pps-card__icon-action--active-pin/);
  assert.match(selector, /scb__status-pill--installed/);
});
