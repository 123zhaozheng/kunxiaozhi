# Localize Password Setup UI

## Goal

Ensure password guidance and the first-login password-change page render fully in the selected language, with complete Chinese copy when the application language is Chinese.

## Background

The application locale is working: password and confirmation placeholders already render in Chinese. Four translation keys used by the password flows are missing from all five supported locale files, so i18next returns inline English defaults. This affects the first-login title, subtitle, submit button, and client-side password-policy guidance used by registration, reset, profile, and administrator user forms.

## Requirements

- Add `auth.changePasswordRequired`, `auth.changePasswordRequiredHint`, `auth.changePassword`, and `auth.validation.passwordPolicy` to every supported locale: English, Chinese, Japanese, Korean, and Russian.
- Use one canonical policy meaning across all consumers: 12-64 characters and at least three of uppercase, lowercase, digit, and symbol character types.
- Use these Chinese values:
  - Title: `设置新密码`
  - Hint: `请设置一个高强度密码后继续。`
  - Submit button: `修改密码`
  - Policy guidance: `请使用 12-64 个字符，并至少包含大写字母、小写字母、数字或符号中的三种。`
- Preserve existing password placeholders, mismatch/loading messages, password rules, routing, and submission behavior.
- Add a focused localization regression test that parses all locale JSON resources and verifies the four keys are non-empty in every supported locale.
- Assert Chinese values do not resolve to the known English fallback strings.
- Keep the first-login component and other policy consumers on the shared i18n key; do not add new hardcoded localized copy.
- Provide a clearly visible logout action beside the password-change submit action so a restricted first-login user can leave the session without changing the password.
- The logout action must use the application's existing logout flow, clear authentication state consistently, and return the user to the login screen.
- Render password change as the primary action and logout as the secondary action in a responsive action row; narrow screens may stack the buttons without overlap.
- Use the existing `auth.logout` resource, which is already localized in every supported locale.
- The logout control must use `type="button"`, call the shared authentication logout flow, and navigate to `/auth/login` with history replacement.
- Explain the effective strong-password requirements before submission instead of showing only a generic length/character-type sentence.
- Provide the requirements through a compact question-mark help control beside every new-password field rather than an always-visible block.
- Clicking or keyboard-activating the help control opens a localized requirements popover; activating it again, clicking outside, or pressing Escape closes it.
- The help control and popover must be accessible, remain within the viewport, and not overlap the password input or adjacent form content on narrow screens.
- Distinguish deterministic client-checkable rules from backend strength guidance. Do not claim that any specific substring such as `123` is always forbidden when the backend evaluates overall password strength.
- Give localized examples of weak patterns to avoid, including common numeric/alphabetic sequences, repeated characters, common passwords, and account-identifying information.
- Keep backend validation authoritative and map backend-only weak/identifier rejection to actionable localized guidance.

## Acceptance Criteria

- [ ] Chinese locale shows `设置新密码` as the first-login password page title.
- [ ] Chinese locale shows `请设置一个高强度密码后继续。` as the subtitle.
- [ ] Chinese locale shows the agreed Chinese policy guidance in first-login, registration, reset, profile, and administrator password validation paths that use the shared key.
- [ ] Chinese locale shows `修改密码` on the submit button.
- [ ] Password and confirmation placeholders remain Chinese.
- [ ] None of the reported English fallback strings is visible in Chinese locale.
- [ ] English, Japanese, Korean, and Russian resources contain explicit non-empty values for the same keys.
- [ ] Password behavior and policy logic are unchanged.
- [ ] The first-login password page shows a localized logout action beside the password-change action.
- [ ] Selecting logout clears the restricted session and returns to the login page without requiring a password change.
- [ ] Logout does not submit the password form or trigger password validation.
- [ ] Logout broadcasts the existing cross-tab logout event through the shared authentication flow.
- [ ] Primary and secondary actions remain readable and non-overlapping on narrow and desktop layouts.
- [ ] Password forms show a concise localized requirements list covering length, byte/whitespace/control constraints, character classes, account identifiers, current-password reuse where applicable, and overall strength.
- [ ] Registration, reset, forced first-login, profile password change, and administrator new-password fields expose the same reusable question-mark help control.
- [ ] The help content follows the active application language and all help keys are non-empty in English, Chinese, Japanese, Korean, and Russian.
- [ ] Mouse, touch, Enter, and Space can open the help; outside click and Escape close it; focus behavior and ARIA relationships are valid.
- [ ] Guidance states that common sequences such as `123`/`abc` and repeated/common patterns should be avoided, without presenting them as a simplistic substring ban.
- [ ] Client-checkable requirements remain validated before API submission while backend-only strength decisions remain authoritative.
- [ ] Backend weak-password and account-identifier errors resolve to actionable localized messages rather than the same vague generic sentence where the current error contract permits distinction.
- [ ] Focused localization tests, frontend lint, and TypeScript checks pass.

## Out of Scope

- Changing password-policy rules or password submission behavior.
- Redesigning the password-change page.
- Rewriting unrelated authentication translations.

## Technical Notes

- Root-cause evidence and the locale coverage matrix are recorded in `research/password-setup-i18n.md`.
- The missing keys originated with the forced-password-change feature; the later administrator password fix exposed the same shared policy-key gap.
- Logout flow, navigation behavior, locale coverage, and UI/test anchors are recorded in `research/forced-password-logout.md`.
- The shared logout function clears authentication state but does not navigate; the page-level handler owns the replace navigation to `/auth/login`.
- Exact backend/client password-policy behavior, `123` counterexamples, consumer coverage, and reusable UX options are recorded in `research/password-policy-guidance.md`.
- `123` is not a literal denylisted substring. Predictable sequences, repeated patterns, and common passwords contribute to backend zxcvbn strength rejection based on the complete password and account context.
- Existing popover/help patterns and the five-consumer integration matrix are recorded in `research/password-help-popover.md`.
- The approved interaction is a reusable click-activated question-mark help control, not an always-visible requirements block.
