# Research: Password requirements help popover

- Query: Find reusable click-activated question-mark help/popover patterns and define the smallest accessible integration for the five password consumers.
- Scope: internal
- Date: 2026-08-18

## Findings

### Existing reusable patterns

- `frontend/src/components/common/Tooltip.tsx:13-21,28-34` is the existing portal tooltip API (`content`, `placement`, `children`, optional class/z-index). It uses a `display: contents` wrapper and portals a fixed bubble to `document.body` (`:166-191`), chooses top/bottom from available vertical space (`:129-160`), and closes on outside `mousedown` (`:107-118`). It is hover/long-press oriented (`:48-89`), `pointer-events-none`, only accepts string/number content (`:162`), and has no Escape or `aria-expanded`/`aria-controls`; it should not be used as the requirements popover.
- `frontend/src/components/chat/ChatInputHelpMenu.tsx:69-129` is the closest visual/icon precedent: it imports Lucide `CircleHelp` (`:3`), toggles from a real `type="button"` with localized `aria-label` and `aria-expanded` (`:94-103`), closes on outside `mousedown` (`:75-83`), and portals a responsive menu to `document.body` (`:87-109`). It does not close on Escape and its fixed bottom-right placement is not suitable beside a password field.
- `frontend/src/components/common/LanguageToggle.tsx:33-54,56-76` supplies the project pattern for outside click plus Escape, and `aria-expanded`/`aria-haspopup` on the trigger. `frontend/src/components/panels/AgentPanel/shared/RoleSelector.tsx:25-38,43-67` shows the simpler local relative dropdown pattern but has no Escape handling.
- `frontend/src/components/common/GlassSelect.tsx:38-75,93-122` is the strongest viewport-positioning precedent: it treats both trigger and portaled dropdown as inside for outside-click checks (`:40-53`), measures the trigger in `useLayoutEffect`, clamps horizontal position to a 16px viewport margin (`:55-62`), chooses above/below from available space (`:63-65`), and portals a fixed element at high z-index (`:67-74,93-122`). Add Escape handling and an anchor-focus return for the password use case.
- Lucide is already a dependency (`frontend/package.json`, `lucide-react ^0.468.0`) and `CircleHelp` is already used for a help control. Do not draw a custom SVG/question mark.

### Recommended component contract

Create one named auth component, e.g. `frontend/src/components/auth/PasswordRequirementsHelp.tsx`, consumed by auth, profile, and admin forms. Keep `PasswordInput` unchanged: it owns the lock icon and eye button (`frontend/src/components/auth/PasswordInput.tsx:29-49`), and adding a help button inside its right padding would collide with the eye toggle. A minimal API is:

```ts
interface PasswordRequirementsHelpProps {
  className?: string;
  context?: "registration" | "reset" | "forced" | "profile" | "admin";
}
```

`context` is optional; use it only to add contextual guidance such as current-password reuse for profile. The component should own the fixed translation-key list (title, deterministic rules, backend-authoritative guidance/examples), call `useTranslation`, and avoid accepting already-localized copy from each consumer. Every new help key must be non-empty in `en`, `zh`, `ja`, `ko`, and `ru`, consistent with the existing all-locale JSON/source contract in `frontend/src/i18n/__tests__/passwordSetupKeys.test.ts:30-47,93-110`.

### Placement and responsive behavior

- Put the trigger in the label row immediately beside the new-password label, not inside the input. Use a `relative flex items-center gap-1` label row; keep the question-mark button at least 32px square, `type="button"`, and use `CircleHelp` with `aria-hidden` so the localized button label is the accessible name.
- Render one help control for each actual new-password entry point. Do not show it for the login mode of `AuthPage` (`autoComplete="current-password"`) or duplicate it on confirmation fields; the confirmation field has no independent policy. Registration should conditionally render it only when `mode === "register"`.
- Prefer a portal to `document.body` plus fixed positioning, borrowing `GlassSelect`'s anchor measurement/clamping. Use a bounded width such as `min(20rem, calc(100vw - 24px))`, 12px horizontal viewport margins, `max-h` with `overflow-auto`, and choose above when there is room, otherwise below. Recalculate on open and close on resize/scroll; this prevents clipping by `auth-panel`, profile, or admin modal overflow.
- To satisfy the narrow-screen no-overlap requirement, the safest implementation is to render the panel in normal flow below the label row below the `sm` breakpoint (or reserve equivalent block space) and use the fixed/absolute portaled placement only at `sm` and above. A fixed panel that simply falls below a label can visually cover the password input; this is a residual risk to verify in mobile screenshots if the implementer chooses a fully overlaid variant.
- Keep the panel a single compact surface (border/background/shadow, not nested cards) and use the existing theme/Tailwind variables. Do not alter the five form shells or make the always-visible guidance block return.

### Accessibility and interaction contract

- Trigger: native `<button>` for mouse, touch, Enter, and Space; `aria-label={t("auth.passwordRequirements.open")}` (or equivalent), `aria-expanded={open}`, and `aria-controls={popoverId}`. Add `aria-haspopup="dialog"` if using `role="dialog"`.
- Panel: `role="dialog"`, a localized heading with `id={popoverTitleId}`, and `aria-labelledby={popoverTitleId}`. It is non-modal, so do not set `aria-modal`; the password input remains usable while the panel is open. Use a scrollable list with semantic `<ul>/<li>` rather than text-only line breaks.
- Open/close: click toggles; a second activation closes; document `pointerdown`/`mousedown` closes only when the target is outside both trigger wrapper and panel; document `keydown` closes on Escape. Keep focus on the trigger on open, and return focus to it on Escape/toggle close. Do not close on input focus changes.
- Deterministic checks (length, 72-byte limit, edge whitespace/control, three-of-four classes) may have status styling only when the caller passes the current password/value and the shared helper actually checks that rule. Identifier fragments, current-password reuse, and zxcvbn/overall strength must be presented as guidance/server-authoritative, never as a green client-passed state. Existing backend mapping currently collapses policy errors in `frontend/src/utils/backendErrors.ts:121-133`; preserve backend authority.

### Five-consumer integration matrix

| Flow | Source field | Recommended insertion | Notes |
| --- | --- | --- | --- |
| Registration | `frontend/src/components/auth/AuthPage.tsx:586-600` | Add the help control to the password label row only when `mode === "register"`; keep `PasswordInput` and `autoComplete` unchanged. | Login uses the same field but is current-password and should not expose new-password guidance. Confirmation is `:603-617` and should not duplicate the popover. |
| Reset link | `frontend/src/components/auth/ResetPassword.tsx:192-203` | Add beside `auth.newPassword`; leave confirmation at `:205-217` unchanged. | Existing toast/policy behavior at `:46-52` remains the validation path. |
| Forced first login | `frontend/src/components/auth/ForcedPasswordChange.tsx:48-60` | Add beside the first `PasswordInput`; keep confirmation separate. | This component already has logout and action-row behavior at `:62-80`; help must not change submit/logout semantics. |
| Profile change | `frontend/src/components/profile/tabs/ProfilePasswordTab.tsx:107-119` | Add beside `profile.newPassword`; keep the old-password field (`:84-105`) and confirmation (`:121-133`) untouched. | Pass `context="profile"` only if showing current-password-reuse guidance. |
| Administrator user form | `frontend/src/components/panels/UsersPanel.tsx:259-280` | Add beside `users.password` in the label row; it applies to create and optional password replacement on edit. | Preserve native input, leading Lock icon, and `es-input--with-leading-icon`; do not put the help control in the input's left/right adornment. |

### Test targets

- Add a static/source contract for the new component or extend `frontend/src/i18n/__tests__/passwordSetupKeys.test.ts`: all help keys are non-empty in five locales; each consumer imports/renders the shared component; the login branch does not render it; trigger has `type="button"`, `aria-expanded`, and `aria-controls`; panel has `role="dialog"`; source includes outside pointer and Escape listeners.
- Keep `frontend/src/components/auth/__tests__/PasswordInput.test.tsx:6-18` focused on the eye toggle; do not make help behavior a `PasswordInput` concern.
- Add a pure positioning helper test modeled on `frontend/src/components/common/selectionActionPopover.ts:33-73` and `frontend/src/components/common/GlassSelect.tsx:55-75`: left/right clamping, above/below choice, and a narrow viewport margin. Existing frontend tests are Node/`tsx` source or `renderToStaticMarkup` tests; no Testing Library/jsdom harness was found, so click/outside/Escape should be source-contract tested unless a DOM harness is deliberately introduced.
- Run the focused `tsx --test` files, then frontend lint and TypeScript build. A screenshot/manual check at narrow and desktop widths is still needed to verify the no-overlap requirement.

## Files found

- `frontend/src/components/common/Tooltip.tsx` - portal tooltip, top/bottom placement, outside mousedown; missing click/Escape/ARIA contract for this use.
- `frontend/src/components/chat/ChatInputHelpMenu.tsx` - existing `CircleHelp` click control and portal menu.
- `frontend/src/components/common/LanguageToggle.tsx` - outside-click, Escape, `aria-expanded`, and `aria-haspopup` pattern.
- `frontend/src/components/common/GlassSelect.tsx` - fixed portal viewport clamping and above/below calculation.
- `frontend/src/components/auth/PasswordInput.tsx` - shared password input and eye-button boundary.
- `frontend/src/components/auth/AuthPage.tsx`, `ResetPassword.tsx`, `ForcedPasswordChange.tsx` - auth consumers and field anchors.
- `frontend/src/components/profile/tabs/ProfilePasswordTab.tsx` - ordinary profile new-password field.
- `frontend/src/components/panels/UsersPanel.tsx` - admin create/edit password field and native input layout.
- `frontend/src/components/auth/__tests__/PasswordInput.test.tsx`, `frontend/src/components/auth/__tests__/authResponsiveLayout.test.ts`, `frontend/src/components/panels/__tests__/usersPanelFormLayout.test.ts`, `frontend/src/i18n/__tests__/passwordSetupKeys.test.ts` - current static/source test conventions.
- `.trellis/tasks/08-18-localize-password-setup-ui/research/password-policy-guidance.md` - authoritative policy, consumer matrix, backend-only strength/identifier caveats, and guidance content.

## Caveats / Not Found

- No existing reusable password-requirements component or popover with complete click + outside + Escape + focus-return behavior was found.
- `Tooltip`'s horizontal position is not clamped and it cannot host interactive/list content; `ChatInputHelpMenu` lacks Escape handling. Both are precedents, not drop-in implementations.
- The repository has no DOM interaction test harness in the inspected frontend dependencies. Static contracts plus pure positioning tests will not prove real pointer/focus behavior; manual/browser verification remains necessary.
- A fixed overlay that opens below a label can cover the input on a narrow viewport. Use an inline-flow mobile fallback or explicitly reserve space, then verify at 320-390px widths.
- The requirement says "every new-password field" while confirmation fields are also `autoComplete="new-password"`; this recommendation interprets it as every primary new-password entry point and deliberately avoids duplicated guidance on confirmation fields. If product means literal autocomplete coverage, add the same component to confirmations and accept the additional vertical density.
