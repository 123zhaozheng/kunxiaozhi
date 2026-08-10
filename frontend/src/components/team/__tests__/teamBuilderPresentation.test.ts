import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const wrapperSource = readFileSync(
  new URL("../TeamBuilderWrapper.tsx", import.meta.url),
  "utf8",
);
const builderSource = readFileSync(
  new URL("../TeamBuilder.tsx", import.meta.url),
  "utf8",
);
const memberCardSource = readFileSync(
  new URL("../TeamMemberCard.tsx", import.meta.url),
  "utf8",
);
const teamCssUrl = new URL("../../../styles/team.css", import.meta.url);
const teamCss = existsSync(teamCssUrl) ? readFileSync(teamCssUrl, "utf8") : "";

function cssBlock(selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return teamCss.match(new RegExp(`${escaped}\\s*\\{(?<body>[^}]*)\\}`))?.groups
    ?.body;
}

function assertCssDeclaration(
  selector: string,
  property: string,
  value: string,
) {
  assert.match(
    cssBlock(selector) ?? "",
    new RegExp(`${property}:\\s*${value};`),
    `${selector} should declare ${property}: ${value}`,
  );
}

test("team selected member cards fill the team member picker width", () => {
  assertCssDeclaration(".team-form-selected__list", "width", "100%");
  assert.match(
    teamCss,
    /\.team-form-selected__list \.list-item-card\s*\{[\s\S]*?width:\s*100%;[\s\S]*?max-width:\s*none;/,
  );
});

test("team toggle keeps the desktop switch dimensions", () => {
  assertCssDeclaration(".team-toggle", "width", "36px");
  assertCssDeclaration(".team-toggle", "height", "20px");
  assertCssDeclaration(".team-toggle", "min-height", "20px");
  assertCssDeclaration(".team-toggle", "min-width", "36px");
  assertCssDeclaration(".team-toggle::after", "width", "16px");
  assertCssDeclaration(".team-toggle::after", "height", "16px");
  assertCssDeclaration(
    ".team-toggle--on::after",
    "transform",
    "translateX\\(16px\\)",
  );
});

test("team builder list adopts shared panel and role-library presentation", () => {
  assert.match(wrapperSource, /<PanelHeader/);
  assert.match(wrapperSource, /<EditorSidebar/);
  assert.match(wrapperSource, /editorOpen/);
  assert.match(wrapperSource, /widthStorageKey="team-editor-sidebar-width"/);
  assert.match(wrapperSource, /skill-theme-shell flex h-full min-h-0 flex-col/);
  assert.match(wrapperSource, /skill-content-area flex-1 overflow-y-auto/);
  assert.match(wrapperSource, /TEAM_PAGE_SIZE/);
  assert.match(wrapperSource, /loadMoreRef/);
  assert.match(wrapperSource, /IntersectionObserver/);
  assert.match(wrapperSource, /className="team-card/);
  assert.match(wrapperSource, /TeamAvatar/);
  assert.match(wrapperSource, /getTeamFallbackAvatar/);
});

test("team editor uses one sidebar form matching role editor patterns", () => {
  assert.match(builderSource, /className="es-form team-editor-form"/);
  assert.match(builderSource, /es-section team-form-identity/);
  assert.match(builderSource, /team-member-builder/);
  assert.match(builderSource, /team-form-role-list/);
  assert.match(builderSource, /team-form-selected/);
  assert.match(builderSource, /team-editor-form__footer/);
  assert.match(builderSource, /team-editor-validation/);
  assert.match(builderSource, /team-editor-save-hint/);
  assert.doesNotMatch(builderSource, /activeMobilePane/);
  assert.doesNotMatch(builderSource, /team-builder-mobile-switch/);
  assert.doesNotMatch(builderSource, /data-mobile-pane/);
  assert.doesNotMatch(builderSource, /team-editor-progress/);
  assert.match(memberCardSource, /list-item-card/);
  assert.match(memberCardSource, /team-member-card__avatar-btn/);
  assert.match(builderSource, /teamAvatar/);
  assert.match(builderSource, /team-avatar-picker/);
  assert.match(builderSource, /persona-avatars/);
  assert.match(teamCss, /\.team-editor-form\s*\{/);
  assert.match(teamCss, /\.team-form-role-option\s*\{/);
  assert.match(teamCss, /\.team-form-selected\s*\{/);
  assert.match(
    teamCss,
    /\.team-form-selected__list \.list-item-card\s*\{[\s\S]*?max-width:\s*none;/,
  );
  assert.match(teamCss, /\.team-editor-validation\s*\{/);
});

test("team member candidates are restricted to Search personas", () => {
  assert.match(
    builderSource,
    /preset\.preferred_agent_id === "search"/,
  );
  assert.match(
    builderSource,
    /const searchPresets = presets\.filter\(/,
  );
  assert.match(builderSource, /return searchPresets;/);
  assert.match(builderSource, /return searchPresets\.filter\(/);
});

test("team editor defines dedicated tablet and mobile adaptations", () => {
  assert.match(teamCss, /@media \(max-width:\s*1180px\)/);
  assert.match(teamCss, /@media \(max-width:\s*760px\)/);
  assert.match(teamCss, /\.team-form-profile-grid/);
  assert.match(teamCss, /\.team-editor-form__actions/);
  assert.match(
    teamCss,
    /@media \(max-width:\s*760px\) \{[\s\S]*?\.team-member-builder__header,[\s\S]*?\.team-editor-form__footer\s*\{[\s\S]*?flex-direction:\s*column;/,
  );
  assert.match(
    teamCss,
    /@media \(max-width:\s*760px\) \{[\s\S]*?\.team-editor-form__actions\s*\{[\s\S]*?grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(7rem,\s*1fr\)\);/,
  );
  assert.match(
    teamCss,
    /@media \(max-width:\s*639px\) \{[\s\S]*?\.team-form-profile-grid\s*\{[\s\S]*?grid-template-columns:\s*1fr;/,
  );
});

test("team styles allow long scrolling lists and compact mobile cards", () => {
  assert.match(teamCss, /\.team-load-sentinel/);
  assert.match(teamCss, /\.team-avatar-picker/);
  assert.match(
    teamCss,
    /\.team-form-role-list\s*\{[\s\S]*?overflow-y:\s*auto;/,
  );
  assert.match(
    teamCss,
    /@media \(max-width:\s*639px\) \{[\s\S]*?\.team-form-role-option\s*\{[\s\S]*?align-items:\s*flex-start;/,
  );
  assert.match(
    teamCss,
    /@media \(max-width:\s*639px\) \{[\s\S]*?\.list-item-card__top\s*\{[\s\S]*?flex-wrap:\s*wrap;/,
  );
});

test("team avatar image containers constrain absolute avatar images", () => {
  for (const selector of [
    ".team-avatar",
    ".team-picker-avatar",
    ".team-toolbar-avatar",
  ]) {
    assertCssDeclaration(selector, "position", "relative");
    assertCssDeclaration(selector, "overflow", "hidden");
    assertCssDeclaration(selector, "flex-shrink", "0");
  }
  for (const selector of [".team-picker-avatar", ".team-toolbar-avatar"]) {
    assert.match(
      teamCss,
      new RegExp(
        `${selector.replace(
          ".",
          "\\.",
        )} \\.scb__avatar-img\\s*,|,\\s*${selector.replace(
          ".",
          "\\.",
        )} \\.scb__avatar-img`,
      ),
      `${selector} avatar images should receive explicit image sizing rules`,
    );
  }
  assertCssDeclaration(".team-picker-avatar", "width", "2rem");
  assertCssDeclaration(".team-picker-avatar", "height", "2rem");
  assertCssDeclaration(".team-toolbar-avatar", "width", "1\\.125rem");
  assertCssDeclaration(".team-toolbar-avatar", "height", "1\\.125rem");
});
