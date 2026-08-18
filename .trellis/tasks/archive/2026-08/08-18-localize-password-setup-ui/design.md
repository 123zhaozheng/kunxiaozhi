# Technical Design

## Scope

Extend the current password localization task with one reusable `PasswordRequirementsHelp` component and integrate it beside the primary new-password label in registration, reset, forced first login, profile password change, and administrator user forms. Login/current-password and confirmation fields do not render duplicate help.

## Component Contract

Create a named auth component with a small API:

```ts
interface PasswordRequirementsHelpProps {
  context?: "registration" | "reset" | "forced" | "profile" | "admin";
  className?: string;
}
```

The component owns its translation keys and interaction state. `context="profile"` may add the current-password reuse rule; other contexts show only applicable general guidance. `PasswordInput` remains unchanged because its right-side eye toggle is a separate ownership boundary.

## Content Contract

The localized popover presents a semantic list:

1. Use 12-64 Unicode characters and no more than 72 UTF-8 bytes.
2. Do not use leading/trailing whitespace or control characters.
3. Include at least three of uppercase, lowercase, digit, and symbol.
4. Do not include username or email fragments; do not reuse the current password where applicable.
5. Avoid common passwords, repeated characters, and predictable sequences such as `123` or `abc`; these patterns may make the whole password too weak.

The copy must not claim that the substring `123` is categorically forbidden. Backend validation and zxcvbn remain authoritative.

Known backend details for weak password, account identifiers, and current-password reuse should map to distinct actionable localized keys. Other policy details keep the safe generic fallback. This retains the existing API payload and exact-string translation boundary while improving feedback.

## Interaction and Accessibility

- Place a Lucide `CircleHelp` icon button in the new-password label row.
- Use a native button with `type="button"`, localized `aria-label`, `aria-expanded`, `aria-controls`, and `aria-haspopup="dialog"`.
- Use one non-modal panel with `role="dialog"`, a localized heading, `aria-labelledby`, and semantic list markup.
- Trigger click/touch/Enter/Space toggles the panel. Outside pointer, Escape, or repeat activation closes it; Escape returns focus to the trigger.
- Do not close merely because the password input receives focus.

## Positioning and Responsive Behavior

- On `sm` and wider viewports, render a fixed portaled panel positioned from the trigger, clamped to viewport margins, and placed above or below according to available space.
- On narrow viewports, render the panel in normal flow below the label so it cannot cover the password input or adjacent controls.
- Use one compact bordered surface with bounded width/height and scrolling. Do not create nested cards or alter form shells.

## Integration Boundaries

- `AuthPage`: render only in registration mode.
- `ResetPassword`: render beside the new-password label.
- `ForcedPasswordChange`: render beside the first password field without changing submit/logout behavior.
- `ProfilePasswordTab`: render beside new password, with profile context.
- `UsersPanel`: render beside the create/replacement password label without changing exact password value handling.

## Test Strategy

- Locale/source contract tests verify all new help and specific-error keys in en/zh/ja/ko/ru, exact Chinese meaning, every intended consumer, and exclusion from login/confirmation fields.
- Component source contracts verify button/dialog ARIA, `type="button"`, outside/Escape listeners, focus return, and responsive branches.
- Pure positioning tests cover left/right viewport clamping and above/below placement.
- Password-policy tests preserve strong passwords containing `123` and add deterministic byte/control/composition boundaries.
- Backend-error mapping tests distinguish weak, identifier, and current-password messages while retaining generic fallback.
- Manual browser verification is required at narrow and desktop widths because the repository has no DOM interaction harness.

## Risks and Rollback

The main risks are popover clipping/overlap and duplicated rules drifting across consumers. Centralizing content and interaction in one component prevents drift; pure positioning tests plus manual viewport checks reduce layout risk. The component/integrations and error mapping can be reverted independently without password-policy or API changes.
