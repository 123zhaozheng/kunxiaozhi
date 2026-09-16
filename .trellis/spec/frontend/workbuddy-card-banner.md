# WorkBuddy Card Banner Ban

> Cards must not render gradient banners — including via CSS pseudo-elements.

---

## Invariant

WorkBuddy UI redesign removed the 48px gradient "rainbow header" from all cards
(expert plaza, skills, teams, persona picker, skeletons, modals):

- No component renders a banner div (`linear-gradient` inline background, `h-12`).
- No CSS layout hook for banners (`.scb__banner`, `.pps-card__banner`, `.mp-card__banner` are deleted).
- No pseudo-element painted strip (`.team-card::before` deleted — it survived the component
  migration because it lives in `team.css`, not in JSX).
- Interaction controls that used to sit on banners (pin/star buttons, "使用中"/active pills)
  move into the card body title row, right-aligned; functionality must survive migration.
- Skeletons drop the banner placeholder row and the compensating `-mt-3` negative margin.

## Forbidden pattern: masking instead of deleting

Never hide a component-owned inline gradient with a CSS `!important` background override.
History: the first migration pass blanked banners via CSS, leaving a 48px empty strip with
floating controls; removing that rule brought the rainbow headers back. The only correct fix
is deleting the banner element from the component and relocating its controls.

## Regression test

`frontend/src/components/team/__tests__/cardBannerMigration.test.ts` asserts on component
sources (no `linear-gradient`, no `__banner`) and CSS files (no banner hooks, no
`.team-card::before`). Extend it when touching card chrome.

## Scope note

`--team-accent` itself stays: the sidebar color bar (`.team-color-bar::before`) and role
card left bar (`.team-role-card::before`) legitimately use it. Only the team card top strip
was banner remnant.
